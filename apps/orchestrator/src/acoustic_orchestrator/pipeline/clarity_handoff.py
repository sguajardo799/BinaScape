from pathlib import Path
from typing import TypedDict

import yaml

from acoustic_orchestrator.config.validator import load_hearing_profile_catalog
from acoustic_orchestrator.config.models import AppConfig
from acoustic_orchestrator.pipeline.clarity_runner import submit_clarity_manifest
from acoustic_orchestrator.pipeline.output_index import (
    ClarityBackendInvocationRecord,
    ClarityJobManifestRecord,
    ClarityRecord,
    ClarityStatus,
    VariantRecord,
    build_clarity_record,
    inspect_clarity_record,
    load_clarity_index,
    load_variant_index,
    should_resume_clarity_job,
    summarize_clarity_index,
    upsert_clarity_record,
    write_clarity_job_manifest,
)
from acoustic_orchestrator.pipeline.output_paths import (
    ArtifactLayout,
    resolve_artifact_layout,
    resolve_clarity_job_paths,
)


CLARITY_SCHEMA_VERSION = "1.0"


class ClaritySummary(TypedDict):
    enabled: bool
    batch_status: str
    auto_submit: bool
    submitted: bool
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
    manifest_path: str
    index_path: str
    output_root: str
    backend_command: list[str] | None
    message: str


def prepare_clarity_handoff(
    config: AppConfig,
    *,
    submit: bool | None = None,
) -> ClaritySummary:
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    submit_enabled = config.hearing_degradation.runner.auto_submit if submit is None else submit

    if not config.hearing_degradation.enabled:
        return _build_summary(
            records={},
            layout=layout,
            config=config,
            submitted=False,
            auto_submit=submit_enabled,
            backend_command=None,
            message="Handoff de Clarity deshabilitado en la configuración",
        )

    render_records = load_variant_index(layout["render_index_path"])
    clarity_records = dict(load_clarity_index(layout["clarity_index_path"]))
    planned_jobs: list[ClarityJobManifestRecord] = []
    try:
        hearing_profiles = load_hearing_profile_catalog(config.hearing_degradation.hearing_profiles_path)
    except ValueError as exc:
        for variant_id in sorted(render_records):
            variant_record = render_records[variant_id]
            for hearing_profile_id in _read_profile_ids_for_blocked_records(config.hearing_degradation.hearing_profiles_path):
                job = build_clarity_job_record(
                    config=config,
                    layout=layout,
                    variant_record=variant_record,
                    hearing_profile_id=hearing_profile_id,
                )
                upsert_clarity_record(
                    layout["clarity_index_path"],
                    clarity_records,
                    build_clarity_record(
                        job,
                        manifest_path=layout["clarity_jobs_path"],
                        status="blocked",
                        attempted=False,
                        resumed=False,
                        runnable=False,
                        validation_issues=[f"invalid_hearing_profiles_catalog:{exc}"],
                    ),
                )
        write_clarity_job_manifest(layout["clarity_jobs_path"], [])
        return _build_summary(
            records=clarity_records,
            layout=layout,
            config=config,
            submitted=False,
            auto_submit=submit_enabled,
            backend_command=None,
            message=f"Catálogo de hearing profiles inválido: {exc}",
        )

    for variant_id in sorted(render_records):
        variant_record = render_records[variant_id]
        manifest_path = layout["clarity_jobs_path"]
        for hearing_profile in hearing_profiles:
            job = build_clarity_job_record(
                config=config,
                layout=layout,
                variant_record=variant_record,
                hearing_profile_id=hearing_profile["hearing_profile_id"],
            )
            eligibility_issues = get_clarity_job_issues(
                config=config,
                variant_record=variant_record,
                job=job,
            )

            if should_resume_clarity_job(clarity_records, job, manifest_path=manifest_path):
                upsert_clarity_record(
                    layout["clarity_index_path"],
                    clarity_records,
                    inspect_clarity_record(
                        job,
                        manifest_path=manifest_path,
                        attempted=False,
                        resumed=True,
                    ),
                )
                continue

            if eligibility_issues:
                status: ClarityStatus = "blocked" if _is_blocking_issue(eligibility_issues) else "skipped"
                upsert_clarity_record(
                    layout["clarity_index_path"],
                    clarity_records,
                    build_clarity_record(
                        job,
                        manifest_path=manifest_path,
                        status=status,
                        attempted=False,
                        resumed=False,
                        runnable=False,
                        validation_issues=eligibility_issues,
                    ),
                )
                continue

            planned_jobs.append(job)
            upsert_clarity_record(
                layout["clarity_index_path"],
                clarity_records,
                build_clarity_record(
                    job,
                    manifest_path=manifest_path,
                    status="planned",
                    attempted=False,
                    resumed=False,
                    runnable=True,
                ),
            )

    write_clarity_job_manifest(layout["clarity_jobs_path"], planned_jobs)

    if not submit_enabled or not planned_jobs:
        return _build_summary(
            records=clarity_records,
            layout=layout,
            config=config,
            submitted=False,
            auto_submit=submit_enabled,
            backend_command=None,
            message=(
                "Jobs de Clarity preparados" if planned_jobs else "No hay jobs de Clarity pendientes para preparar o reenviar"
            ),
        )

    submission = submit_clarity_manifest(config.hearing_degradation.runner, layout["clarity_jobs_path"])
    if submission["status"] == "blocked":
        for job in planned_jobs:
            upsert_clarity_record(
                layout["clarity_index_path"],
                clarity_records,
                build_clarity_record(
                    job,
                    manifest_path=layout["clarity_jobs_path"],
                    status="blocked",
                    attempted=False,
                    resumed=False,
                    runnable=False,
                    validation_issues=[submission["message"]],
                ),
            )
        return _build_summary(
            records=clarity_records,
            layout=layout,
            config=config,
            submitted=False,
            auto_submit=submit_enabled,
            backend_command=submission["command"],
            message=submission["message"],
        )

    for job in planned_jobs:
        upsert_clarity_record(
            layout["clarity_index_path"],
            clarity_records,
            build_clarity_record(
                job,
                manifest_path=layout["clarity_jobs_path"],
                status="submitted",
                attempted=True,
                resumed=False,
                runnable=True,
                backend_command=submission["command"],
            ),
        )

    for job in planned_jobs:
        upsert_clarity_record(
            layout["clarity_index_path"],
            clarity_records,
            inspect_clarity_record(
                job,
                manifest_path=layout["clarity_jobs_path"],
                attempted=True,
                resumed=False,
                backend_command=submission["command"],
            ),
        )

    return _build_summary(
        records=clarity_records,
        layout=layout,
        config=config,
        submitted=submission["status"] == "submitted",
        auto_submit=submit_enabled,
        backend_command=submission["command"],
        message=submission["message"],
    )


def build_clarity_job_record(
    *,
    config: AppConfig,
    layout: ArtifactLayout,
    variant_record: VariantRecord,
    hearing_profile_id: str,
) -> ClarityJobManifestRecord:
    clarity_paths = resolve_clarity_job_paths(
        output_root=config.hearing_degradation.output_dir or layout["degraded_output_root"],
        output_type=variant_record["output_type"],
        variant_id=variant_record["variant_id"],
        hearing_profile_id=hearing_profile_id,
        input_wav_path=variant_record["rendered_wav_path"],
    )
    return {
        "schema_version": CLARITY_SCHEMA_VERSION,
        "run_id": config.outputs.run_name or config.experiment.experiment_id,
        "job_id": clarity_paths["job_id"],
        "variant_id": variant_record["variant_id"],
        "scene_id": variant_record["scene_id"],
        "output_type": variant_record["output_type"],
        "hearing_profile_id": hearing_profile_id,
        "hearing_profiles_path": config.hearing_degradation.hearing_profiles_path.resolve().as_posix(),
        "backend_invocation": _build_backend_invocation_metadata(config),
        "input_wav_path": variant_record["rendered_wav_path"],
        "input_render_metadata_path": variant_record["render_metadata_path"] or "",
        "output_dir": clarity_paths["output_dir"],
        "expected_output_wav_path": clarity_paths["expected_output_wav_path"],
        "expected_output_metadata_path": clarity_paths["expected_output_metadata_path"],
    }


def get_clarity_job_issues(
    *,
    config: AppConfig,
    variant_record: VariantRecord,
    job: ClarityJobManifestRecord,
) -> list[str]:
    issues: list[str] = []

    if variant_record["output_type"] not in config.hearing_degradation.input_targets:
        issues.append("output_type_not_targeted")

    if variant_record["status"] != "completed":
        issues.append(f"render_variant_not_completed:{variant_record['status']}")

    if not variant_record["validation"]["ok"]:
        issues.extend(f"render_validation:{issue}" for issue in variant_record["validation"]["issues"])

    if issues:
        return issues

    if not variant_record["scene_id"].strip():
        issues.append("missing_scene_id")
    if not variant_record["variant_id"].strip():
        issues.append("missing_variant_id")
    if not Path(job["input_wav_path"]).is_file():
        issues.append(f"missing_input_wav:{job['input_wav_path']}")

    metadata_path = job["input_render_metadata_path"]
    if not metadata_path.strip():
        issues.append("missing_input_render_metadata")
    elif not Path(metadata_path).is_file():
        issues.append(f"missing_input_render_metadata:{metadata_path}")

    if not Path(job["hearing_profiles_path"]).is_file():
        issues.append(f"missing_hearing_profiles:{job['hearing_profiles_path']}")

    return issues


def _build_summary(
    *,
    records: dict[str, ClarityRecord],
    layout: ArtifactLayout,
    config: AppConfig,
    submitted: bool,
    auto_submit: bool,
    backend_command: list[str] | None,
    message: str,
) -> ClaritySummary:
    index_summary = summarize_clarity_index(records)
    return {
        "enabled": config.hearing_degradation.enabled,
        "batch_status": str(index_summary["batch_status"]),
        "auto_submit": auto_submit,
        "submitted": submitted,
        "total_jobs": index_summary["total_jobs"],
        "planned_jobs": index_summary["planned_jobs"],
        "submitted_jobs": index_summary["submitted_jobs"],
        "completed_jobs": index_summary["completed_jobs"],
        "partial_jobs": index_summary["partial_jobs"],
        "failed_jobs": index_summary["failed_jobs"],
        "blocked_jobs": index_summary["blocked_jobs"],
        "skipped_jobs": index_summary["skipped_jobs"],
        "resumed_jobs": index_summary["resumed_jobs"],
        "invalid_jobs": index_summary["invalid_jobs"],
        "manifest_path": layout["clarity_jobs_path"].resolve().as_posix(),
        "index_path": layout["clarity_index_path"].resolve().as_posix(),
        "output_root": (config.hearing_degradation.output_dir or layout["degraded_output_root"]).resolve().as_posix(),
        "backend_command": backend_command,
        "message": message,
    }


def _build_backend_invocation_metadata(config: AppConfig) -> ClarityBackendInvocationRecord:
    backend_project_path = config.hearing_degradation.runner.backend_project_path
    return {
        "mode": "uv_project" if config.hearing_degradation.runner.use_uv else "direct",
        "entrypoint": config.hearing_degradation.runner.entrypoint,
        "use_uv": config.hearing_degradation.runner.use_uv,
        "backend_project_path": backend_project_path.resolve().as_posix() if backend_project_path is not None else None,
    }


def _is_blocking_issue(issues: list[str]) -> bool:
    return any(
        issue.startswith(prefix)
        for issue in issues
        for prefix in [
            "missing_scene_id",
            "missing_variant_id",
            "missing_input_wav",
            "missing_input_render_metadata",
            "missing_hearing_profiles",
        ]
    )


def _read_profile_ids_for_blocked_records(hearing_profiles_path: Path) -> list[str]:
    try:
        raw_catalog = yaml.safe_load(hearing_profiles_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []

    if not isinstance(raw_catalog, dict):
        return []

    raw_profiles = raw_catalog.get("profiles")
    if not isinstance(raw_profiles, list):
        return []

    seen_profile_ids: set[str] = set()
    profile_ids: list[str] = []
    for raw_profile in raw_profiles:
        if not isinstance(raw_profile, dict):
            continue
        hearing_profile_id = raw_profile.get("hearing_profile_id")
        if not isinstance(hearing_profile_id, str):
            continue
        normalized_profile_id = hearing_profile_id.strip()
        if not normalized_profile_id or normalized_profile_id in seen_profile_ids:
            continue
        seen_profile_ids.add(normalized_profile_id)
        profile_ids.append(normalized_profile_id)

    return profile_ids
