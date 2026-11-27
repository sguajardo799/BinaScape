"""Functional evidence for a render attempt, independent of diagnostics."""

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, TypedDict
from uuid import uuid4

from acoustic_orchestrator.pipeline.output_paths import resolve_render_attempts_dir


class ExpectedArtifact(TypedDict):
    kind: str
    path: str


class CompletionValidation(TypedDict):
    status: Literal["missing", "valid", "invalid"]
    issues: list[str]


def sha256_file(path: Path) -> str:
    """Hash exact file bytes with bounded memory, not reserialized JSON."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _json_number(value: str) -> int | float:
    # MATLAB jsonencode uses exponent notation for large double-valued counts
    # (e.g. size_bytes=1.411244E+6). JSON integer semantics concern the value,
    # not its spelling. Never round fractions or normalize unsafe float integers.
    exact = Decimal(value)
    if exact.is_finite() and abs(exact) <= 2**53 and exact == exact.to_integral_value():
        return int(exact)
    return float(value)


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
        parse_float=_json_number,
    )
    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be an object")
    return payload


class RenderAttempt:
    """Prepare durable input evidence before launch; storage errors are fatal.

    A snapshot is written exclusively in a fresh UUID directory and never
    replaced by this API. The mutable outcome is published atomically.
    """

    def __init__(self, manifest_path: Path, attempts_dir: Path | None = None, *,
                 attempt_id: str | None = None, chain_context: Mapping[str, Any] | None = None) -> None:
        self.manifest_path = manifest_path.resolve()
        self.attempt_id = attempt_id or uuid4().hex
        if re.fullmatch(r"[0-9a-f]{32}", self.attempt_id) is None:
            raise ValueError("attempt_id must be a UUID hex string")
        self.directory = resolve_render_attempts_dir(self.manifest_path, attempts_dir) / self.attempt_id
        self.snapshot_path = self.directory / "input_manifest.json"
        self.completion_path = self.directory / "completion.json"
        self.failure_path = self.directory / "failure.json"
        self.outcome_path = self.directory / "outcome.json"

        # Validate and snapshot the same bytes; never reread a changing runtime.
        source_bytes = self.manifest_path.read_bytes()
        payload = json.loads(source_bytes.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
        if not isinstance(payload, dict):
            raise ValueError("render manifest must be an object")
        self.scene_id = _required_string(payload.get("scene_id"), "scene_id")
        render = payload.get("render")
        if not isinstance(render, dict):
            raise ValueError("render manifest must have a render object")
        self.output_type = _required_string(render.get("active_output_type"), "active_output_type")
        self.variant_id = self.manifest_path.stem
        self.expected_artifacts: list[ExpectedArtifact] = [
            {"kind": kind, "path": _absolute_output_path(render.get(field), field)}
            for kind, field in (("wav", "output_wav_path"), ("metadata", "output_metadata_path"))
        ]
        if len({item["path"] for item in self.expected_artifacts}) != len(self.expected_artifacts):
            raise ValueError("render output paths must be distinct")
        self.manifest_sha256 = hashlib.sha256(source_bytes).hexdigest()
        self.record: dict[str, Any] = {
            "schema_version": 1,
            "attempt_id": self.attempt_id,
            "scene_id": self.scene_id,
            "variant_id": self.variant_id,
            "output_type": self.output_type,
            "manifest_path": self.manifest_path.as_posix(),
            "manifest_snapshot_path": self.snapshot_path.as_posix(),
            "manifest_sha256": self.manifest_sha256,
            "completion_path": self.completion_path.as_posix(),
            "failure_path": self.failure_path.as_posix(),
            "expected_artifacts": self.expected_artifacts,
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "command": None,
            "process_status": "running",
            "exit_code": None,
            "error": None,
            "render_validity": "not_verifiable",
            "validation": {"ok": False, "issues": ["process_not_observed"]},
            "decision": "pending",
            "commit_status": "pending",
        }
        if chain_context is not None:
            allowed = {"chain_id", "attempt_ordinal", "previous_attempt_id", "input_sha256", "retry_policy"}
            if set(chain_context) - allowed:
                raise ValueError("unsupported chain context fields")
            self.record.update(chain_context)
        self.directory.mkdir(parents=True, exist_ok=False)
        with self.snapshot_path.open("xb") as snapshot:
            snapshot.write(source_bytes)
        self.save()

    def save(self) -> None:
        """Publish functional state; unlike diagnostics, never suppress errors."""
        temporary = self.outcome_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(self.record, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
        temporary.replace(self.outcome_path)


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or invalid {field}")
    return value


def _absolute_output_path(value: Any, field: str) -> str:
    path = Path(_required_string(value, field))
    if not path.is_absolute():
        raise ValueError(f"{field} must be absolute")
    return path.resolve().as_posix()


def _normalized_path(value: Any) -> str:
    return os.path.normcase(_absolute_output_path(value, "artifact path"))


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def inspect_completion(expected: Mapping[str, Any]) -> CompletionValidation:
    """Verify functional evidence against independently prepared expectations.

    `expected` is the orchestrator's attempt record, never the receipt itself.
    This checks file identity/integrity only, not audio or acoustic validity.
    Missing and invalid evidence remain distinct for the acceptance policy.
    """
    issues: list[str] = []
    completion_path = Path(expected["completion_path"])
    try:
        completion_path.stat()
    except FileNotFoundError:
        return {"status": "missing", "issues": ["completion_missing"]}
    except OSError as exc:
        return {"status": "invalid", "issues": [f"completion_unreadable: {exc}"]}

    try:
        receipt = read_json_object(completion_path)
        if type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1:
            issues.append("completion_schema_unsupported")
        for field in ("attempt_id", "scene_id", "variant_id", "output_type"):
            value = receipt.get(field)
            if not isinstance(value, str) or not value or value != expected[field]:
                issues.append(f"completion_{field}_mismatch")

        manifest_hash = receipt.get("manifest_sha256")
        if not _is_sha256(manifest_hash) or manifest_hash != expected["manifest_sha256"]:
            issues.append("completion_manifest_hash_mismatch")
        if sha256_file(Path(expected["manifest_snapshot_path"])) != expected["manifest_sha256"]:
            issues.append("completion_snapshot_hash_mismatch")

        artifacts = receipt.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            issues.append("completion_artifacts_invalid")
        else:
            required = {
                (_required_string(item["kind"], "kind"), _normalized_path(item["path"]))
                for item in expected["expected_artifacts"]
            }
            seen: set[tuple[str, str]] = set()
            seen_paths: set[str] = set()
            for index, item in enumerate(artifacts):
                if not isinstance(item, dict):
                    issues.append(f"completion_artifact_invalid: {index}")
                    continue
                try:
                    kind = _required_string(item.get("kind"), "kind")
                    path = _normalized_path(item.get("path"))
                except (ValueError, OSError) as exc:
                    issues.append(f"completion_artifact_invalid: {index}: {exc}")
                    continue
                key = (kind, path)
                if key in seen or path in seen_paths:
                    issues.append(f"completion_artifact_duplicate: {index}")
                seen.add(key)
                seen_paths.add(path)
                if key not in required:
                    issues.append(f"completion_artifact_unexpected: {index}")
                    continue  # Never open arbitrary paths supplied by a receipt.
                size = item.get("size_bytes")
                digest = item.get("sha256")
                if type(size) is not int or size < 0 or not _is_sha256(digest):
                    issues.append(f"completion_artifact_fields_invalid: {index}")
                    continue
                try:
                    output_path = Path(item["path"])
                    if output_path.stat().st_size != size:
                        issues.append(f"completion_artifact_size_mismatch: {kind}")
                    if sha256_file(output_path) != digest:
                        issues.append(f"completion_artifact_hash_mismatch: {kind}")
                except OSError as exc:
                    issues.append(f"completion_artifact_unreadable: {kind}: {exc}")
            if seen != required:
                issues.append("completion_artifact_inventory_mismatch")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        issues.append(f"completion_unreadable: {exc}")
    return {"status": "invalid" if issues else "valid", "issues": issues}
