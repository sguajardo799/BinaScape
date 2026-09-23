import random
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal, TypedDict

from acoustic_orchestrator.config.models import AppConfig, ReceiverOutputConfig
from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.validator import validate_config
from acoustic_orchestrator.experiment.manifest_writer import write_manifest
from acoustic_orchestrator.experiment.sampler import SceneSamplingFailure, build_background_noise_plan, sample_static_scene
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
    status: Literal["complete", "partial", "failed"]
    failures_path: str


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
    planned_variants = _build_planned_variants(config, manifest_paths, runtime_manifest_dir)
    metadata_required = config.execution.save_render_metadata

    for planned_variant in planned_variants:
        upsert_variant(
            index_path,
            variant_records,
            build_planned_variant_record(
                planned_variant["variant_paths"],
                metadata_required=metadata_required,
            ),
        )

    render_tasks: list[RenderTask] = []
    prepared_source_paths_by_scene: dict[str, dict[str, str]] = {}
    for planned_variant in planned_variants:
        scene_manifest = planned_variant["scene_manifest"]
        hrtf = planned_variant["hrtf"]
        variant_paths = planned_variant["variant_paths"]
        prepared_source_paths = prepared_source_paths_by_scene.get(scene_manifest["scene_id"])
        if prepared_source_paths is None:
            prepared_source_paths = prepare_scene_source_assets(
                scene_manifest,
                layout["prepared_audio_dir"] / scene_manifest["scene_id"],
            )
            prepared_source_paths_by_scene[scene_manifest["scene_id"]] = prepared_source_paths
        runtime_manifest_path = write_single_hrtf_render_manifest(
            scene_manifest,
            hrtf,
            runtime_manifest_dir,
            config.outputs,
            planned_variant["receiver_output"],
            prepared_source_paths,
        )

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
            continue

        render_tasks.append({"runtime_manifest_path": runtime_manifest_path, "variant_paths": variant_paths})

    render_jobs = len(render_tasks)
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
        for hrtf in scene_manifest["receiver"]["hrtfs"]:
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
    if not sampling_failures:
        status: Literal["complete", "partial", "failed"] = "complete"
    elif generated_scenes:
        status = "partial"
    else:
        status = "failed"
    sampling_summary: SamplingSummary = {
        "requested_scenes": config.execution.num_simulations,
        "generated_scenes": generated_scenes,
        "failed_scenes": len(sampling_failures),
        "failed_scene_indices": failed_scene_indices,
        "status": status,
        "failures_path": failures_path.as_posix(),
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
