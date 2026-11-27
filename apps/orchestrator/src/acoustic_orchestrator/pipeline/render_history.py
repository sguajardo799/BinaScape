"""Read functional attempt history for fail-closed reuse, never diagnostic logs."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from acoustic_orchestrator.pipeline.render_attempt import inspect_completion, read_json_object, sha256_file
from acoustic_orchestrator.pipeline.render_validation import equivalent_render_inputs
from acoustic_orchestrator.pipeline.render_failure import inspect_failure


@dataclass
class RenderHistory:
    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


def load_render_history(root: Path) -> RenderHistory:
    history = RenderHistory()
    try:
        directories = list(root.iterdir())
    except FileNotFoundError:
        return history
    except OSError as exc:
        history.issues.append(f"attempt_history_unreadable: {exc}")
        return history
    for directory in directories:
        try:
            if not directory.is_dir():
                continue
            record = read_json_object(directory / "outcome.json")
            variant_id = record.get("variant_id")
            if (not isinstance(variant_id, str) or not variant_id or record.get("attempt_id") != directory.name
                    or re.fullmatch(r"[0-9a-f]{32}", directory.name) is None
                    or type(record.get("schema_version")) is not int or record["schema_version"] != 1):
                raise ValueError("invalid attempt identity or schema")
            record = {**record, "_outcome_path": directory / "outcome.json"}
            history.records.setdefault(variant_id, []).append(record)
        except FileNotFoundError:
            # Preparation can fail after the snapshot but before initial outcome.
            try:
                snapshot = read_json_object(directory / "input_manifest.json")
                scene, output = snapshot["scene_id"], snapshot["render"]["active_output_type"]
                if not isinstance(scene, str) or not isinstance(output, str):
                    raise ValueError("unidentified interrupted preparation")
                history.records.setdefault(f"{scene}__{output}", []).append({"_unresolved": True})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                history.issues.append(f"attempt_history_unresolved: {directory.name}: {exc}")
        except (OSError, ValueError, TypeError) as exc:
            history.issues.append(f"attempt_history_unresolved: {directory.name}: {exc}")
    return history


def resume_evidence(
    history: RenderHistory, paths: Mapping[str, Any], existing: Mapping[str, Any] | None,
    expected_manifest: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """Return an accepted prior attempt, or allow strict legacy reuse without one."""
    if history.issues:
        return False, None
    if existing is not None and existing.get("decision") not in (None, "continue", "continue_with_warning"):
        return False, None
    records = history.records.get(paths["variant_id"], [])
    if not records:
        # An index reference whose functional evidence disappeared is not legacy.
        known_failure = bool(existing and (
            existing.get("attempt_id") or existing.get("attempt_outcome_path") or existing.get("completion_path")
            or existing.get("recovery_reason")
            or existing.get("decision") == "continue_with_warning"
            or existing.get("process_status") in ("failed", "launch_failed", "running", "termination_unobserved")
            or existing.get("exit_code") not in (None, 0)
        ))
        return not known_failure, None
    try:
        for record in records:
            if record.get("_unresolved"):
                return False, None
            timestamp = datetime.fromisoformat(record["started_at"])
            if timestamp.tzinfo is None:
                return False, None
        latest = max(records, key=lambda item: datetime.fromisoformat(item["started_at"]))
        if existing and existing.get("attempt_id") and existing["attempt_id"] != latest["attempt_id"]:
            return False, None
        if (latest.get("commit_status") != "committed" or latest.get("termination_observed") is not True
                or latest.get("decision") not in ("continue", "continue_with_warning")
                or latest.get("scene_id") != paths["scene_id"] or latest.get("output_type") != paths["output_type"]
                or not isinstance(latest.get("validation"), dict) or latest["validation"].get("ok") is not True):
            return False, None
        directory = latest["_outcome_path"].parent
        snapshot_path = directory / "input_manifest.json"
        completion_path = directory / "completion.json"
        if (Path(latest["manifest_snapshot_path"]).resolve() != snapshot_path.resolve()
                or Path(latest["completion_path"]).resolve() != completion_path.resolve()
                or sha256_file(snapshot_path) != latest["manifest_sha256"]):
            return False, None
        current = expected_manifest or read_json_object(Path(paths["runtime_manifest_path"]))
        if not equivalent_render_inputs(current, read_json_object(snapshot_path)):
            return False, None
        expectation = {**latest, "expected_artifacts": [
            {"kind": "wav", "path": paths["rendered_wav_path"]},
            {"kind": "metadata", "path": paths["render_metadata_path"]},
        ]}
        completion = inspect_completion(expectation)
        if inspect_failure(expectation)["status"] != "missing":
            return False, None
        code = latest.get("exit_code")
        if type(code) is not int:
            return False, None
        if code == 0:
            allowed = (latest.get("process_status") == "succeeded" and latest.get("decision") == "continue"
                       and latest.get("error") is None and completion["status"] != "invalid")
        else:
            error = latest.get("error")
            allowed = (latest.get("process_status") == "failed" and latest.get("decision") == "continue_with_warning"
                       and latest.get("recovery_reason") == "post_completion" and completion["status"] == "valid"
                       and isinstance(error, dict) and error.get("type") == "CalledProcessError"
                       and isinstance(error.get("message"), str) and bool(error["message"]))
        return allowed, latest if allowed else None
    except (OSError, ValueError, KeyError, TypeError):
        return False, None


def history_root(paths: Mapping[str, Any], existing: Mapping[str, Any] | None = None) -> Path:
    if existing and existing.get("attempt_outcome_path"):
        return Path(existing["attempt_outcome_path"]).parent.parent
    runtime_parent = Path(paths["runtime_manifest_path"]).parent
    if runtime_parent.parts[-3:] == ("manifests", "runtime", "render"):
        return runtime_parent.parent.parent.parent / "render_attempts"
    return runtime_parent / "render_attempts"
