import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict, cast
from uuid import uuid4

from acoustic_orchestrator.pipeline.matlab_runner import RenderVariantPaths


_INDEX_LOCKS_GUARD = threading.Lock()
_INDEX_LOCKS: dict[Path, threading.Lock] = {}
_INDEX_REPLACE_ATTEMPTS = 10
_INDEX_REPLACE_RETRY_DELAY_S = 0.01

VariantStatus = Literal["planned", "completed", "partial", "failed"]
ClarityStatus = Literal["planned", "submitted", "completed", "partial", "failed", "blocked", "skipped"]
ClarityBatchStatus = Literal["planned", "submitted", "completed", "partial", "failed", "blocked"]
ClarityStatusSummaryKey = Literal[
    "planned_jobs",
    "submitted_jobs",
    "completed_jobs",
    "partial_jobs",
    "failed_jobs",
    "blocked_jobs",
    "skipped_jobs",
]


class ObservedOutputsRecord(TypedDict):
    runtime_manifest: bool
    rendered_wav: bool
    render_metadata: bool


class ValidationRecord(TypedDict):
    ok: bool
    issues: list[str]


class MetadataSnapshot(TypedDict, total=False):
    scene_id: str
    output_type: str
    hrtf_id: str
    rendered_wav_path: str
    render_metadata_path: str


class VariantRecord(TypedDict):
    variant_id: str
    scene_id: str
    output_type: str
    status: VariantStatus
    resumed: bool
    attempted: bool
    metadata_required: bool
    runtime_manifest_path: str
    rendered_wav_path: str
    render_metadata_path: str | None
    receiver_ir_path: str
    observed_outputs: ObservedOutputsRecord
    metadata_snapshot: MetadataSnapshot | None
    validation: ValidationRecord
    updated_at: str


class ClarityBackendInvocationRecord(TypedDict):
    mode: str
    entrypoint: str
    use_uv: bool
    backend_project_path: str | None


class ClarityJobManifestRecord(TypedDict):
    schema_version: str
    run_id: str
    job_id: str
    variant_id: str
    scene_id: str
    output_type: str
    hearing_profile_id: str
    hearing_profiles_path: str
    backend_invocation: ClarityBackendInvocationRecord
    input_wav_path: str
    input_render_metadata_path: str
    output_dir: str
    expected_output_wav_path: str
    expected_output_metadata_path: str


class ClarityObservedOutputsRecord(TypedDict):
    expected_output_wav: bool
    expected_output_metadata: bool


class ClarityIndexSummary(TypedDict):
    total_jobs: int
    planned_jobs: int
    submitted_jobs: int
    completed_jobs: int
    partial_jobs: int
    failed_jobs: int
    blocked_jobs: int
    skipped_jobs: int
    resumed_jobs: int
    invalid_jobs: int
    batch_status: ClarityBatchStatus


class ClarityRecord(TypedDict):
    run_id: str
    job_id: str
    variant_id: str
    scene_id: str
    output_type: str
    hearing_profile_id: str
    status: ClarityStatus
    resumed: bool
    attempted: bool
    runnable: bool
    manifest_path: str
    input_wav_path: str
    input_render_metadata_path: str
    hearing_profiles_path: str
    backend_invocation: ClarityBackendInvocationRecord
    output_dir: str
    expected_output_wav_path: str
    expected_output_metadata_path: str
    backend_command: list[str] | None
    observed_outputs: ClarityObservedOutputsRecord
    validation: ValidationRecord
    updated_at: str


def load_variant_index(index_path: Path) -> dict[str, VariantRecord]:
    if not index_path.is_file():
        return {}

    records: dict[str, VariantRecord] = {}
    for raw_line in index_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        parsed_record = cast(dict[str, Any], json.loads(raw_line))
        normalized_record = _normalize_variant_record(parsed_record)
        records[normalized_record["variant_id"]] = normalized_record
    return records


def build_planned_variant_record(
    variant_paths: RenderVariantPaths,
    *,
    metadata_required: bool,
) -> VariantRecord:
    return {
        "variant_id": variant_paths["variant_id"],
        "scene_id": variant_paths["scene_id"],
        "output_type": variant_paths["output_type"],
        "status": "planned",
        "resumed": False,
        "attempted": False,
        "metadata_required": metadata_required,
        "runtime_manifest_path": variant_paths["runtime_manifest_path"],
        "rendered_wav_path": variant_paths["rendered_wav_path"],
        "render_metadata_path": variant_paths["render_metadata_path"],
        "receiver_ir_path": variant_paths["receiver_ir_path"],
        "observed_outputs": {
            "runtime_manifest": False,
            "rendered_wav": False,
            "render_metadata": False,
        },
        "metadata_snapshot": None,
        "validation": {"ok": True, "issues": []},
        "updated_at": _now_isoformat(),
    }


def inspect_variant(
    variant_paths: RenderVariantPaths,
    *,
    metadata_required: bool,
    attempted: bool,
    resumed: bool = False,
) -> VariantRecord:
    observed_outputs = _observe_outputs(variant_paths)
    metadata_snapshot, validation_issues = _read_render_metadata(variant_paths["render_metadata_path"])

    required_outputs = [
        observed_outputs["runtime_manifest"],
        observed_outputs["rendered_wav"],
    ]
    if metadata_required:
        required_outputs.append(observed_outputs["render_metadata"])

    if not attempted and not resumed:
        status: VariantStatus = "planned"
    elif all(required_outputs):
        status = "completed"
    elif any(required_outputs):
        status = "partial"
    else:
        status = "failed"

    validation_issues.extend(
        _validate_variant(
            variant_paths,
            metadata_required=metadata_required,
            observed_outputs=observed_outputs,
            metadata_snapshot=metadata_snapshot,
        )
    )

    return {
        "variant_id": variant_paths["variant_id"],
        "scene_id": variant_paths["scene_id"],
        "output_type": variant_paths["output_type"],
        "status": status,
        "resumed": resumed,
        "attempted": attempted,
        "metadata_required": metadata_required,
        "runtime_manifest_path": variant_paths["runtime_manifest_path"],
        "rendered_wav_path": variant_paths["rendered_wav_path"],
        "render_metadata_path": variant_paths["render_metadata_path"],
        "receiver_ir_path": variant_paths["receiver_ir_path"],
        "observed_outputs": observed_outputs,
        "metadata_snapshot": metadata_snapshot,
        "validation": {
            "ok": not validation_issues,
            "issues": validation_issues,
        },
        "updated_at": _now_isoformat(),
    }


def should_resume_variant(
    existing_records: dict[str, VariantRecord],
    variant_paths: RenderVariantPaths,
    *,
    metadata_required: bool,
) -> bool:
    existing_record = existing_records.get(variant_paths["variant_id"])
    if existing_record is not None:
        if existing_record["status"] != "completed":
            return False
        if not existing_record["validation"]["ok"]:
            return False

    current_record = inspect_variant(
        variant_paths,
        metadata_required=metadata_required,
        attempted=False,
        resumed=True,
    )
    return current_record["status"] == "completed" and current_record["validation"]["ok"]


def upsert_variant(index_path: Path, records: dict[str, VariantRecord], record: VariantRecord) -> None:
    _upsert_jsonl(index_path=index_path, records=records, key=record["variant_id"], record=record)


def summarize_variant_index(records: dict[str, VariantRecord]) -> dict[str, int]:
    summary = {
        "total_variants": len(records),
        "planned_variants": 0,
        "completed_variants": 0,
        "partial_variants": 0,
        "failed_variants": 0,
        "resumed_variants": 0,
        "inconsistent_variants": 0,
    }

    for record in records.values():
        summary[f"{record['status']}_variants"] += 1
        if record["resumed"]:
            summary["resumed_variants"] += 1
        if not record["validation"]["ok"]:
            summary["inconsistent_variants"] += 1

    return summary


def write_clarity_job_manifest(manifest_path: Path, jobs: list[ClarityJobManifestRecord]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(job, sort_keys=True) for job in jobs]
    manifest_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_clarity_index(index_path: Path) -> dict[str, ClarityRecord]:
    if not index_path.is_file():
        return {}

    records: dict[str, ClarityRecord] = {}
    for raw_line in index_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        parsed_record = cast(dict[str, Any], json.loads(raw_line))
        normalized_record = _normalize_clarity_record(parsed_record)
        records[normalized_record["job_id"]] = normalized_record
    return records


def build_clarity_record(
    job: ClarityJobManifestRecord,
    *,
    manifest_path: Path,
    status: ClarityStatus,
    attempted: bool,
    resumed: bool,
    runnable: bool,
    backend_command: list[str] | None = None,
    validation_issues: list[str] | None = None,
) -> ClarityRecord:
    observed_outputs = _observe_clarity_outputs(job)
    return {
        "run_id": job["run_id"],
        "job_id": job["job_id"],
        "variant_id": job["variant_id"],
        "scene_id": job["scene_id"],
        "output_type": job["output_type"],
        "hearing_profile_id": job["hearing_profile_id"],
        "status": status,
        "resumed": resumed,
        "attempted": attempted,
        "runnable": runnable,
        "manifest_path": manifest_path.resolve().as_posix(),
        "input_wav_path": job["input_wav_path"],
        "input_render_metadata_path": job["input_render_metadata_path"],
        "hearing_profiles_path": job["hearing_profiles_path"],
        "backend_invocation": job["backend_invocation"],
        "output_dir": job["output_dir"],
        "expected_output_wav_path": job["expected_output_wav_path"],
        "expected_output_metadata_path": job["expected_output_metadata_path"],
        "backend_command": backend_command,
        "observed_outputs": observed_outputs,
        "validation": {"ok": not (validation_issues or []), "issues": list(validation_issues or [])},
        "updated_at": _now_isoformat(),
    }


def inspect_clarity_record(
    job: ClarityJobManifestRecord,
    *,
    manifest_path: Path,
    attempted: bool,
    resumed: bool = False,
    backend_command: list[str] | None = None,
) -> ClarityRecord:
    observed_outputs = _observe_clarity_outputs(job)
    result_metadata, metadata_read_issues = _read_clarity_result_metadata(job["expected_output_metadata_path"])
    validation_issues = list(metadata_read_issues)
    validation_issues.extend(_validate_clarity_result(job, observed_outputs, result_metadata))
    status = _derive_clarity_status(
        attempted=attempted,
        resumed=resumed,
        observed_outputs=observed_outputs,
        result_metadata=result_metadata,
        validation_issues=validation_issues,
    )

    return build_clarity_record(
        job,
        manifest_path=manifest_path,
        status=status,
        attempted=attempted,
        resumed=resumed,
        runnable=True,
        backend_command=backend_command,
        validation_issues=validation_issues,
    )


def should_resume_clarity_job(
    existing_records: dict[str, ClarityRecord],
    job: ClarityJobManifestRecord,
    *,
    manifest_path: Path,
) -> bool:
    existing_record = existing_records.get(job["job_id"])
    if existing_record is not None and not existing_record["validation"]["ok"]:
        return False

    current_record = inspect_clarity_record(
        job,
        manifest_path=manifest_path,
        attempted=False,
        resumed=True,
    )
    return current_record["status"] in {"completed", "skipped"} and current_record["validation"]["ok"]


def upsert_clarity_record(index_path: Path, records: dict[str, ClarityRecord], record: ClarityRecord) -> None:
    _upsert_jsonl(index_path=index_path, records=records, key=record["job_id"], record=record)


def summarize_clarity_index(records: dict[str, ClarityRecord]) -> ClarityIndexSummary:
    summary: ClarityIndexSummary = {
        "total_jobs": len(records),
        "planned_jobs": 0,
        "submitted_jobs": 0,
        "completed_jobs": 0,
        "partial_jobs": 0,
        "failed_jobs": 0,
        "blocked_jobs": 0,
        "skipped_jobs": 0,
        "resumed_jobs": 0,
        "invalid_jobs": 0,
        "batch_status": "blocked",
    }

    for record in records.values():
        status_key = _clarity_status_summary_key(record["status"])
        summary[status_key] += 1
        if record["resumed"]:
            summary["resumed_jobs"] += 1
        if not record["validation"]["ok"]:
            summary["invalid_jobs"] += 1

    summary["batch_status"] = _summarize_clarity_batch_status(records)

    return summary


def _clarity_status_summary_key(status: ClarityStatus) -> ClarityStatusSummaryKey:
    status_to_summary_key: dict[ClarityStatus, ClarityStatusSummaryKey] = {
        "planned": "planned_jobs",
        "submitted": "submitted_jobs",
        "completed": "completed_jobs",
        "partial": "partial_jobs",
        "failed": "failed_jobs",
        "blocked": "blocked_jobs",
        "skipped": "skipped_jobs",
    }
    return status_to_summary_key[status]


def _normalize_variant_record(record: dict[str, Any]) -> VariantRecord:
    render_metadata_path = cast(str | None, record.get("render_metadata_path"))
    observed_outputs = cast(ObservedOutputsRecord | None, record.get("observed_outputs")) or {
        "runtime_manifest": _path_exists(record.get("runtime_manifest_path")),
        "rendered_wav": _path_exists(record.get("rendered_wav_path")),
        "render_metadata": _path_exists(render_metadata_path),
    }
    metadata_snapshot = cast(MetadataSnapshot | None, record.get("metadata_snapshot"))
    validation = cast(ValidationRecord | None, record.get("validation")) or {"ok": True, "issues": []}

    return {
        "variant_id": cast(str, record["variant_id"]),
        "scene_id": cast(str, record["scene_id"]),
        "output_type": cast(str, record["output_type"]),
        "status": cast(VariantStatus, record.get("status", "completed")),
        "resumed": bool(record.get("resumed", False)),
        "attempted": bool(record.get("attempted", False)),
        "metadata_required": bool(record.get("metadata_required", False)),
        "runtime_manifest_path": cast(str, record["runtime_manifest_path"]),
        "rendered_wav_path": cast(str, record["rendered_wav_path"]),
        "render_metadata_path": render_metadata_path,
        "receiver_ir_path": cast(str, record["receiver_ir_path"]),
        "observed_outputs": observed_outputs,
        "metadata_snapshot": metadata_snapshot,
        "validation": {
            "ok": bool(validation.get("ok", True)),
            "issues": [str(issue) for issue in validation.get("issues", [])],
        },
        "updated_at": cast(str, record.get("updated_at", _now_isoformat())),
    }


def _normalize_clarity_record(record: dict[str, Any]) -> ClarityRecord:
    observed_outputs = cast(ClarityObservedOutputsRecord | None, record.get("observed_outputs")) or {
        "expected_output_wav": _path_exists(record.get("expected_output_wav_path")),
        "expected_output_metadata": _path_exists(record.get("expected_output_metadata_path")),
    }
    validation = cast(ValidationRecord | None, record.get("validation")) or {"ok": True, "issues": []}
    backend_command = record.get("backend_command")

    return {
        "run_id": cast(str, record.get("run_id", "")),
        "job_id": cast(str, record["job_id"]),
        "variant_id": cast(str, record["variant_id"]),
        "scene_id": cast(str, record["scene_id"]),
        "output_type": cast(str, record["output_type"]),
        "hearing_profile_id": cast(str, record.get("hearing_profile_id", "default")),
        "status": cast(ClarityStatus, record.get("status", "planned")),
        "resumed": bool(record.get("resumed", False)),
        "attempted": bool(record.get("attempted", False)),
        "runnable": bool(record.get("runnable", True)),
        "manifest_path": cast(str, record["manifest_path"]),
        "input_wav_path": cast(str, record["input_wav_path"]),
        "input_render_metadata_path": cast(str, record.get("input_render_metadata_path") or ""),
        "hearing_profiles_path": cast(str, record["hearing_profiles_path"]),
        "backend_invocation": {
            "mode": str(_as_dict(record.get("backend_invocation")).get("mode", "direct")),
            "entrypoint": str(_as_dict(record.get("backend_invocation")).get("entrypoint", "")),
            "use_uv": bool(_as_dict(record.get("backend_invocation")).get("use_uv", False)),
            "backend_project_path": _pick_string(_as_dict(record.get("backend_invocation")).get("backend_project_path")),
        },
        "output_dir": cast(str, record["output_dir"]),
        "expected_output_wav_path": cast(str, record["expected_output_wav_path"]),
        "expected_output_metadata_path": cast(str, record["expected_output_metadata_path"]),
        "backend_command": [str(part) for part in backend_command] if isinstance(backend_command, list) else None,
        "observed_outputs": observed_outputs,
        "validation": {
            "ok": bool(validation.get("ok", True)),
            "issues": [str(issue) for issue in validation.get("issues", [])],
        },
        "updated_at": cast(str, record.get("updated_at", _now_isoformat())),
    }


def _observe_outputs(variant_paths: RenderVariantPaths) -> ObservedOutputsRecord:
    metadata_path = variant_paths["render_metadata_path"]
    return {
        "runtime_manifest": Path(variant_paths["runtime_manifest_path"]).is_file(),
        "rendered_wav": Path(variant_paths["rendered_wav_path"]).is_file(),
        "render_metadata": Path(metadata_path).is_file() if metadata_path else False,
    }


def _observe_clarity_outputs(job: ClarityJobManifestRecord) -> ClarityObservedOutputsRecord:
    return {
        "expected_output_wav": Path(job["expected_output_wav_path"]).is_file(),
        "expected_output_metadata": Path(job["expected_output_metadata_path"]).is_file(),
    }


def _read_render_metadata(render_metadata_path: str | None) -> tuple[MetadataSnapshot | None, list[str]]:
    if not render_metadata_path:
        return None, []

    metadata_path = Path(render_metadata_path)
    if not metadata_path.is_file():
        return None, []

    try:
        payload = cast(dict[str, Any], json.loads(metadata_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"render_metadata_parse_error: {exc}"]

    summary = _as_dict(payload.get("summary"))
    render = _as_dict(payload.get("render"))

    metadata_snapshot: MetadataSnapshot = {}
    scene_id = _pick_string(payload.get("scene_id"), summary.get("scene_id"))
    output_type = _pick_string(payload.get("output_type"), summary.get("output_type"))
    hrtf_id = _pick_string(payload.get("hrtf_id"), summary.get("hrtf_id"))
    rendered_wav_path = _pick_string(
        render.get("output_wav_path"),
        payload.get("output_wav_path"),
        summary.get("output_wav_path"),
    )
    normalized_render_metadata_path = _pick_string(
        render.get("output_metadata_path"),
        payload.get("output_metadata_path"),
        summary.get("output_metadata_path"),
    )

    if scene_id is not None:
        metadata_snapshot["scene_id"] = scene_id
    if output_type is not None:
        metadata_snapshot["output_type"] = output_type
    if hrtf_id is not None:
        metadata_snapshot["hrtf_id"] = hrtf_id
    if rendered_wav_path is not None:
        metadata_snapshot["rendered_wav_path"] = rendered_wav_path
    if normalized_render_metadata_path is not None:
        metadata_snapshot["render_metadata_path"] = normalized_render_metadata_path

    return (metadata_snapshot or None), []


def _read_clarity_result_metadata(metadata_path_value: str) -> tuple[dict[str, Any] | None, list[str]]:
    metadata_path = Path(metadata_path_value)
    if not metadata_path.is_file():
        return None, []

    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"clarity_result_parse_error: {exc}"]

    if not isinstance(payload, dict):
        return None, ["clarity_result_payload_not_object"]

    return cast(dict[str, Any], payload), []


def _validate_variant(
    variant_paths: RenderVariantPaths,
    *,
    metadata_required: bool,
    observed_outputs: ObservedOutputsRecord,
    metadata_snapshot: MetadataSnapshot | None,
) -> list[str]:
    issues: list[str] = []
    if metadata_required and not observed_outputs["render_metadata"]:
        issues.append("render_metadata_missing")

    if observed_outputs["render_metadata"] and metadata_snapshot is None:
        issues.append("render_metadata_missing_stable_fields")
        return issues

    if metadata_snapshot is None:
        return issues

    observed_scene_id = metadata_snapshot.get("scene_id")
    if observed_scene_id is not None and observed_scene_id != variant_paths["scene_id"]:
        issues.append(f"scene_id mismatch: expected {variant_paths['scene_id']} got {observed_scene_id}")

    observed_output_type = metadata_snapshot.get("output_type")
    if observed_output_type is not None and observed_output_type != variant_paths["output_type"]:
        issues.append(f"output_type mismatch: expected {variant_paths['output_type']} got {observed_output_type}")

    observed_hrtf_id = metadata_snapshot.get("hrtf_id")
    if observed_hrtf_id is not None and observed_hrtf_id != variant_paths["output_type"]:
        issues.append(f"hrtf_id mismatch: expected {variant_paths['output_type']} got {observed_hrtf_id}")

    observed_wav_path = metadata_snapshot.get("rendered_wav_path")
    if observed_wav_path is not None and _normalize_path(observed_wav_path) != _normalize_path(variant_paths["rendered_wav_path"]):
        issues.append(
            "rendered_wav_path mismatch: expected "
            f"{_normalize_path(variant_paths['rendered_wav_path'])} got {_normalize_path(observed_wav_path)}"
        )

    observed_metadata_path = metadata_snapshot.get("render_metadata_path")
    expected_metadata_path = variant_paths["render_metadata_path"]
    if (
        observed_metadata_path is not None
        and expected_metadata_path
        and _normalize_path(observed_metadata_path) != _normalize_path(expected_metadata_path)
    ):
        issues.append(
            "render_metadata_path mismatch: expected "
            f"{_normalize_path(expected_metadata_path)} got {_normalize_path(observed_metadata_path)}"
        )

    return issues


def _validate_clarity_result(
    job: ClarityJobManifestRecord,
    observed_outputs: ClarityObservedOutputsRecord,
    result_metadata: dict[str, Any] | None,
) -> list[str]:
    issues: list[str] = []
    if not job["hearing_profile_id"].strip():
        issues.append("hearing_profile_id_missing")
    if not job["input_render_metadata_path"].strip():
        issues.append("input_render_metadata_path_missing")
    if not observed_outputs["expected_output_metadata"]:
        issues.append(f"expected_output_metadata_missing: {job['expected_output_metadata_path']}")
        return issues

    if result_metadata is None:
        issues.append("clarity_result_missing_stable_fields")
        return issues

    issues.extend(_validate_required_string(result_metadata, "schema_version", prefix="clarity_result"))
    issues.extend(_validate_matching_field(result_metadata, "job_id", job["job_id"]))
    issues.extend(_validate_matching_field(result_metadata, "variant_id", job["variant_id"]))
    issues.extend(_validate_matching_field(result_metadata, "scene_id", job["scene_id"]))
    issues.extend(_validate_matching_field(result_metadata, "output_type", job["output_type"]))
    issues.extend(_validate_matching_field(result_metadata, "hearing_profile_id", job["hearing_profile_id"]))
    issues.extend(_validate_matching_field(result_metadata, "input_wav_path", job["input_wav_path"]))
    issues.extend(
        _validate_matching_field(
            result_metadata,
            "input_render_metadata_path",
            job["input_render_metadata_path"],
        )
    )

    result_status = _pick_string(result_metadata.get("status"))
    if result_status is None:
        issues.append("clarity_result_status_missing")
        return issues

    if result_status == "completed":
        if not observed_outputs["expected_output_wav"]:
            issues.append(f"expected_output_wav_missing: {job['expected_output_wav_path']}")
        issues.extend(_validate_matching_field(result_metadata, "output_wav_path", job["expected_output_wav_path"]))
        degradation_applied = _as_dict(result_metadata.get("degradation_applied"))
        if not degradation_applied:
            issues.append("clarity_result_degradation_applied_missing")
        else:
            if not isinstance(degradation_applied.get("left"), dict):
                issues.append("clarity_result_degradation_left_missing")
            if not isinstance(degradation_applied.get("right"), dict):
                issues.append("clarity_result_degradation_right_missing")
    elif result_status == "failed":
        output_wav_path = result_metadata.get("output_wav_path")
        if output_wav_path not in {None, ""}:
            issues.append("clarity_result_failed_output_wav_path_must_be_null")
        error_payload = _as_dict(result_metadata.get("error"))
        issues.extend(_validate_required_string(error_payload, "type", prefix="clarity_result_error"))
        issues.extend(_validate_required_string(error_payload, "message", prefix="clarity_result_error"))
    elif result_status != "partial":
        issues.append(f"clarity_result_status_unexpected: {result_status}")

    return issues


def _derive_clarity_status(
    *,
    attempted: bool,
    resumed: bool,
    observed_outputs: ClarityObservedOutputsRecord,
    result_metadata: dict[str, Any] | None,
    validation_issues: list[str],
) -> ClarityStatus:
    if not attempted and not resumed:
        return "planned"

    if result_metadata is None:
        if observed_outputs["expected_output_wav"] or observed_outputs["expected_output_metadata"]:
            return "partial"
        return "failed"

    result_status = _pick_string(result_metadata.get("status"))
    if result_status == "completed":
        if observed_outputs["expected_output_wav"] and observed_outputs["expected_output_metadata"] and not validation_issues:
            return "skipped" if resumed and not attempted else "completed"
        return "partial"

    if result_status == "failed":
        if observed_outputs["expected_output_wav"]:
            return "partial"
        if not validation_issues:
            return "failed"
        return "partial"

    if result_status == "partial":
        return "partial"

    if observed_outputs["expected_output_wav"] or observed_outputs["expected_output_metadata"]:
        return "partial"
    return "failed"


def _summarize_clarity_batch_status(records: dict[str, ClarityRecord]) -> ClarityBatchStatus:
    if not records:
        return "blocked"

    runnable_records = [record for record in records.values() if record["runnable"]]
    if not runnable_records:
        return "blocked"

    runnable_statuses = {record["status"] for record in runnable_records}
    all_statuses = {record["status"] for record in records.values()}
    terminal_mixed_statuses = {"failed", "partial", "blocked"}

    if any(status == "submitted" for status in runnable_statuses):
        return "submitted"

    if any(status == "planned" for status in runnable_statuses):
        return "planned"

    if runnable_statuses.issubset({"completed", "skipped"}):
        return "completed"

    if runnable_statuses == {"failed"}:
        return "failed"

    if any(status in {"completed", "skipped"} for status in runnable_statuses) and any(
        status in terminal_mixed_statuses for status in all_statuses
    ):
        return "partial"

    if any(status == "partial" for status in runnable_statuses):
        return "partial"

    if any(status == "failed" for status in runnable_statuses):
        return "failed"

    return "partial"


def _validate_required_string(payload: dict[str, Any], field_name: str, *, prefix: str) -> list[str]:
    value = _pick_string(payload.get(field_name))
    if value is None:
        return [f"{prefix}_{field_name}_missing"]
    return []


def _validate_matching_field(payload: dict[str, Any], field_name: str, expected_value: str) -> list[str]:
    observed_value = _pick_string(payload.get(field_name))
    if observed_value is None:
        return [f"clarity_result_{field_name}_missing"]
    if _normalize_path_if_possible(observed_value) != _normalize_path_if_possible(expected_value):
        return [f"clarity_result_{field_name}_mismatch: expected {expected_value} got {observed_value}"]
    return []


def _normalize_path_if_possible(value: str) -> str:
    if value.startswith(".") or "/" in value or "\\" in value:
        return _normalize_path(value)
    return value


def _pick_string(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _as_dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _normalize_path(path_value: str) -> str:
    return Path(path_value).resolve().as_posix()


def _path_exists(path_value: object) -> bool:
    return isinstance(path_value, str) and Path(path_value).is_file()


def _now_isoformat() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _index_write_lock(index_path: Path) -> Iterator[None]:
    resolved_path = index_path.resolve()
    with _INDEX_LOCKS_GUARD:
        lock = _INDEX_LOCKS.setdefault(resolved_path, threading.Lock())
    with lock:
        yield


def _index_temp_path(index_path: Path) -> Path:
    return index_path.with_name(
        f".{index_path.name}.{os.getpid()}.{threading.get_ident()}.{uuid4().hex}.tmp"
    )


def _replace_index(temp_path: Path, index_path: Path) -> None:
    for attempt in range(_INDEX_REPLACE_ATTEMPTS):
        try:
            temp_path.replace(index_path)
            return
        except PermissionError:
            if attempt == _INDEX_REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_INDEX_REPLACE_RETRY_DELAY_S)


def _upsert_jsonl(index_path: Path, records: dict[str, Any], key: str, record: Any) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with _index_write_lock(index_path):
        records[key] = record
        temp_path = _index_temp_path(index_path)
        lines = [json.dumps(records[current_key], sort_keys=True) for current_key in sorted(records)]
        try:
            temp_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            _replace_index(temp_path, index_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
