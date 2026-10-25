"""Best-effort evidence for one MATLAB invocation, never pipeline state."""

import json
import shutil
import sys
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import UUID, uuid4


class MatlabActivityReader:
    """Bounded incremental diagnostic tail. Never warns or decides outcomes."""

    def __init__(self, path: Path, attempt_id: str) -> None:
        self.path = path
        self.attempt_id = attempt_id
        self.offset = 0
        self.tail = b""
        self.sequence = 0
        self.activity = "arranque / actividad interna desconocida"

    def poll(self) -> str:
        try:
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                data = stream.read(65536)
                self.offset = stream.tell()
            lines = (self.tail + data).split(b"\n")
            self.tail = lines.pop()
            if len(self.tail) > 65536:
                self.tail = b""  # Discard oversized diagnostic records.
            for line in lines:
                try:
                    event = json.loads(line.decode("utf-8"))
                    if (not isinstance(event, dict) or event.get("schema_version") != 1
                            or event.get("attempt_id") != self.attempt_id
                            or type(event.get("sequence")) is not int
                            or event["sequence"] <= self.sequence
                            or not isinstance(event.get("timestamp"), str)):
                        continue
                    kind = event.get("type")
                    if kind in {"stage_started", "stage_completed"}:
                        if event.get("stage") not in {"initialization", "simulation", "artifact_writing"}:
                            continue
                        activity = event["stage"]
                    elif kind in {"step_started", "step_completed"}:
                        if event.get("stage") != "simulation" or event.get("step") not in {"raven_run", "audio_postprocessing"}:
                            continue
                        activity = f"simulation / {event['step']}"
                    elif kind == "entrypoint_completed":
                        activity = "entrypoint completado; esperando terminacion del proceso"
                    else:
                        continue
                    self.sequence = event["sequence"]
                    self.activity = activity
                except (ValueError, TypeError, UnicodeError):
                    continue
        except OSError:
            pass
        return self.activity


def diagnostic_warning(message: str) -> None:
    try:
        warnings.warn(f"MATLAB diagnostics: {message}", RuntimeWarning, stacklevel=2)
    except Exception:
        try:
            print(f"MATLAB diagnostics: {message}", file=sys.stderr)
        except Exception:
            pass


class MatlabDiagnostics:
    """Attempt-local files; callers must not use these to accept/resume outputs."""

    def __init__(
        self, manifest_path: Path, diagnostics_dir: Path | None = None, *,
        attempt_id: str | None = None, variant_id: str | None = None,
        input_snapshot_path: Path | None = None,
    ) -> None:
        # Functional attempts own identity when provided. Standalone diagnostic
        # callers keep their existing UUID-per-invocation behavior.
        self.attempt_id = uuid4().hex if attempt_id is None else UUID(attempt_id).hex
        self.directory = (diagnostics_dir or manifest_path.parent / "matlab_diagnostics") / self.attempt_id
        self.manifest_path = manifest_path
        self.snapshot_path = self.directory / "input_manifest.json"
        self.events_path = self.directory / "events.jsonl"
        self.console_path = self.directory / "console.log"
        self.summary_path = self.directory / "invocation.json"
        self.started = time.monotonic()
        self.record: dict[str, Any] = {
            "schema_version": 1, "attempt_id": self.attempt_id,
            "scene_id": None, "variant_id": variant_id if variant_id is not None else manifest_path.stem, "output_type": None,
            "manifest_path": manifest_path.as_posix(), "manifest_snapshot_path": self.snapshot_path.as_posix(),
            "console_path": self.console_path.as_posix(), "events_path": self.events_path.as_posix(),
            "started_at": self._now(), "finished_at": None, "duration_s": None,
            "command": None, "process_status": "running", "exit_code": None, "error": None,
            "last_started_stage": None, "last_completed_stage": None,
            "last_started_step": None, "last_completed_step": None,
            "entrypoint_completed": False, "failure_location": None,
            "artifacts": [], "diagnostic_errors": [],
        }
        self.ready = False
        try:
            self.directory.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(input_snapshot_path or manifest_path, self.snapshot_path)
            self.ready = True
        except Exception as exc:
            self.warn(exc)
        self.safely(self._read_input)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def warn(self, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"
        self.record["diagnostic_errors"].append(message)
        diagnostic_warning(f"{self.attempt_id}: {message}")

    def safely(self, operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except Exception as exc:
            self.warn(exc)
            return None

    def _read_input(self) -> None:
        payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        self.record["scene_id"] = payload.get("scene_id")
        self.record["output_type"] = payload.get("render", {}).get("active_output_type")
        render = payload.get("render", {})
        for kind, field in (("wav", "output_wav_path"), ("metadata", "output_metadata_path")):
            if render.get(field):
                path = Path(render[field]).resolve()
                self.record["artifacts"].append({
                    "kind": kind, "path": path.as_posix(), "observed_before": path.is_file(),
                    "observed_after": None, "write_confirmed": False,
                })

    def read_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events = []
        sequence = 0
        stages = {"initialization", "simulation", "artifact_writing"}
        types = {"stage_started", "stage_completed", "step_started", "step_completed",
                 "artifact_written", "entrypoint_completed", "error"}
        # Decode inside the per-line guard: a torn UTF-8 tail must not discard
        # complete events already read (or prevent reading subsequent lines).
        with self.events_path.open("rb") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    if not line.endswith(b"\n"):
                        raise ValueError("incomplete event line")
                    event = json.loads(line.decode("utf-8"))
                    if not isinstance(event, dict):
                        raise ValueError("event is not an object")
                    if (event.get("schema_version") != 1 or event.get("attempt_id") != self.attempt_id
                            or type(event.get("sequence")) is not int or event["sequence"] <= sequence
                            or not isinstance(event.get("timestamp"), str) or event.get("type") not in types):
                        raise ValueError("invalid event identity, sequence, version or type")
                    if event["type"].startswith("stage_") and event.get("stage") not in stages:
                        raise ValueError("invalid stage")
                    if event["type"].startswith("step_") and (
                        event.get("stage") != "simulation" or event.get("step") not in {"raven_run", "audio_postprocessing"}
                    ):
                        raise ValueError("invalid simulation step")
                    if event["type"] == "artifact_written" and not isinstance(event.get("path"), str):
                        raise ValueError("missing artifact path")
                    sequence = event["sequence"]
                    events.append(event)
                except (ValueError, TypeError) as exc:
                    self.warn(ValueError(f"events line {line_number}: {exc}"))
        return events

    def collect_evidence(self) -> None:
        for artifact in self.record["artifacts"]:
            artifact["observed_after"] = self.safely(lambda: Path(artifact["path"]).is_file())
        events = self.safely(self.read_events) or []
        active_stage = None
        for event in events:
            event_type = event["type"]
            if event_type == "stage_started":
                active_stage = event["stage"]
                self.record["last_started_stage"] = event["stage"]
            elif event_type == "stage_completed":
                self.record["last_completed_stage"] = event["stage"]
                if active_stage == event["stage"]:
                    active_stage = None
            elif event_type in {"step_started", "step_completed"}:
                key = "last_started_step" if event_type == "step_started" else "last_completed_step"
                self.record[key] = event["step"]
            elif event_type == "entrypoint_completed":
                self.record["entrypoint_completed"] = True
            elif event_type == "error":
                self.record["matlab_error"] = event.get("error")
            elif event_type == "artifact_written":
                for artifact in self.record["artifacts"]:
                    if Path(event["path"]).resolve() == Path(artifact["path"]):
                        artifact["write_confirmed"] = True
        if self.record["process_status"] == "failed":
            self.record["failure_location"] = (
                "post_entrypoint" if self.record["entrypoint_completed"] else active_stage or "indeterminate"
            )

    def save(self) -> None:
        temp = self.summary_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.record, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.summary_path)

    def begin(self, command: list[str]) -> BinaryIO | None:
        self.record["command"] = command
        self.safely(self.save)
        return self.safely(lambda: self.console_path.open("wb"))

    def close_console(self, console: BinaryIO | None) -> None:
        if console is not None:
            self.safely(console.flush)
            self.safely(console.close)

    def finish(self, exit_code: int | None, error: Exception | None) -> None:
        self.record.update(
            finished_at=self._now(), duration_s=max(0.0, time.monotonic() - self.started),
            exit_code=exit_code,
            process_status=("succeeded" if exit_code == 0 else "failed" if exit_code is not None else
                            "launch_failed" if isinstance(error, OSError) else "termination_unobserved"),
            error=None if error is None else {"type": type(error).__name__, "message": str(error)},
        )
        if error is not None:
            self.record["failure_location"] = "launch" if exit_code is None and isinstance(error, OSError) else "indeterminate"
        self.safely(self.collect_evidence)
        self.safely(self.save)
