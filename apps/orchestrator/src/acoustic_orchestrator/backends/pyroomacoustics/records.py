"""Completion, failure and diagnostic-event writers of the worker process.

Format-compatible with the orchestrator's readers (``render_attempt``,
``render_failure``, ``matlab_diagnostics``), which serve as the test oracle,
and with the MATLAB writers (``write_render_completion.m``,
``write_render_failure.m``, ``emit_render_event.m``). Functional records are
written to ``<file>.tmp`` and published with ``os.replace`` only when the
target does not exist yet; diagnostic events are best effort.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


class RecordExistsError(RuntimeError):
    """A functional record is already present and must never be replaced."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _absolute(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"path must be absolute: {value}")
    return path.resolve()


def publish_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    """Write ``<path>.tmp``, refuse an existing target, then ``os.replace``."""
    path = _absolute(path)
    if path.exists():
        raise RecordExistsError(f"record already exists: {path}")
    data = (json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists():
        raise RecordExistsError(f"record already exists: {path}")
    os.replace(temporary, path)


def _check_identity(attempt_id: str, manifest_sha256: str) -> None:
    if re.fullmatch(r"[0-9a-f]{32}", attempt_id) is None or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None:
        raise ValueError("invalid attempt identity or manifest hash")


def write_completion(
    *, snapshot_path: Path, attempt_id: str, variant_id: str, manifest_sha256: str, scene_id: str,
    output_type: str, completion_path: Path, artifacts: Sequence[tuple[str, str]],
) -> dict:
    """Publish the completion record after every inventoried output is closed."""
    _check_identity(attempt_id, manifest_sha256)
    actual = sha256_file(snapshot_path)
    if actual != manifest_sha256:
        raise ValueError("snapshot does not match the attempt manifest hash")
    if _absolute(completion_path).exists():
        raise RecordExistsError(f"completion already exists: {completion_path}")
    entries = []
    for kind, value in artifacts:
        path = _absolute(value)
        if not path.is_file():
            raise FileNotFoundError(f"missing output: {path}")
        entries.append({"kind": kind, "path": path.as_posix(), "size_bytes": path.stat().st_size,
                        "sha256": sha256_file(path)})
    if len({entry["path"] for entry in entries}) != len(entries):
        raise ValueError("output paths are not distinct")
    receipt = {"schema_version": 1, "attempt_id": attempt_id, "scene_id": scene_id, "variant_id": variant_id,
               "output_type": output_type, "manifest_sha256": actual, "artifacts": entries}
    publish_json_exclusive(Path(completion_path), receipt)
    return receipt


def write_failure(
    *, snapshot_path: Path, attempt_id: str, variant_id: str, manifest_sha256: str, failure_path: Path,
    completion_path: Path | None, identifier: str, message: str,
) -> dict:
    """Publish the deterministic ``invalid_input`` report for a rejected input."""
    _check_identity(attempt_id, manifest_sha256)
    # Never attribute bytes that were not observed.
    if sha256_file(snapshot_path) != manifest_sha256:
        raise ValueError("snapshot does not match the attempt manifest hash")
    if completion_path is not None and Path(completion_path).exists():
        raise RecordExistsError("completion evidence already exists")
    report = {"schema_version": 1, "attempt_id": attempt_id, "variant_id": variant_id,
              "manifest_sha256": manifest_sha256, "category": "invalid_input",
              "error": {"identifier": identifier, "message": message.strip() or identifier}}
    publish_json_exclusive(Path(failure_path), report)
    return report


def utc_timestamp() -> str:
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


class EventWriter:
    """Best-effort diagnostic events; failures never change the render outcome."""

    def __init__(self, events_path: Path | None, attempt_id: str) -> None:
        self.events_path = events_path
        self.attempt_id = attempt_id
        self.sequence = 0

    def emit(self, event_type: str, stage: str, **details: Any) -> None:
        if self.events_path is None:
            return
        try:
            self.sequence += 1
            event = {**details, "schema_version": 1, "attempt_id": self.attempt_id, "sequence": self.sequence,
                     "timestamp": utc_timestamp(), "type": event_type, "stage": stage}
            line = json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
            with open(self.events_path, "a", encoding="utf-8") as stream:
                stream.write(line)
        except Exception as exc:  # pragma: no cover - diagnostics only
            try:
                print(f"[WARN] Diagnóstico incompleto: {exc}", file=sys.stderr)
            except Exception:
                pass
