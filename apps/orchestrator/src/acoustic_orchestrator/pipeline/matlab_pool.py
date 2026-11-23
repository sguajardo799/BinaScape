"""One MATLAB session per render run, rendering attempts on a local parallel pool.

With ``execution.matlab_runner: pool`` every RAVEN attempt is a job served by a
single ``matlab -batch`` process (one license checkout) running
``run_raven_pool_server``. The session and the orchestrator talk through files
under ``metadata/matlab_pool/<session_id>/``:

- ``requests/<attempt_id>.json``: written here (atomic rename).
- ``results/<attempt_id>.json``: written by MATLAB with the job's exit code.
- ``ready.json`` / ``heartbeat.json``: written by MATLAB.
- ``stop``: written here to shut the session down.
- ``console.log`` / ``invocation.json``: session diagnostics written here.

A session that exits, never becomes ready, or stops refreshing its heartbeat
fails every job waiting on it with :class:`PoolSessionFailure`; the runner turns
that into an observed non-zero process exit so retries and the batch circuit
apply unchanged. The next dispatch starts a new session.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import uuid4

from acoustic_orchestrator.config.models import MatlabPoolConfig
from acoustic_orchestrator.pipeline.matlab_diagnostics import diagnostic_warning

SERVER_ENTRYPOINT = "run_raven_pool_server"
STOP_GRACE_S = 120.0
RESULT_READ_TIMEOUT_S = 10.0
RESULT_READ_RETRY_S = 0.05
Launch = Callable[[list[str], Path, dict[str, str], BinaryIO], Any]


class PoolSessionFailure(Exception):
    """The session ended before publishing the job's result."""

    def __init__(self, reason: str, exit_code: int, session_id: str, console_path: Path) -> None:
        super().__init__(f"MATLAB pool session {session_id} failed ({reason}, exit {exit_code})")
        self.reason = reason
        self.exit_code = exit_code
        self.session_id = session_id
        self.console_path = console_path

    def as_record(self) -> dict[str, Any]:
        return {"reason": self.reason, "session_id": self.session_id, "exit_code": self.exit_code,
                "console_path": self.console_path.as_posix()}


@dataclass
class _SessionState:
    session_id: str
    directory: Path
    process: Any
    console: BinaryIO
    prefdir: Path
    invocation: dict[str, Any]
    ready: bool = False
    last_beat: float = 0.0
    beat_mtime: float | None = None
    kill_reason: str | None = None
    retired: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def console_path(self) -> Path:
        return self.directory / "console.log"


class MatlabPoolSession:
    """Lazily started, restartable MATLAB pool session shared by render threads."""

    def __init__(self, root: Path, *, pool_size: int, timeouts: MatlabPoolConfig,
                 matlab_executable: str = "matlab", app_dir: Path | None = None,
                 launch: Launch | None = None, poll_s: float = 0.2,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if pool_size < 1:
            raise ValueError("pool_size must be at least 1")
        self.root = root
        self.pool_size = pool_size
        self.timeouts = timeouts
        self.matlab_executable = matlab_executable
        self.app_dir = app_dir
        self._launch = launch or _launch_subprocess
        self.poll_s = poll_s
        self.clock = clock
        self._lock = threading.Lock()
        self._state: _SessionState | None = None
        self._generation = 0
        self._startup_failure: PoolSessionFailure | None = None
        self.last_session_id: str | None = None

    # -- public -------------------------------------------------------------
    def run_job(self, attempt_id: str, request: dict[str, Any]) -> tuple[int, str | None, str]:
        """Submit one job and wait for it; returns (exit_code, worker_id, session_id).

        Raises :class:`PoolSessionFailure` when the session dies or never becomes
        ready, and ``OSError`` when the request cannot be published.
        """
        state = self._acquire()
        result_path = state.directory / "results" / f"{attempt_id}.json"
        _write_json_atomic(state.directory / "requests" / f"{attempt_id}.json", request)
        while True:
            if result_path.exists():
                result = _read_result(result_path, attempt_id)
                return result["exit_code"], result.get("worker_id"), state.session_id
            code = state.process.poll()
            if code is not None:
                if result_path.exists():
                    continue  # Published just before exiting.
                reason = state.kill_reason or "session_exited"
                raise PoolSessionFailure(reason, code if code != 0 else 1, state.session_id, state.console_path)
            if self._heartbeat_stale(state):
                self._kill(state, "heartbeat_stale")
                raise PoolSessionFailure("heartbeat_stale", 1, state.session_id, state.console_path)
            time.sleep(self.poll_s)

    def close(self) -> None:
        """Stop the session (graceful, then forced) and record its outcome."""
        with self._lock:
            state = self._state
            self._state = None
            if state is None:
                return
            if state.process.poll() is None:
                try:
                    (state.directory / "stop").write_bytes(b"")
                    state.process.wait(timeout=STOP_GRACE_S)
                except Exception:
                    self._kill(state, "stop_timeout")
            self._retire(state)

    # -- session lifecycle --------------------------------------------------
    def _acquire(self) -> _SessionState:
        entry_generation = self._generation
        with self._lock:
            state = self._state
            if state is not None and state.ready and state.process.poll() is None and state.kill_reason is None:
                return state
            if self._generation != entry_generation and self._startup_failure is not None:
                # This attempt waited on a startup that failed: it fails with it.
                raise self._startup_failure
            if state is not None:
                self._retire(state)
                self._state = None
            self._generation += 1
            self._startup_failure = None
            state = self._start()
            self._state = state
            try:
                self._wait_ready(state)
            except PoolSessionFailure as failure:
                self._startup_failure = failure
                self._retire(state)
                self._state = None
                raise
            return state

    def _start(self) -> _SessionState:
        # Imported here: matlab_runner imports this module.
        from acoustic_orchestrator.pipeline.matlab_runner import MATLAB_APP_DIR, isolated_matlab_env

        app_dir = (self.app_dir or MATLAB_APP_DIR).resolve()
        session_id = uuid4().hex
        directory = self.root / session_id
        (directory / "requests").mkdir(parents=True)
        (directory / "results").mkdir()
        server_call = (f"addpath(genpath('{_matlab_path(app_dir)}')); "
                       f"{SERVER_ENTRYPOINT}('{_matlab_path(directory)}', {self.pool_size}, {os.getpid()});")
        command = [self.matlab_executable, "-batch", server_call]
        env, prefdir = isolated_matlab_env()
        console = (directory / "console.log").open("wb")
        invocation = {
            "schema_version": 1, "session_id": session_id, "command": command, "pool_size": self.pool_size,
            "matlab_prefdir": prefdir.as_posix(), "started_at": _now(), "ready_at": None,
            "finished_at": None, "exit_code": None, "failure": None, "prefdir_cleanup": None,
        }
        state = _SessionState(session_id, directory, None, console, prefdir, invocation)
        self.last_session_id = session_id
        _save_invocation(state)
        try:
            state.process = self._launch(command, directory, env, console)
        except Exception:
            state.process = _NeverStarted()
            self._retire(state)
            raise
        return state

    def _wait_ready(self, state: _SessionState) -> None:
        deadline = self.clock() + self.timeouts.startup_timeout_s
        while True:
            if (state.directory / "ready.json").exists():
                state.ready = True
                state.last_beat = self.clock()
                state.invocation["ready_at"] = _now()
                _save_invocation(state)
                return
            code = state.process.poll()
            if code is not None:
                raise PoolSessionFailure("startup_exited", code if code != 0 else 1, state.session_id,
                                         state.console_path)
            if self.clock() > deadline:
                self._kill(state, "startup_timeout")
                raise PoolSessionFailure("startup_timeout", 1, state.session_id, state.console_path)
            time.sleep(self.poll_s)

    def _heartbeat_stale(self, state: _SessionState) -> bool:
        with state.lock:
            try:
                mtime = (state.directory / "heartbeat.json").stat().st_mtime
            except OSError:
                mtime = None
            if mtime is not None and mtime != state.beat_mtime:
                state.beat_mtime = mtime
                state.last_beat = self.clock()
            return self.clock() - state.last_beat > self.timeouts.heartbeat_timeout_s

    def _kill(self, state: _SessionState, reason: str) -> None:
        with state.lock:
            if state.kill_reason is None:
                state.kill_reason = reason
            if state.process.poll() is not None:
                return
            try:
                _kill_tree(state.process)
                state.process.wait(timeout=30)
            except Exception as exc:
                diagnostic_warning(f"could not stop MATLAB pool session {state.session_id}: {exc}")

    def _retire(self, state: _SessionState) -> None:
        """Release a session that has ended (or must end) and persist its invocation."""
        from acoustic_orchestrator.pipeline.matlab_runner import remove_matlab_prefdir

        if state.retired:
            return
        state.retired = True
        if state.process is not None and state.process.poll() is None:
            self._kill(state, state.kill_reason or "retired")
        try:
            state.console.close()
        except Exception:
            pass
        state.invocation.update(
            finished_at=_now(),
            exit_code=state.process.poll() if state.process is not None else None,
            failure=state.kill_reason,
            prefdir_cleanup=remove_matlab_prefdir(state.prefdir),
        )
        _save_invocation(state)


class _NeverStarted:
    """Process stand-in for a session whose launch raised."""
    pid = None

    def poll(self) -> int:
        return 1

    def wait(self, timeout: float | None = None) -> int:
        return 1

    def kill(self) -> None:
        pass


def _launch_subprocess(command: list[str], directory: Path, env: dict[str, str], console: BinaryIO):
    return subprocess.Popen(command, stdout=console, stderr=subprocess.STDOUT, env=env)


def _kill_tree(process: Any) -> None:
    # `matlab` on Windows is a launcher; the pool workers are grandchildren.
    if os.name == "nt" and isinstance(process, subprocess.Popen):
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if process.poll() is None:
            process.kill()
    else:
        process.kill()


def _read_result(path: Path, attempt_id: str) -> dict[str, Any]:
    # On Windows the file can be briefly locked (sharing violation) right after
    # MATLAB's movefile publishes it, e.g. by antivirus or the move itself.
    # A transient read failure must not turn a finished job into launch_failed.
    deadline = time.monotonic() + RESULT_READ_TIMEOUT_S
    while True:
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            break
        except (PermissionError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(RESULT_READ_RETRY_S)
    if (not isinstance(result, dict) or result.get("attempt_id") != attempt_id
            or type(result.get("exit_code")) is not int):
        raise RuntimeError(f"Resultado del pool MATLAB inválido para {attempt_id}: {path}")
    return result


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _save_invocation(state: _SessionState) -> None:
    try:
        _write_json_atomic(state.directory / "invocation.json", state.invocation)
    except Exception as exc:
        diagnostic_warning(f"could not save MATLAB pool invocation {state.session_id}: {exc}")


def _matlab_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def _now() -> str:
    return datetime.now(UTC).isoformat()
