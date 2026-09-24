import random
import json
import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal, TypedDict

from acoustic_orchestrator.config.models import AppConfig, ReceiverOutputConfig
from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.validator import validate_config
from acoustic_orchestrator.experiment.manifest_writer import write_manifest
from acoustic_orchestrator.experiment.sampler import SceneSamplingFailure, build_background_noise_plan, sample_static_scene
from acoustic_orchestrator.experiment.reverberation_sampling import build_bins, build_global_plan, summarize
from acoustic_orchestrator.experiment.reverberation_sampling import PROPOSAL_STRATEGY_VERSION
from acoustic_orchestrator.experiment.treatment_catalog import MIX_MODEL, MIX_MODEL_VERSION
from acoustic_orchestrator.experiment.scene_builder import build_static_manifest
from acoustic_orchestrator.pipeline.clarity_handoff import ClaritySummary, prepare_clarity_handoff
from acoustic_orchestrator.pipeline.matlab_runner import (
    RenderVariantPaths,
    build_render_variant_paths,
    render_manifest_with_matlab,
    write_single_hrtf_render_manifest,
)
from acoustic_orchestrator.pipeline.output_index import (
    build_planned_variant_record,
    inspect_variant,
    load_variant_index,
    should_resume_variant,
    summarize_variant_index,
    upsert_variant,
)
from acoustic_orchestrator.pipeline.output_paths import get_receiver_output_config, resolve_artifact_layout
from acoustic_orchestrator.pipeline.runtime_audio import prepare_scene_source_assets


class RenderStaticRunError(RuntimeError):
    def __init__(self, *, manifest_paths: list[Path], summary: "RenderSummary", cause: Exception) -> None:
        super().__init__(str(cause))
        self.manifest_paths = manifest_paths
        self.summary = summary
        self.__cause__ = cause


class RenderSummary(TypedDict):
    total_variants: int
    planned_variants: int
    completed_variants: int
    partial_variants: int
    failed_variants: int
    resumed_variants: int
    inconsistent_variants: int
    render_jobs: int
    index_path: str
    sampling: "SamplingSummary"
    clarity: ClaritySummary | None


class SamplingSummary(TypedDict):
    requested_scenes: int
    generated_scenes: int
    failed_scenes: int
    failed_scene_indices: list[int]
    status: Literal["complete", "complete_with_deviation", "partial", "failed"]
    failures_path: str
    reverberation_batch: dict


def generate_static_manifests(config_path: str | Path) -> list[Path]:
    manifest_paths, _ = generate_static_manifests_with_summary(config_path)
    return manifest_paths


def generate_static_manifests_with_summary(config_path: str | Path) -> tuple[list[Path], SamplingSummary]:
    _, manifest_paths, sampling_summary = _prepare_static_manifests(config_path)
    return manifest_paths, sampling_summary


def render_static_scenes(config_path: str | Path, matlab_executable: str = "matlab") -> tuple[list[Path], RenderSummary]:
    config, manifest_paths, sampling_summary = _prepare_static_manifests(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    runtime_manifest_dir = layout["runtime_manifest_dir"]
    index_path = layout["render_index_path"]
    existing_records = load_variant_index(index_path)
    variant_records = dict(existing_records)
    metadata_required = config.execution.save_render_metadata
    # Publish the complete plan before the first render while still enforcing
    # canonical-first execution below.
    for planned_variant in _build_planned_variants(config, manifest_paths, runtime_manifest_dir):
        upsert_variant(
            index_path,
            variant_records,
            build_planned_variant_record(
                planned_variant["variant_paths"],
                metadata_required=metadata_required,
            ),
        )
    render_jobs = 0
    accepted_manifest_paths: list[Path] = []
    render_tasks: list[RenderTask] = []
    prepared_source_paths_by_scene: dict[str, dict[str, str]] = {}
    first_render_error: Exception | None = None

    for manifest_path in manifest_paths:
        scene_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        scene_index = int(scene_manifest["reverberation_sampling"]["scene_index"])
        raven_rejections: list[dict] = []

        while True:
            planned_variants = _build_planned_variants_from_manifest(
                config, scene_manifest, runtime_manifest_dir
            )
            for planned_variant in planned_variants:
                upsert_variant(
                    index_path,
                    variant_records,
                    build_planned_variant_record(
                        planned_variant["variant_paths"],
                        metadata_required=metadata_required,
                    ),
                )

            prepared_source_paths = prepared_source_paths_by_scene.get(scene_manifest["scene_id"])
            if prepared_source_paths is None:
                prepared_source_paths = prepare_scene_source_assets(
                    scene_manifest,
                    layout["prepared_audio_dir"] / scene_manifest["scene_id"],
                )
                prepared_source_paths_by_scene[scene_manifest["scene_id"]] = prepared_source_paths

            prepared_tasks = [
                _prepare_render_task(
                    config,
                    planned_variant,
                    runtime_manifest_dir,
                    prepared_source_paths,
                )
                for planned_variant in planned_variants
            ]
            canonical_task = prepared_tasks[0]
            canonical_paths = canonical_task["variant_paths"]
            resumed = config.execution.resume_if_possible and should_resume_variant(
                existing_records,
                canonical_paths,
                metadata_required=True,
            )
            if resumed:
                canonical_record = inspect_variant(
                    canonical_paths,
                    metadata_required=True,
                    attempted=False,
                    resumed=True,
                )
                upsert_variant(index_path, variant_records, canonical_record)
            else:
                render_jobs += 1
                result = _run_single_render_task(canonical_task, matlab_executable=matlab_executable)
                _upsert_attempted_render_result(
                    result,
                    metadata_required=metadata_required,
                    index_path=index_path,
                    variant_records=variant_records,
                )
                if result["error"] is not None:
                    first_render_error = result["error"]
                    break
                canonical_record = variant_records[canonical_paths["variant_id"]]

            t30_diagnostic = _validate_canonical_raven_t30(canonical_paths)
            if t30_diagnostic is None:
                scene_manifest["reverberation_sampling"]["raven_t30_rejections"] = raven_rejections
                scene_manifest["reverberation_sampling"]["raven_retry_count"] = len(raven_rejections)
                write_manifest(
                    scene_manifest=scene_manifest,
                    manifest_path=manifest_path,
                    overwrite=True,
                )
                accepted_manifest_paths.append(manifest_path)
                for task in prepared_tasks[1:]:
                    variant_paths = task["variant_paths"]
                    if config.execution.resume_if_possible and should_resume_variant(
                        existing_records,
                        variant_paths,
                        metadata_required=metadata_required,
                    ):
                        upsert_variant(
                            index_path,
                            variant_records,
                            inspect_variant(
                                variant_paths,
                                metadata_required=metadata_required,
                                attempted=False,
                                resumed=True,
                            ),
                        )
                    else:
                        render_tasks.append(task)
                break

            t30_diagnostic.update(
                {
                    "scene_index": scene_index,
                    "scene_id": scene_manifest["scene_id"],
                    "hrtf_id": planned_variants[0]["hrtf"]["hrtf_id"],
                    "render_status": canonical_record["status"],
                    "sampling_attempt": scene_manifest["reverberation_sampling"]["attempts"],
                }
            )
            raven_rejections.append(t30_diagnostic)
            _discard_variant_outputs(canonical_paths)
            upsert_variant(
                index_path,
                variant_records,
                inspect_variant(
                    canonical_paths,
                    metadata_required=metadata_required,
                    attempted=True,
                ),
            )

            attempts_used = int(scene_manifest["reverberation_sampling"]["attempts"])
            try:
                sampled_scene = sample_static_scene(
                    config,
                    None,
                    scene_index,
                    start_scene_attempt=attempts_used + 1,
                    prior_rejections={
                        **scene_manifest["reverberation_sampling"].get("rejections_by_reason", {}),
                        "raven_t30_invalid": len(raven_rejections),
                    },
                )
            except SceneSamplingFailure as exc:
                _append_raven_sampling_failure(
                    sampling_summary,
                    scene_manifest,
                    exc.diagnostics,
                    raven_rejections,
                )
                _discard_failed_scene_variants(
                    planned_variants,
                    metadata_required=metadata_required,
                    index_path=index_path,
                    variant_records=variant_records,
                )
                manifest_path.unlink(missing_ok=True)
                break

            sampled_scene["background_noise"] = scene_manifest.get("background_noise")
            scene_manifest = build_static_manifest(config, sampled_scene, scene_index, manifest_path)
            scene_manifest["reverberation_sampling"]["raven_t30_rejections"] = list(raven_rejections)
            prepared_source_paths_by_scene.pop(scene_manifest["scene_id"], None)

        if first_render_error is not None:
            break

    manifest_paths = accepted_manifest_paths
    _refresh_reverberation_batch(config, manifest_paths, sampling_summary)
    if first_render_error is not None:
        raise RenderStaticRunError(
            manifest_paths=manifest_paths,
            summary=_build_render_summary(
                variant_records,
                render_jobs,
                index_path,
                sampling_summary=sampling_summary,
                clarity_summary=None,
            ),
            cause=first_render_error,
        ) from first_render_error

    render_jobs += len(render_tasks)
    first_render_error = _run_render_tasks(
        render_tasks,
        num_workers=config.execution.num_workers,
        matlab_executable=matlab_executable,
        metadata_required=metadata_required,
        index_path=index_path,
        variant_records=variant_records,
    )
    if first_render_error is not None:
        raise RenderStaticRunError(
            manifest_paths=manifest_paths,
            summary=_build_render_summary(
                variant_records,
                render_jobs,
                index_path,
                sampling_summary=sampling_summary,
                clarity_summary=None,
            ),
            cause=first_render_error,
        ) from first_render_error

    clarity_summary = None
    if config.hearing_degradation.enabled:
        clarity_summary = prepare_clarity_handoff(config)

    return manifest_paths, _build_render_summary(
        variant_records,
        render_jobs,
        index_path,
        sampling_summary=sampling_summary,
        clarity_summary=clarity_summary,
    )


def run_clarity_handoff(config_path: str | Path, *, submit: bool | None = None) -> ClaritySummary:
    config = load_config(config_path)
    validate_config(config)
    return prepare_clarity_handoff(config, submit=submit)


class PlannedVariant(TypedDict):
    scene_manifest: dict
    hrtf: dict
    receiver_output: ReceiverOutputConfig
    variant_paths: RenderVariantPaths


class RenderTask(TypedDict):
    runtime_manifest_path: Path
    variant_paths: RenderVariantPaths


class RenderResult(TypedDict):
    task: RenderTask
    error: Exception | None


RAVEN_OCTAVE_FREQUENCIES_HZ = (31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)


def _build_planned_variants_from_manifest(
    config: AppConfig,
    scene_manifest: dict,
    runtime_manifest_dir: Path,
) -> list[PlannedVariant]:
    planned_variants: list[PlannedVariant] = []
    for hrtf in sorted(scene_manifest["receiver"]["hrtfs"], key=lambda item: item["hrtf_id"]):
        receiver_output = get_receiver_output_config(config, hrtf["hrtf_id"])
        planned_variants.append(
            {
                "scene_manifest": scene_manifest,
                "hrtf": hrtf,
                "receiver_output": receiver_output,
                "variant_paths": build_render_variant_paths(
                    scene_manifest=scene_manifest,
                    hrtf=hrtf,
                    runtime_manifest_dir=runtime_manifest_dir,
                    outputs=config.outputs,
                    receiver_output=receiver_output,
                ),
            }
        )
    return planned_variants


def _prepare_render_task(
    config: AppConfig,
    planned_variant: PlannedVariant,
    runtime_manifest_dir: Path,
    prepared_source_paths: dict[str, str],
) -> RenderTask:
    runtime_manifest_path = write_single_hrtf_render_manifest(
        planned_variant["scene_manifest"],
        planned_variant["hrtf"],
        runtime_manifest_dir,
        config.outputs,
        planned_variant["receiver_output"],
        prepared_source_paths,
    )
    return {
        "runtime_manifest_path": runtime_manifest_path,
        "variant_paths": planned_variant["variant_paths"],
    }


def _validate_canonical_raven_t30(variant_paths: RenderVariantPaths) -> dict | None:
    metadata_path = Path(variant_paths["render_metadata_path"])
    base = {"metadata_path": metadata_path.resolve().as_posix()}
    if not metadata_path.is_file():
        return {**base, "reason": "render_metadata_missing", "invalid_bands": list(RAVEN_OCTAVE_FREQUENCIES_HZ)}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**base, "reason": "render_metadata_unreadable", "diagnostic": str(exc), "invalid_bands": list(RAVEN_OCTAVE_FREQUENCIES_HZ)}

    try:
        t30_values = payload["summary"]["room"]["reverberation"]["t30_s"]
    except (KeyError, TypeError):
        return {**base, "reason": "raven_t30_missing", "invalid_bands": list(RAVEN_OCTAVE_FREQUENCIES_HZ)}
    if not isinstance(t30_values, list) or len(t30_values) != len(RAVEN_OCTAVE_FREQUENCIES_HZ):
        return {
            **base,
            "reason": "raven_t30_band_count",
            "observed_band_count": len(t30_values) if isinstance(t30_values, list) else None,
            "expected_band_count": len(RAVEN_OCTAVE_FREQUENCIES_HZ),
            "invalid_bands": list(RAVEN_OCTAVE_FREQUENCIES_HZ),
        }

    invalid_bands: list[dict] = []
    for frequency_hz, value in zip(RAVEN_OCTAVE_FREQUENCIES_HZ, t30_values, strict=True):
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        valid = valid and math.isfinite(float(value)) and float(value) > 0
        if not valid:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                diagnostic_value = value if math.isfinite(float(value)) else str(value)
            else:
                diagnostic_value = value if isinstance(value, str) else repr(value)
            invalid_bands.append({
                "frequency_hz": frequency_hz,
                "value": diagnostic_value,
            })
    if invalid_bands:
        return {**base, "reason": "raven_t30_non_finite_or_non_positive", "invalid_bands": invalid_bands}
    return None


def _discard_variant_outputs(variant_paths: RenderVariantPaths) -> None:
    Path(variant_paths["rendered_wav_path"]).unlink(missing_ok=True)
    Path(variant_paths["render_metadata_path"]).unlink(missing_ok=True)


def _discard_failed_scene_variants(
    planned_variants: list[PlannedVariant],
    *,
    metadata_required: bool,
    index_path: Path,
    variant_records: dict,
) -> None:
    for planned_variant in planned_variants:
        variant_paths = planned_variant["variant_paths"]
        _discard_variant_outputs(variant_paths)
        Path(variant_paths["runtime_manifest_path"]).unlink(missing_ok=True)
        upsert_variant(
            index_path,
            variant_records,
            inspect_variant(
                variant_paths,
                metadata_required=metadata_required,
                attempted=True,
            ),
        )


def _append_raven_sampling_failure(
    sampling_summary: SamplingSummary,
    scene_manifest: dict,
    diagnostics: dict,
    raven_rejections: list[dict],
) -> None:
    failure = {
        **diagnostics,
        "scene_id": scene_manifest["scene_id"],
        "stage": "raven_t30",
        "raven_t30_rejections": raven_rejections,
        "failure_record_path": sampling_summary["failures_path"],
    }
    failures_path = Path(sampling_summary["failures_path"])
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    with failures_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(failure, sort_keys=True, ensure_ascii=False) + "\n")
    sampling_summary["generated_scenes"] -= 1
    sampling_summary["failed_scenes"] += 1
    sampling_summary["failed_scene_indices"].append(int(diagnostics["scene_index"]))
    sampling_summary["failed_scene_indices"].sort()
    sampling_summary["status"] = "partial" if sampling_summary["generated_scenes"] else "failed"


def _refresh_reverberation_batch(
    config: AppConfig,
    manifest_paths: list[Path],
    sampling_summary: SamplingSummary,
) -> None:
    reverberation = config.room_sampling.reverberation
    distribution = reverberation.distribution
    bins = build_bins(distribution.range_s.min, distribution.range_s.max, distribution.bin_width_s)
    quotas, _ = build_global_plan(config.execution.num_simulations, bins, config.experiment.random_seed)
    observed = [0] * len(bins)
    fallback_count = 0
    rejections_by_reason: Counter[str] = Counter()
    manifests: list[tuple[Path, dict]] = []
    for manifest_path in manifest_paths:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sampling = manifest["reverberation_sampling"]
        observed[int(sampling["obtained_bin"]["index"])] += 1
        fallback_count += int(bool(sampling["fallback_used"]))
        rejections_by_reason.update(sampling.get("rejections_by_reason", {}))
        manifests.append((manifest_path, manifest))
    rejections_by_reason.update(_read_failure_rejections(Path(sampling_summary["failures_path"])))

    batch = _build_reverberation_batch_metadata(
        config,
        bins,
        quotas,
        observed,
        fallback_count,
        sampling_summary["failed_scenes"],
        dict(rejections_by_reason),
    )
    sampling_summary["reverberation_batch"] = batch
    sampling_summary["status"] = batch["status"]
    for manifest_path, manifest in manifests:
        manifest["reverberation_batch"] = batch
        write_manifest(scene_manifest=manifest, manifest_path=manifest_path, overwrite=True)


def _build_reverberation_batch_metadata(
    config: AppConfig,
    bins,
    quotas: list[int],
    observed: list[int],
    fallback_count: int,
    failed_count: int,
    rejections_by_reason: dict[str, int] | None = None,
) -> dict:
    distribution = config.room_sampling.reverberation.distribution
    summary = summarize(
        quotas,
        observed,
        distribution.quota_tolerance_fraction,
        fallback_count,
        failed_count,
        rejections_by_reason,
    )
    return {
        "range_s": {"min": distribution.range_s.min, "max": distribution.range_s.max},
        "bin_width_s": distribution.bin_width_s,
        "bin_edges_s": [bins[0].lower_s, *[item.upper_s for item in bins]],
        "requested_realizations": config.execution.num_simulations,
        "accepted_realizations": sum(observed),
        "matched_requested_bin_count": sum(observed) - fallback_count,
        "quota_tolerance_fraction": distribution.quota_tolerance_fraction,
        "zero_quota_bins": [index for index, target in enumerate(quotas) if target == 0],
        "bins_without_observations": [index for index, count in enumerate(observed) if count == 0],
        "difficult_bins": [
            index for index, (target, count) in enumerate(zip(quotas, observed, strict=True))
            if target > 0 and count < target
        ],
        "estimator_version": config.room_sampling.reverberation.estimator_version,
        "proposal_strategy_version": PROPOSAL_STRATEGY_VERSION,
        "treatment_catalog_version": config.room_sampling.reverberation.treatment.catalog_version,
        "absorption_mix_model": MIX_MODEL,
        "absorption_mix_model_version": MIX_MODEL_VERSION,
        **summary,
    }


def _read_failure_rejections(failures_path: Path) -> Counter[str]:
    aggregated: Counter[str] = Counter()
    if not failures_path.is_file():
        return aggregated
    for line in failures_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        aggregated.update(payload.get("rejections_by_reason", {}))
    return aggregated


def _run_render_tasks(
    render_tasks: list[RenderTask],
    *,
    num_workers: int,
    matlab_executable: str,
    metadata_required: bool,
    index_path: Path,
    variant_records: dict,
) -> Exception | None:
    if num_workers <= 1:
        return _run_render_tasks_sequentially(
            render_tasks,
            matlab_executable=matlab_executable,
            metadata_required=metadata_required,
            index_path=index_path,
            variant_records=variant_records,
        )

    return _run_render_tasks_in_parallel(
        render_tasks,
        num_workers=num_workers,
        matlab_executable=matlab_executable,
        metadata_required=metadata_required,
        index_path=index_path,
        variant_records=variant_records,
    )


def _run_render_tasks_sequentially(
    render_tasks: list[RenderTask],
    *,
    matlab_executable: str,
    metadata_required: bool,
    index_path: Path,
    variant_records: dict,
) -> Exception | None:
    for task in render_tasks:
        result = _run_single_render_task(task, matlab_executable=matlab_executable)
        _upsert_attempted_render_result(
            result,
            metadata_required=metadata_required,
            index_path=index_path,
            variant_records=variant_records,
        )
        if result["error"] is not None:
            return result["error"]
    return None


def _run_render_tasks_in_parallel(
    render_tasks: list[RenderTask],
    *,
    num_workers: int,
    matlab_executable: str,
    metadata_required: bool,
    index_path: Path,
    variant_records: dict,
) -> Exception | None:
    if not render_tasks:
        return None

    first_render_error: Exception | None = None
    effective_workers = min(num_workers, len(render_tasks))
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = [
            executor.submit(_run_single_render_task, task, matlab_executable=matlab_executable)
            for task in render_tasks
        ]
        for future in as_completed(futures):
            result = future.result()
            _upsert_attempted_render_result(
                result,
                metadata_required=metadata_required,
                index_path=index_path,
                variant_records=variant_records,
            )
            if first_render_error is None and result["error"] is not None:
                first_render_error = result["error"]
    return first_render_error


def _run_single_render_task(task: RenderTask, *, matlab_executable: str) -> RenderResult:
    render_error: Exception | None = None
    try:
        render_manifest_with_matlab(task["runtime_manifest_path"], matlab_executable=matlab_executable)
    except Exception as exc:
        render_error = exc
    return {"task": task, "error": render_error}


def _upsert_attempted_render_result(
    result: RenderResult,
    *,
    metadata_required: bool,
    index_path: Path,
    variant_records: dict,
) -> None:
    upsert_variant(
        index_path,
        variant_records,
        inspect_variant(
            result["task"]["variant_paths"],
            metadata_required=metadata_required,
            attempted=True,
        ),
    )


def _build_planned_variants(config: AppConfig, manifest_paths: list[Path], runtime_manifest_dir: Path) -> list[PlannedVariant]:
    planned_variants: list[PlannedVariant] = []

    for manifest_path in manifest_paths:
        scene_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for hrtf in sorted(scene_manifest["receiver"]["hrtfs"], key=lambda item: item["hrtf_id"]):
            receiver_output = get_receiver_output_config(config, hrtf["hrtf_id"])
            planned_variants.append(
                {
                    "scene_manifest": scene_manifest,
                    "hrtf": hrtf,
                    "receiver_output": receiver_output,
                    "variant_paths": build_render_variant_paths(
                        scene_manifest=scene_manifest,
                        hrtf=hrtf,
                        runtime_manifest_dir=runtime_manifest_dir,
                        outputs=config.outputs,
                        receiver_output=receiver_output,
                    ),
                }
            )

    return planned_variants


def _prepare_static_manifests(config_path: str | Path) -> tuple[AppConfig, list[Path], SamplingSummary]:
    config = load_config(config_path)
    validate_config(config)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)

    rng = random.Random(config.experiment.random_seed)
    background_noise_plan = build_background_noise_plan(config, config.execution.num_simulations)
    planned_manifests: list[tuple[dict, Path]] = []
    sampling_failures: list[dict] = []
    failures_path = layout["sampling_failures_path"].resolve()

    for scene_index in range(config.execution.num_simulations):
        scene_id = f"{config.outputs.naming.scene_id_prefix}_{scene_index + 1:04d}"
        manifest_path = layout["scene_manifest_dir"] / f"{scene_id}.json"
        try:
            sampled_scene = sample_static_scene(config, rng, scene_index)
        except SceneSamplingFailure as exc:
            sampling_failures.append(
                {
                    **exc.diagnostics,
                    "scene_id": scene_id,
                    "failure_record_path": failures_path.as_posix(),
                }
            )
            continue
        sampled_scene["background_noise"] = background_noise_plan[scene_index]
        scene_manifest = build_static_manifest(config, sampled_scene, scene_index, manifest_path)
        planned_manifests.append((scene_manifest, manifest_path))

    reverberation = config.room_sampling.reverberation
    distribution = reverberation.distribution
    bins = build_bins(distribution.range_s.min, distribution.range_s.max, distribution.bin_width_s)
    quotas, _ = build_global_plan(config.execution.num_simulations, bins, config.experiment.random_seed)
    observed = [0] * len(bins)
    fallback_count = 0
    rejections_by_reason: Counter[str] = Counter()
    for scene_manifest, _ in planned_manifests:
        sampling = scene_manifest["reverberation_sampling"]
        observed[sampling["obtained_bin"]["index"]] += 1
        fallback_count += int(sampling["fallback_used"])
        rejections_by_reason.update(sampling.get("rejections_by_reason", {}))
    for failure in sampling_failures:
        rejections_by_reason.update(failure.get("rejections_by_reason", {}))
    reverberation_batch = _build_reverberation_batch_metadata(
        config,
        bins,
        quotas,
        observed,
        fallback_count,
        len(sampling_failures),
        dict(rejections_by_reason),
    )
    for scene_manifest, _ in planned_manifests:
        scene_manifest["reverberation_batch"] = reverberation_batch

    manifest_paths = [
        write_manifest(
            scene_manifest=scene_manifest,
            manifest_path=manifest_path,
            overwrite=config.execution.overwrite_existing,
        )
        for scene_manifest, manifest_path in planned_manifests
    ]

    _write_sampling_failures(failures_path, sampling_failures)
    failed_scene_indices = [failure["scene_index"] for failure in sampling_failures]
    generated_scenes = len(manifest_paths)
    status = reverberation_batch["status"]
    sampling_summary: SamplingSummary = {
        "requested_scenes": config.execution.num_simulations,
        "generated_scenes": generated_scenes,
        "failed_scenes": len(sampling_failures),
        "failed_scene_indices": failed_scene_indices,
        "status": status,
        "failures_path": failures_path.as_posix(),
        "reverberation_batch": reverberation_batch,
    }

    return config, manifest_paths, sampling_summary


def _write_sampling_failures(path: Path, failures: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered_failures = sorted(failures, key=lambda failure: failure["scene_index"])
    contents = "".join(
        f"{json.dumps(failure, sort_keys=True, ensure_ascii=False)}\n"
        for failure in ordered_failures
    )
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(contents, encoding="utf-8")
    temporary_path.replace(path)


def _build_render_summary(
    variant_records: dict,
    render_jobs: int,
    index_path: Path,
    *,
    sampling_summary: SamplingSummary,
    clarity_summary: ClaritySummary | None,
) -> RenderSummary:
    index_summary = summarize_variant_index(variant_records)
    summary: RenderSummary = {
        "total_variants": index_summary["total_variants"],
        "planned_variants": index_summary["planned_variants"],
        "completed_variants": index_summary["completed_variants"],
        "partial_variants": index_summary["partial_variants"],
        "failed_variants": index_summary["failed_variants"],
        "resumed_variants": index_summary["resumed_variants"],
        "inconsistent_variants": index_summary["inconsistent_variants"],
        "render_jobs": render_jobs,
        "index_path": index_path.resolve().as_posix(),
        "sampling": sampling_summary,
        "clarity": clarity_summary,
    }
    return summary
