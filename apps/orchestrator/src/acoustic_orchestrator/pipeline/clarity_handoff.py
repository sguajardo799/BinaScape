import json
from pathlib import Path
from typing import Any, TypedDict

import yaml

from acoustic_orchestrator.config.validator import load_hearing_profile_catalog
from acoustic_orchestrator.config.models import AppConfig
from acoustic_orchestrator.pipeline.clarity_runner import submit_clarity_manifest
from acoustic_orchestrator.pipeline.progress import ProgressReporter
from acoustic_orchestrator.pipeline.clarity_eligibility import is_clarity_output_eligible
from acoustic_orchestrator.pipeline.level_calibration import (
    CALIBRATION_UNDEFINED_SILENT_INPUT,
    RenderCalibrationRecord,
    compute_render_calibration,
    effective_calibration,
    effective_outer_middle_ear,
    load_level_calibration_index,
    upsert_level_calibration_records,
    uses_level_range,
)
from acoustic_orchestrator.pipeline.matlab_runner import build_single_receiver_render_manifest, receiver_variants
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_history import RenderHistory, load_render_history, resume_evidence
from acoustic_orchestrator.pipeline.render_validation import equivalent_render_inputs, validate_render
from acoustic_orchestrator.pipeline.output_index import (
    ClarityBackendInvocationRecord,
    ClarityJobManifestRecord,
    ClarityRecord,
    ClarityStatus,
    VariantRecord,
    _upsert_many_jsonl,
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
    get_receiver_output_config,
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
    progress: ProgressReporter | None = None,
) -> ClaritySummary:
    progress = progress or ProgressReporter()
    progress.begin("Clarity preparacion" if config.hearing_degradation.enabled else "Clarity deshabilitado")
    summary = _prepare_clarity_handoff(config, submit=submit, progress=progress)
    progress.counters.update({
        "planificados": summary["planned_jobs"], "reutilizados": summary["resumed_jobs"],
        "bloqueados": summary["blocked_jobs"], "omitidos": summary["skipped_jobs"],
    })
    message = summary["message"]
    if not summary["submitted"] and summary["planned_jobs"] and not summary["auto_submit"]:
        message += "; ejecucion pendiente"
    progress.finish(message)
    return summary


def _prepare_clarity_handoff(
    config: AppConfig, *, submit: bool | None, progress: ProgressReporter,
) -> ClaritySummary:
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    submit_enabled = config.hearing_degradation.runner.auto_submit if submit is None else submit
    resume_enabled = config.execution.resume_if_possible and not config.hearing_degradation.runner.force_rerun

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
    render_history = load_render_history(layout["render_attempts_dir"])
    clarity_records = dict(load_clarity_index(layout["clarity_index_path"]))
    _drop_legacy_layout_records(layout["clarity_index_path"], clarity_records)
    calibration_records = load_level_calibration_index(layout["level_calibration_index_path"])
    processors = config.hearing_degradation.processors
    planned_jobs: list[ClarityJobManifestRecord] = []
    try:
        hearing_profiles = load_hearing_profile_catalog(
            config.hearing_degradation.hearing_profiles_path, processors=processors,
        )
    except ValueError as exc:
        for variant_id in sorted(render_records):
            variant_record = render_records[variant_id]
            if not is_clarity_output_eligible(variant_record["output_type"]):
                continue
            profile_ids = _read_profile_ids_for_blocked_records(config.hearing_degradation.hearing_profiles_path)
            for hearing_profile_id, processor in ((p, proc) for p in profile_ids for proc in processors):
                job = build_clarity_job_record(
                    config=config,
                    layout=layout,
                    variant_record=variant_record,
                    hearing_profile_id=hearing_profile_id,
                    processor=processor,
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

    new_calibrations: list[RenderCalibrationRecord] = []
    for variant_id in sorted(render_records):
        variant_record = render_records[variant_id]
        if not is_clarity_output_eligible(variant_record["output_type"]):
            continue
        manifest_path = layout["clarity_jobs_path"]
        revalidation_issues = _revalidate_render_input(config, variant_record, render_history)
        # K is computed once per render and shared unchanged by every profile and processor.
        calibration, calibration_issue = _calibrate_render(config, variant_record, revalidation_issues)
        if calibration is not None:
            new_calibrations.append(calibration)
        for hearing_profile, processor in ((p, proc) for p in hearing_profiles for proc in processors):
            job = build_clarity_job_record(
                config=config,
                layout=layout,
                variant_record=variant_record,
                hearing_profile_id=hearing_profile["hearing_profile_id"],
                processor=processor,
                calibration=calibration,
            )
            eligibility_issues = get_clarity_job_issues(
                config=config,
                variant_record=variant_record,
                job=job,
                revalidation_issues=revalidation_issues,
            )
            if not eligibility_issues and calibration_issue is not None:
                eligibility_issues = [calibration_issue]

            if not eligibility_issues and resume_enabled and should_resume_clarity_job(clarity_records, job, manifest_path=manifest_path):
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

    upsert_level_calibration_records(layout["level_calibration_index_path"], calibration_records, new_calibrations)
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

    # Planning can involve many profiles and index writes. Check disk evidence
    # again at the submission boundary, not only before creating the jobs.
    progress.finish("jobs preparados")
    progress.begin("Clarity revalidando entradas")
    current_render_records = load_variant_index(layout["render_index_path"])
    current_history = load_render_history(layout["render_attempts_dir"])
    checked_inputs: dict[str, list[str]] = {}
    valid_jobs = []
    for job in planned_jobs:
        variant = current_render_records.get(job["variant_id"])
        if variant is None:
            input_issues = ["render_revalidation:index_record_missing"]
        else:
            if job["variant_id"] not in checked_inputs:
                checked_inputs[job["variant_id"]] = _revalidate_render_input(config, variant, current_history)
            input_issues = get_clarity_job_issues(
                config=config, variant_record=variant, job=job,
                revalidation_issues=checked_inputs[job["variant_id"]],
            )
        if input_issues:
            upsert_clarity_record(
                layout["clarity_index_path"], clarity_records,
                build_clarity_record(job, manifest_path=layout["clarity_jobs_path"], status="blocked",
                                    attempted=False, resumed=False, runnable=False, validation_issues=input_issues),
            )
        else:
            valid_jobs.append(job)
    if len(valid_jobs) != len(planned_jobs):
        planned_jobs = valid_jobs
        write_clarity_job_manifest(layout["clarity_jobs_path"], planned_jobs)
    if not planned_jobs:
        return _build_summary(records=clarity_records, layout=layout, config=config, submitted=False,
                              auto_submit=submit_enabled, backend_command=None,
                              message="Entradas de render rechazadas al revalidar antes del envío")

    submission = submit_clarity_manifest(config.hearing_degradation.runner, layout["clarity_jobs_path"], progress=progress)
    withheld = submission.get("blocked_jobs") or {}
    for job in planned_jobs:
        if job["job_id"] in withheld:
            upsert_clarity_record(
                layout["clarity_index_path"], clarity_records,
                build_clarity_record(job, manifest_path=layout["clarity_jobs_path"], status="blocked", attempted=False,
                                     resumed=False, runnable=False, validation_issues=[withheld[job["job_id"]]]),
            )
    planned_jobs = [job for job in planned_jobs if job["job_id"] not in withheld]
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
    processor: str = "msbg",
    calibration: RenderCalibrationRecord | None = None,
) -> ClarityJobManifestRecord:
    clarity_paths = resolve_clarity_job_paths(
        output_root=config.hearing_degradation.output_dir or layout["degraded_output_root"],
        processor=processor,
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
        "processor": processor,
        "calibration": calibration,
        "outer_middle_ear": effective_outer_middle_ear(config.hearing_degradation),
        "processor_config": build_processor_config(config, processor),
        "processing_signature": build_processing_signature(config, processor),
    }


def build_processor_config(config: AppConfig, processor: str) -> dict[str, Any]:
    hearing = config.hearing_degradation
    if processor == "whis":
        if hearing.whis is None:
            raise ValueError("hearing_degradation.whis es obligatorio para planificar jobs whis")
        return {
            "install_root": hearing.whis.install_root.resolve().as_posix(),
            "matlab_executable": hearing.whis.matlab_executable,
            "synth_method": hearing.whis.synth_method,
        }
    return {"level_handling": hearing.msbg.level_handling if hearing.msbg is not None else "true_level"}


def build_processing_signature(config: AppConfig, processor: str) -> dict[str, Any]:
    """Everything that changes a processor's output for the same render audio (design D9)."""
    calibration = effective_calibration(config.hearing_degradation)
    processor_config = build_processor_config(config, processor)
    signature: dict[str, Any] = {
        "processor": processor,
        "calibration": dict(calibration),
        "random_seed": config.experiment.random_seed if uses_level_range(calibration) else None,
        "outer_middle_ear": effective_outer_middle_ear(config.hearing_degradation),
    }
    if processor == "whis":
        signature["whis"] = {"synth_method": processor_config["synth_method"]}
    else:
        signature["msbg"] = {"level_handling": processor_config["level_handling"]}
    return json.loads(json.dumps(signature, sort_keys=True))


def _calibrate_render(
    config: AppConfig, variant_record: VariantRecord, revalidation_issues: list[str],
) -> tuple[RenderCalibrationRecord | None, str | None]:
    """Return the render calibration, or the blocking issue that prevents it."""
    wav_path = variant_record["rendered_wav_path"]
    if revalidation_issues or variant_record["status"] != "completed" or not wav_path or not Path(wav_path).is_file():
        return None, None  # the job is already ineligible for another, reported reason
    try:
        record = compute_render_calibration(
            config.hearing_degradation,
            random_seed=config.experiment.random_seed,
            variant_id=variant_record["variant_id"],
            input_wav_path=wav_path,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return None, f"calibration_unreadable_input:{exc}"
    if record["status"] != "ok":
        return record, CALIBRATION_UNDEFINED_SILENT_INPUT
    return record, None


def _drop_legacy_layout_records(index_path: Path, records: dict[str, ClarityRecord]) -> None:
    """Records from the layout before per-processor directories are never resumed (design D6)."""
    legacy_ids = {job_id for job_id, record in records.items() if record["processor"] is None}
    if legacy_ids:
        kept = {job_id: record for job_id, record in records.items() if job_id not in legacy_ids}
        records.clear()
        _upsert_many_jsonl(index_path, records, kept)


def get_clarity_job_issues(
    *,
    config: AppConfig,
    variant_record: VariantRecord,
    job: ClarityJobManifestRecord,
    revalidation_issues: list[str] | None = None,
) -> list[str]:
    issues: list[str] = []

    output_type = variant_record["output_type"]
    if not is_clarity_output_eligible(output_type) or not is_clarity_output_eligible(job["output_type"]):
        issues.append(f"output_type_ineligible:{output_type}")
    if "__" in output_type:
        base_type, suffix = output_type.rsplit("__", 1)
        if suffix.isdecimal():
            output_type = base_type
    if output_type not in config.hearing_degradation.input_targets:
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

    for field_name, input_path, expected_path in (
        ("input_wav_path", job["input_wav_path"], variant_record["rendered_wav_path"]),
        ("input_render_metadata_path", job["input_render_metadata_path"], variant_record["render_metadata_path"]),
    ):
        try:
            if not expected_path or Path(input_path).resolve() != Path(expected_path).resolve():
                issues.append(f"render_revalidation:job_{field_name}_mismatch")
        except (OSError, ValueError):
            issues.append(f"render_revalidation:job_{field_name}_invalid")
    for field_name, job_value, render_value in (
        ("variant_id", job["variant_id"], variant_record["variant_id"]),
        ("scene_id", job["scene_id"], variant_record["scene_id"]),
        ("output_type", job["output_type"], variant_record["output_type"]),
    ):
        if job_value != render_value:
            issues.append(f"render_revalidation:job_{field_name}_mismatch")
    if not issues:
        issues.extend(revalidation_issues if revalidation_issues is not None else _revalidate_render_input(config, variant_record))

    return issues


def _revalidate_render_input(
    config: AppConfig, record: VariantRecord, history: RenderHistory | None = None,
) -> list[str]:
    """Read current input evidence once per variant, not once per hearing profile."""
    validation = validate_render(record, metadata_required=True, profile="reuse")
    issues = [f"render_revalidation:{issue}" for issue in validation.as_record()["issues"]]
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    expected_manifest = None
    if not issues:
        try:
            current = read_json_object(Path(record["runtime_manifest_path"]))
            scene_path = layout["scene_manifest_dir"] / f"{record['scene_id']}.json"
            if scene_path.is_file():
                scene = read_json_object(scene_path)
                descriptor = next(
                    item for item in receiver_variants(scene)
                    if item.get("hrtf_id", item.get("output_type")) == record["output_type"]
                )
                prepared_root = (layout["prepared_audio_dir"] / record["scene_id"]).resolve()
                runtime_sources = current["sources"]
                runtime_sources = runtime_sources if isinstance(runtime_sources, list) else [runtime_sources]
                rewrites = {source["source_id"]: source["audio_path"] for source in runtime_sources
                            if Path(source["audio_path"]).resolve().is_relative_to(prepared_root)}
                _, expected_manifest = build_single_receiver_render_manifest(
                    scene, descriptor, layout["runtime_manifest_dir"], config.outputs,
                    get_receiver_output_config(config, record["output_type"]), rewrites,
                )
                if not equivalent_render_inputs(expected_manifest, current):
                    issues.append("render_revalidation:current_scene_mismatch")
        except (OSError, ValueError, TypeError, KeyError, StopIteration) as exc:
            issues.append(f"render_revalidation:runtime_input_unverifiable:{exc}")
    if history is None:
        history = load_render_history(layout["render_attempts_dir"])
    allowed, _ = resume_evidence(history, record, record, expected_manifest)
    if not allowed:
        issues.append("render_history_not_accepted")
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
            "calibration_",
            "render_revalidation:",
            "render_history_not_accepted",
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
