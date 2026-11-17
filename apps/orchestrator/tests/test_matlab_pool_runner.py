"""Pool-mode MATLAB runner against a fake session that serves the file queue."""
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import pytest

from acoustic_orchestrator.config.models import MatlabPoolConfig
from acoustic_orchestrator.pipeline import matlab_pool
from acoustic_orchestrator.pipeline.matlab_pool import MatlabPoolSession
from acoustic_orchestrator.pipeline.matlab_runner import render_manifest_with_matlab_pool
from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt


class FakeSession:
    """Popen-like object whose 'process' is a thread speaking the pool protocol.

    ``behavior(request) -> int | str``: an exit code to publish, ``"hang"`` to
    never answer, or ``"die"`` to end the whole session with code 3.
    """

    def __init__(self, session_dir: Path, behavior: Callable[[dict], int | str], *,
                 ready: bool = True, startup_exit: int | None = None, heartbeat: bool = True) -> None:
        self.session_dir = session_dir
        self.behavior = behavior
        self.pid = 4242
        self.returncode: int | None = None
        self.killed = False
        self._ready = ready
        self._startup_exit = startup_exit
        self._heartbeat = heartbeat
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _write(self, path: Path, payload: dict) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)

    def _serve(self) -> None:
        if self._startup_exit is not None:
            time.sleep(0.05)
            self.returncode = self._startup_exit
            return
        if not self._ready:
            while not self.killed:
                time.sleep(0.01)
            self.returncode = 1
            return
        self._write(self.session_dir / "ready.json", {"schema_version": 1, "pool_size": 2, "pid": self.pid})
        seen: set[str] = set()
        while not self.killed:
            if self._heartbeat:
                self._write(self.session_dir / "heartbeat.json", {"schema_version": 1, "in_flight": 0})
            if (self.session_dir / "stop").exists():
                self.returncode = 0
                return
            for request_path in sorted((self.session_dir / "requests").glob("*.json")):
                attempt_id = request_path.stem
                if attempt_id in seen:
                    continue
                seen.add(attempt_id)
                request = json.loads(request_path.read_text(encoding="utf-8"))
                outcome = self.behavior(request)
                if outcome == "die":
                    self.returncode = 3
                    return
                if outcome == "hang":
                    continue
                Path(request["console_path"]).open("a", encoding="utf-8").write(f"job {attempt_id}\n")
                self._write(self.session_dir / "results" / f"{attempt_id}.json", {
                    "schema_version": 1, "attempt_id": attempt_id, "exit_code": outcome,
                    "error": None if outcome == 0 else {"identifier": "X:y", "message": "boom"},
                    "worker_id": "worker-7", "started_at": "s", "finished_at": "f"})
            time.sleep(0.01)
        self.returncode = 1

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self._thread.join(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self._thread.join(2)


def _manifest(tmp_path: Path, name: str = "scene__binaural_hrtf") -> Path:
    path = tmp_path / "runtime" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    scene = name.split("__")[0]
    path.write_text(json.dumps({
        "schema_version": "3.0", "scene_id": scene,
        "render": {"active_output_type": "binaural_hrtf",
                   "output_wav_path": (path.parent / f"{name}.wav").as_posix(),
                   "output_metadata_path": (path.parent / f"{name}.meta.json").as_posix()},
    }, indent=2), encoding="utf-8")
    return path


def _session(tmp_path: Path, behavior=lambda request: 0, *, timeouts: MatlabPoolConfig | None = None,
             launched: list | None = None, **fake) -> MatlabPoolSession:
    def launch(command, session_dir, env, console):
        process = FakeSession(session_dir, behavior, **fake)
        if launched is not None:
            launched.append((command, env, process))
        return process
    return MatlabPoolSession(tmp_path / "metadata" / "matlab_pool", pool_size=2,
                             timeouts=timeouts or MatlabPoolConfig(), launch=launch, poll_s=0.01)


def _render(tmp_path: Path, session: MatlabPoolSession, name: str = "scene__binaural_hrtf"):
    return render_manifest_with_matlab_pool(
        _manifest(tmp_path, name), session, diagnostics_dir=tmp_path / "diagnostics",
        attempts_dir=tmp_path / "attempts")


def _outcome(result) -> dict:
    return json.loads((result.attempt.directory / "outcome.json").read_text(encoding="utf-8"))


def test_successful_job_is_an_observed_zero_exit(tmp_path: Path) -> None:
    launched: list = []
    session = _session(tmp_path, launched=launched)
    try:
        result = _render(tmp_path, session)
    finally:
        session.close()
    assert (result.exit_code, result.process_status, result.termination_observed, result.error) == (
        0, "succeeded", True, None)
    outcome = _outcome(result)
    assert outcome["matlab_runner"] == "pool"
    assert outcome["pool_session_id"] == session.last_session_id
    assert outcome["pool_worker_id"] == "worker-7"
    assert outcome["command"][:2] == ["matlab_pool", "run_raven_static_render"]
    command, env, _ = launched[0]
    assert command[1] == "-batch" and "run_raven_pool_server" in command[2]
    assert Path(env["MATLAB_PREFDIR"]).name.startswith("binascape-matlab-prefs-")


def test_job_error_is_failed_called_process_error(tmp_path: Path) -> None:
    session = _session(tmp_path, lambda request: 1)
    try:
        result = _render(tmp_path, session)
    finally:
        session.close()
    assert result.process_status == "failed" and result.exit_code == 1 and result.termination_observed
    assert isinstance(result.error, subprocess.CalledProcessError)
    assert _outcome(result)["error"]["type"] == "CalledProcessError"


def test_console_of_job_lands_in_attempt_console(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        result = _render(tmp_path, session)
    finally:
        session.close()
    console = tmp_path / "diagnostics" / result.attempt.attempt_id / "console.log"
    assert console.read_text(encoding="utf-8") == f"job {result.attempt.attempt_id}\n"


def test_session_death_fails_in_flight_jobs_with_session_exit_code(tmp_path: Path) -> None:
    lock = threading.Lock()
    seen = {"n": 0}

    def behavior(request: dict) -> str:
        # First job stays in flight; the second one takes the whole session down.
        with lock:
            seen["n"] += 1
            return "hang" if seen["n"] == 1 else "die"
    session = _session(tmp_path, behavior)
    gate = threading.Barrier(2, timeout=5)
    results = []

    def run(name: str) -> None:
        gate.wait()
        results.append(_render(tmp_path, session, name))
    threads = [threading.Thread(target=run, args=(f"scene__v{k}",)) for k in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
    finally:
        session.close()
    assert len(results) == 2
    for result in results:
        assert (result.process_status, result.exit_code, result.termination_observed) == ("failed", 3, True)
        assert isinstance(result.error, subprocess.CalledProcessError)
        assert _outcome(result)["pool_session_failure"]["reason"] == "session_exited"


def test_next_dispatch_after_death_starts_new_session(tmp_path: Path) -> None:
    calls = {"n": 0}

    def behavior(request: dict) -> int | str:
        calls["n"] += 1
        return "die" if calls["n"] == 1 else 0
    launched: list = []
    session = _session(tmp_path, behavior, launched=launched)
    try:
        first = _render(tmp_path, session, "scene__a")
        second = _render(tmp_path, session, "scene__b")
    finally:
        session.close()
    assert first.process_status == "failed" and second.process_status == "succeeded"
    assert len(launched) == 2
    assert _outcome(first)["pool_session_id"] != _outcome(second)["pool_session_id"]


def test_startup_exit_fails_waiting_attempt_with_session_console(tmp_path: Path) -> None:
    def launch(command, session_dir, env, console):
        console.write(b"Unable to communicate with required MathWorks services (error 5001).\n")
        console.flush()
        return FakeSession(session_dir, lambda request: 0, startup_exit=1)
    session = MatlabPoolSession(tmp_path / "metadata" / "matlab_pool", pool_size=2,
                                timeouts=MatlabPoolConfig(), launch=launch, poll_s=0.01)
    try:
        result = _render(tmp_path, session)
    finally:
        session.close()
    assert (result.process_status, result.exit_code, result.termination_observed) == ("failed", 1, True)
    assert _outcome(result)["pool_session_failure"]["reason"] == "startup_exited"
    console = tmp_path / "diagnostics" / result.attempt.attempt_id / "console.log"
    assert "error 5001" in console.read_text(encoding="utf-8")


def test_startup_timeout_kills_session_and_fails_attempt(tmp_path: Path) -> None:
    launched: list = []
    session = _session(tmp_path, launched=launched, ready=False,
                       timeouts=MatlabPoolConfig(startup_timeout_s=0.2))
    try:
        result = _render(tmp_path, session)
    finally:
        session.close()
    assert result.process_status == "failed" and result.termination_observed
    assert _outcome(result)["pool_session_failure"]["reason"] == "startup_timeout"
    assert launched[0][2].killed


def test_stale_heartbeat_kills_session_and_fails_in_flight(tmp_path: Path) -> None:
    launched: list = []
    session = _session(tmp_path, lambda request: "hang", launched=launched, heartbeat=False,
                       timeouts=MatlabPoolConfig(heartbeat_timeout_s=0.3))
    try:
        result = _render(tmp_path, session)
    finally:
        session.close()
    assert result.process_status == "failed" and result.termination_observed
    assert _outcome(result)["pool_session_failure"]["reason"] == "heartbeat_stale"
    assert launched[0][2].killed


@pytest.mark.filterwarnings("ignore:MATLAB diagnostics:RuntimeWarning")
def test_request_publish_oserror_is_launch_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path)

    def broken(path: Path, payload: dict) -> None:
        raise PermissionError("denied")
    monkeypatch.setattr(matlab_pool, "_write_json_atomic", broken)
    try:
        result = _render(tmp_path, session)
    finally:
        session.close()
    assert result.process_status == "launch_failed" and not result.termination_observed


def test_close_stops_session_and_records_invocation(tmp_path: Path) -> None:
    launched: list = []
    session = _session(tmp_path, launched=launched)
    _render(tmp_path, session)
    session.close()
    process = launched[0][2]
    assert process.returncode == 0 and not process.killed
    session_dir = tmp_path / "metadata" / "matlab_pool" / session.last_session_id
    invocation = json.loads((session_dir / "invocation.json").read_text(encoding="utf-8"))
    assert invocation["exit_code"] == 0 and invocation["finished_at"] is not None
    assert invocation["prefdir_cleanup"] == "removed"
    assert not Path(invocation["matlab_prefdir"]).exists()


def test_close_without_dispatch_launches_nothing(tmp_path: Path) -> None:
    launched: list = []
    session = _session(tmp_path, launched=launched)
    session.close()
    assert launched == []


def test_attempt_fields_survive_history_loading(tmp_path: Path) -> None:
    from acoustic_orchestrator.pipeline.render_history import load_render_history
    session = _session(tmp_path)
    try:
        _render(tmp_path, session)
    finally:
        session.close()
    assert load_render_history(tmp_path / "attempts") is not None


def test_attempt_is_fresh_render_attempt(tmp_path: Path) -> None:
    attempt = RenderAttempt(_manifest(tmp_path).resolve(), tmp_path / "attempts")
    session = _session(tmp_path)
    try:
        result = render_manifest_with_matlab_pool(_manifest(tmp_path), session, attempt=attempt,
                                                  diagnostics_dir=tmp_path / "diagnostics")
    finally:
        session.close()
    assert result.attempt is attempt and result.process_status == "succeeded"
