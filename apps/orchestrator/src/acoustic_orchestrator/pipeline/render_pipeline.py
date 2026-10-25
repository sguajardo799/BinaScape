import json
import hashlib
import subprocess
import sys
from uuid import uuid4
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict, cast

from acoustic_orchestrator.config.models import AppConfig, CircularArrayOutputConfig, ReceiverOutputConfig
from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.validator import validate_config
from acoustic_orchestrator.experiment.manifest_writer import write_manifest
from acoustic_orchestrator.experiment.sampler import SceneSamplingFailure, sample_static_scene
from acoustic_orchestrator.experiment.reverberation_sampling import build_bins, build_global_plan, summarize
from acoustic_orchestrator.experiment.reverberation_sampling import PROPOSAL_STRATEGY_VERSION
from acoustic_orchestrator.experiment.treatment_catalog import MIX_MODEL, MIX_MODEL_VERSION
from acoustic_orchestrator.experiment.scene_builder import build_static_manifest
from acoustic_orchestrator.pipeline.clarity_handoff import ClaritySummary, prepare_clarity_handoff
from acoustic_orchestrator.pipeline.progress import ProgressCallback, ProgressReporter
from acoustic_orchestrator.pipeline.matlab_runner import (
    RenderVariantPaths,
    build_render_variant_paths,
    build_single_receiver_render_manifest,
    render_manifest_with_matlab,
    write_single_receiver_render_manifest,
    receiver_variants,
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
from acoustic_orchestrator.pipeline.matlab_runner import MatlabProcessResult
from acoustic_orchestrator.pipeline.render_acceptance import RenderAcceptance, RenderValidationError, evaluate_render, persist_acceptance
from acoustic_orchestrator.pipeline.render_validation import equivalent_render_inputs, validate_render
from acoustic_orchestrator.pipeline.render_history import load_render_history
from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt, read_json_object, sha256_file
from acoustic_orchestrator.pipeline.process_retries import (
    BatchCircuit, RetryBlockedError, RetryChain, classify_attempt, inspect_chain, open_chain, preflight_chain,
)
from acoustic_orchestrator.pipeline.static_plan import (
    StaticWriterLock, apply_static_plan, inspect_static_plan, record_static_manifest_revision,
    record_static_scene_failure, static_writer_lock,
)


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
    failed_matlab_processes: NotRequired[int]
    recovered_matlab_processes: NotRequired[int]
    resumed_recovered_variants: NotRequired[int]
    matlab_invocations: NotRequired[int]
    process_retries: NotRequired[int]
    variants_completed_after_retry: NotRequired[int]
    circuit_open: NotRequired[bool]
    circuit_reason: NotRequired[str | None]


class SamplingSummary(TypedDict):
    requested_scenes: int
    generated_scenes: int
    failed_scenes: int
    failed_scene_indices: list[int]
    status: Literal["complete", "complete_with_deviation", "partial", "failed"]
    failures_path: str
    reverberation_batch: dict


def generate_static_manifests(config_path: str | Path, *, progress: ProgressCallback | None = None) -> list[Path]:
    manifest_paths, _ = generate_static_manifests_with_summary(config_path, progress=progress)
    return manifest_paths


def generate_static_manifests_with_summary(
    config_path: str | Path, *, progress: ProgressCallback | None = None,
) -> tuple[list[Path], SamplingSummary]:
    _, manifest_paths, sampling_summary = _prepare_static_manifests(config_path, progress=ProgressReporter(progress))
    return manifest_paths, sampling_summary


def render_static_scenes(
    config_path: str | Path, matlab_executable: str = "matlab", *, progress: ProgressCallback | None = None,
) -> tuple[list[Path], RenderSummary]:
    config = load_config(config_path)
    validate_config(config)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    with static_writer_lock(layout) as lock:
        return _render_static_scenes_locked(config_path, matlab_executable, lock=lock, progress=ProgressReporter(progress))


def _render_static_scenes_locked(
    config_path: str | Path, matlab_executable: str, *, lock: StaticWriterLock,
    progress: ProgressReporter | None = None,
) -> tuple[list[Path], RenderSummary]:
    progress = progress or ProgressReporter()
    config, manifest_paths, sampling_summary = _prepare_static_manifests(config_path, lock=lock, for_render=True, progress=progress)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    runtime_manifest_dir = layout["runtime_manifest_dir"]
    index_path = layout["render_index_path"]
    existing_records = load_variant_index(index_path)
    resume_history = load_render_history(layout["render_attempts_dir"])
    variant_records = dict(existing_records)
    metadata_required = config.execution.save_render_metadata
    coordinator = _RenderCoordinator(config, layout["render_attempts_dir"], index_path, variant_records, progress=progress)
    # Publish the complete plan before the first render while still enforcing
    # canonical-first execution below.
    for planned_variant in _build_planned_variants(config, manifest_paths, runtime_manifest_dir):
        if planned_variant["variant_paths"]["variant_id"] in existing_records:
            continue  # Publishing a plan must not erase an earlier decision.
        upsert_variant(
            index_path,
            variant_records,
            build_planned_variant_record(
                planned_variant["variant_paths"],
                metadata_required=metadata_required,
            ),
        )
    render_jobs = 0
    attempt_records: list[dict[str, Any]] = []
    accepted_manifest_paths: list[Path] = []
    render_tasks: list[RenderTask] = []
    reused_remaining: list[str] = []
    prepared_source_paths_by_scene: dict[str, dict[str, str]] = {}
    first_render_error: Exception | None = None

    progress.begin("Render canonico", len(manifest_paths), "escenas")
    for manifest_path in manifest_paths:
        # Strict inspection happens in preflight; retain the producer's numeric
        # spelling when deriving runtime JSON (e.g. a gain of 6.0 stays 6.0).
        scene_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        progress.start(scene_manifest["scene_id"])
        scene_index = int(scene_manifest["reverberation_sampling"]["scene_index"])
        raven_rejections: list[dict] = list(
            scene_manifest["reverberation_sampling"].get("raven_t30_rejections", [])
        )

        while True:
            planned_variants = _build_planned_variants_from_manifest(
                config, scene_manifest, runtime_manifest_dir
            )
            for planned_variant in planned_variants:
                if planned_variant["variant_paths"]["variant_id"] in existing_records:
                    continue
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
                    create=False,
                )
                prepared_source_paths_by_scene[scene_manifest["scene_id"]] = prepared_source_paths

            # Check every variant before overwriting runtime evidence, including
            # sources, timing and IR selection, not only array geometry.
            resume_allowed = {}
            for variant_number, variant in enumerate(planned_variants):
                _, expected_manifest = build_single_receiver_render_manifest(
                    variant["scene_manifest"], variant["receiver_variant"], runtime_manifest_dir,
                    config.outputs, variant["receiver_output"], prepared_source_paths,
                )
                resume_allowed[variant["variant_paths"]["variant_id"]] = (
                    all(Path(path).is_file() for path in prepared_source_paths.values())
                    and config.execution.resume_if_possible and should_resume_variant(
                        existing_records, variant["variant_paths"], metadata_required=True if variant_number == 0 else metadata_required,
                        history=resume_history, expected_manifest=expected_manifest,
                    )
                )
            if not all(resume_allowed.values()):
                prepare_scene_source_assets(
                    scene_manifest, layout["prepared_audio_dir"] / scene_manifest["scene_id"],
                )
            prepared_tasks = [
                _prepare_render_task(
                    config,
                    planned_variant,
                    runtime_manifest_dir,
                    prepared_source_paths,
                    materialize=not resume_allowed.get(planned_variant["variant_paths"]["variant_id"], False),
                )
                for planned_variant in planned_variants
            ]
            canonical_task = prepared_tasks[0]
            canonical_paths = canonical_task["variant_paths"]
            resumed = config.execution.resume_if_possible and resume_allowed.get(
                canonical_paths["variant_id"], False
            ) and should_resume_variant(
                existing_records,
                canonical_paths,
                metadata_required=True,
            )
            if resumed:
                canonical_validation = validate_render(canonical_paths, metadata_required=True, canonical=True, profile="reuse")
                canonical_record = inspect_variant(
                    canonical_paths,
                    metadata_required=True,
                    attempted=False,
                    resumed=True,
                    render_validation=canonical_validation,
                    resume_history=resume_history,
                )
                upsert_variant(index_path, variant_records, canonical_record)
                if not canonical_validation.artifacts_ok:
                    first_render_error = RenderValidationError(canonical_validation.as_record()["issues"])
                    break
            else:
                render_jobs += 1
                result = coordinator.execute(
                    canonical_task, matlab_executable=matlab_executable, metadata_required=metadata_required,
                    canonical=True, attempt_records=attempt_records,
                )
                if result["error"] is not None:
                    acceptance = result.get("acceptance")
                    if acceptance is not None and acceptance.process.attempt.record.get("commit_status") == "committed":
                        progress.resolve(scene_manifest["scene_id"], "fallidas")
                    first_render_error = result["error"]
                    break
                canonical_record = variant_records[canonical_paths["variant_id"]]
                canonical_validation = result["acceptance"].validation

            t30_diagnostic = canonical_validation.acoustic_rejection
            if t30_diagnostic is not None:
                t30_diagnostic = {**t30_diagnostic, "metadata_path": canonical_paths["render_metadata_path"]}
            if t30_diagnostic is None:
                accepted_manifest_paths.append(manifest_path)
                progress.resolve(scene_manifest["scene_id"], "reutilizadas" if resumed else "aceptadas")
                for task in prepared_tasks[1:]:
                    variant_paths = task["variant_paths"]
                    if config.execution.resume_if_possible and resume_allowed.get(
                        variant_paths["variant_id"], False
                    ) and should_resume_variant(
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
                                resume_history=resume_history,
                            ),
                        )
                        reused_remaining.append(variant_paths["variant_id"])
                    else:
                        render_tasks.append(task)
                break

            t30_diagnostic.update(
                {
                    "scene_index": scene_index,
                    "scene_id": scene_manifest["scene_id"],
                    "output_type": planned_variants[0]["variant_paths"]["output_type"],
                    "render_status": canonical_record["status"],
                    "sampling_attempt": scene_manifest["reverberation_sampling"]["attempts"],
                }
            )
            if not config.execution.overwrite_existing and any(
                resume_allowed.get(task["variant_paths"]["variant_id"], False) for task in prepared_tasks[1:]
            ):
                first_render_error = RenderValidationError(["acoustic_resampling_would_replace_protected_variants"])
                break
            raven_rejections.append(t30_diagnostic)
            progress.count("remuestreos_T30")
            # Scene-wide resampling invalidates every variant of this scene,
            # but must retain their evidence rather than silently deleting it.
            for task in prepared_tasks:
                coordinator._archive_replaced_outputs(task, include_runtime=True)
            _archive_prepared_source_paths(prepared_source_paths, layout["render_attempts_dir"].parent)
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
                failure = _append_raven_sampling_failure(
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
                    coordinator=coordinator,
                )
                manifest_path.unlink(missing_ok=True)
                record_static_scene_failure(config, scene_manifest["scene_id"], failure, lock=lock)
                progress.resolve(scene_manifest["scene_id"], "fallidas")
                break

            sampled_scene["background_noise"] = scene_manifest.get("background_noise")
            scene_manifest = build_static_manifest(config, sampled_scene, scene_index, manifest_path)
            scene_manifest["reverberation_sampling"]["raven_t30_rejections"] = list(raven_rejections)
            scene_manifest["reverberation_sampling"]["raven_retry_count"] = len(raven_rejections)
            # The candidate must be durable before any of its MATLAB attempts.
            # A crash between scene and plan publication remains fail-closed.
            write_manifest(scene_manifest, manifest_path, overwrite=True)
            record_static_manifest_revision(config, scene_manifest, manifest_path, lock=lock)
            prepared_source_paths_by_scene.pop(scene_manifest["scene_id"], None)

        if first_render_error is not None:
            break

    progress.finish("interrumpido" if first_render_error is not None else "")
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
                attempt_records=attempt_records,
            ),
            cause=first_render_error,
        ) from first_render_error

    render_jobs += len(render_tasks)
    progress.begin("Otras variantes", len(render_tasks) + len(reused_remaining), "variantes",
                   parallel=config.execution.num_workers > 1)
    for variant_id in reused_remaining:
        progress.resolve(variant_id, "reutilizadas")
    first_render_error = _run_render_tasks(
        render_tasks,
        num_workers=config.execution.num_workers,
        matlab_executable=matlab_executable,
        metadata_required=metadata_required,
        index_path=index_path,
        variant_records=variant_records,
        attempt_records=attempt_records,
        coordinator=coordinator,
    )
    progress.finish("interrumpido" if first_render_error is not None else "")
    if first_render_error is not None:
        raise RenderStaticRunError(
            manifest_paths=manifest_paths,
            summary=_build_render_summary(
                variant_records,
                render_jobs,
                index_path,
                sampling_summary=sampling_summary,
                clarity_summary=None,
                attempt_records=attempt_records,
            ),
            cause=first_render_error,
        ) from first_render_error

    clarity_summary = None
    if config.hearing_degradation.enabled:
        clarity_summary = prepare_clarity_handoff(config, progress=progress)
    else:
        progress.begin("Clarity deshabilitado")
        progress.finish()

    return manifest_paths, _build_render_summary(
        variant_records,
        render_jobs,
        index_path,
        sampling_summary=sampling_summary,
        clarity_summary=clarity_summary,
        attempt_records=attempt_records,
    )


def run_clarity_handoff(
    config_path: str | Path, *, submit: bool | None = None, progress: ProgressCallback | None = None,
) -> ClaritySummary:
    config = load_config(config_path)
    validate_config(config)
    return prepare_clarity_handoff(config, submit=submit, progress=ProgressReporter(progress))


class PlannedVariant(TypedDict):
    scene_manifest: dict
    receiver_variant: dict
    receiver_output: ReceiverOutputConfig | CircularArrayOutputConfig
    variant_paths: RenderVariantPaths


class RenderTask(TypedDict):
    runtime_manifest_path: Path
    variant_paths: RenderVariantPaths
    diagnostics_dir: Path
    attempts_dir: Path
    reserved_attempt: NotRequired[RenderAttempt]


class RenderResult(TypedDict):
    task: RenderTask
    error: Exception | None
    acceptance: NotRequired[RenderAcceptance]


class _RenderCoordinator:
    """Single owner of functional commits, retry budgets and batch dispatch."""

    def __init__(self, config: AppConfig, attempts_dir: Path, index_path: Path, records: dict,
                 *, progress: ProgressReporter | None = None) -> None:
        self.config = config
        self.attempts_dir = attempts_dir
        self.index_path = index_path
        self.records = records
        self.progress = progress or ProgressReporter()
        self.chains: dict[str, RetryChain] = {}
        self.circuit = BatchCircuit(
            index_path.parent / "matlab_process_circuit.json",
            config.execution.matlab_process_retry.max_consecutive_failures,
            force_reset=not config.execution.resume_if_possible and config.execution.overwrite_existing,
        )

    def prepare(self, task: RenderTask) -> None:
        if self.progress.stage == "Otras variantes":
            self.progress.start(task["variant_paths"]["variant_id"])
        if self.circuit.is_open:
            raise RetryBlockedError(f"Corte de procesos MATLAB persistido: {self.circuit.path}")
        runtime = task["runtime_manifest_path"]
        key = runtime.as_posix() + ":" + sha256_file(runtime)
        chain = self.chains.get(key)
        if chain is None:
            chains_dir = self.attempts_dir.parent / "process_chains"
            force = not self.config.execution.resume_if_possible and self.config.execution.overwrite_existing
            scheduled = preflight_chain(
                runtime, chains_dir, input_sha256=sha256_file(runtime), reusable=False,
                overwrite_existing=self.config.execution.overwrite_existing,
                force_reset=force,
            )
            if force and scheduled is not None:
                original = scheduled.previous_process_error()
                try:
                    scheduled.archive_attempt()
                except Exception as exc:
                    if original is not None:
                        original.add_note(f"No se pudo conservar el intento anterior al reset explícito: {exc}")
                        raise original from exc
                    raise
                scheduled = None
            # Replacing public outputs is explicit and retains old evidence.
            # Scheduled chains own their own archive instead.
            if self.config.execution.overwrite_existing and scheduled is None:
                self._archive_replaced_outputs(task)
            chain = scheduled or open_chain(
                runtime, chains_dir, self.config.execution.matlab_process_retry,
                resume_if_possible=self.config.execution.resume_if_possible,
                overwrite_existing=self.config.execution.overwrite_existing,
            )
            self.chains[key] = chain
        previous_error = chain.previous_process_error() if chain.record["state"] == "scheduled" else None
        try:
            if chain.record["state"] == "scheduled":
                chain.archive_attempt()
            task["reserved_attempt"] = chain.reserve_attempt(
                runtime, self.attempts_dir, circuit_open=self.circuit.is_open,
            )
        except Exception as exc:
            if previous_error is not None:
                previous_error.add_note(f"No se pudo archivar/reservar el siguiente intento: {exc}")
                raise previous_error from exc
            raise

    def resolve(self, result: RenderResult, *, metadata_required: bool,
                attempt_records: list[dict[str, Any]] | None) -> bool:
        """Persist acceptance before retry scheduling; True means retry is due."""
        try:
            _upsert_attempted_render_result(
                result, metadata_required=metadata_required, index_path=self.index_path,
                variant_records=self.records, attempt_records=attempt_records,
            )
        except Exception as storage_error:
            original = result["error"] or storage_error
            if original is not storage_error:
                original.add_note(f"No se pudo persistir el resultado: {storage_error}")
            result["error"] = original
            acceptance = result.get("acceptance")
            if acceptance is not None:
                attempt = acceptance.process.attempt
                attempt.record.update(commit_status="failed", decision="abort",
                    persistence_error={"type": type(storage_error).__name__, "message": str(storage_error)})
                try:
                    attempt.save()
                except Exception as secondary:
                    original.add_note(f"No se pudo persistir tampoco el fallo funcional: {secondary}")
                if attempt_records is not None and not any(r.get("attempt_id") == attempt.attempt_id for r in attempt_records):
                    attempt_records.append(dict(attempt.record))
            return False
        acceptance = result.get("acceptance")
        if acceptance is None:
            if self.progress.stage == "Otras variantes":
                self.progress.resolve(result["task"]["variant_paths"]["variant_id"], "fallidas")
            return False
        attempt = acceptance.process.attempt
        reserved = result["task"].get("reserved_attempt")
        if reserved is None or reserved.attempt_id != attempt.attempt_id:
            result["error"] = RetryBlockedError("El runner no devolvió el intento reservado")
            return False
        key = result["task"]["runtime_manifest_path"].as_posix() + ":" + attempt.manifest_sha256
        chain = self.chains[key]
        try:
            classification = classify_attempt(acceptance)
            opened = self.circuit.resolve(
                attempt.attempt_id, classification,
                process_failed=acceptance.process.process_status == "failed" and not acceptance.recovered,
            )
            decision = chain.resolve_attempt(acceptance, circuit_open=opened)
            if attempt_records is not None:
                attempt_records[-1] = dict(attempt.record)
            if decision == "scheduled" and result["error"] is not None:
                return True
            if self.progress.stage == "Otras variantes" and attempt.record.get("commit_status") == "committed":
                self.progress.resolve(result["task"]["variant_paths"]["variant_id"],
                                      "fallidas" if result["error"] is not None else "aceptadas")
            if result["error"] is not None:
                result["error"].add_note(f"Cadena MATLAB: {chain.path}; decisión: {decision}")
        except Exception as exc:
            if result["error"] is not None:
                result["error"].add_note(f"No se pudo persistir/programar el reintento: {exc}")
            else:
                result["error"] = exc
        return False

    def execute(self, task: RenderTask, *, matlab_executable: str, metadata_required: bool,
                canonical: bool = False, attempt_records: list[dict[str, Any]] | None = None) -> RenderResult:
        prior_error: Exception | None = None
        while True:
            try:
                self.prepare(task)
            except Exception as exc:
                if prior_error is not None:
                    prior_error.add_note(f"No se pudo preparar el siguiente intento: {exc}")
                return {"task": task, "error": prior_error or exc}
            result = _run_single_render_task(
                task, matlab_executable=matlab_executable, metadata_required=metadata_required, canonical=canonical,
                progress=self.progress,
            )
            if not self.resolve(result, metadata_required=metadata_required, attempt_records=attempt_records):
                return result
            prior_error = result["error"]

    def _archive_replaced_outputs(self, task: RenderTask, *, include_runtime: bool = False) -> None:
        paths = [Path(task["variant_paths"]["rendered_wav_path"]), Path(task["variant_paths"]["render_metadata_path"])]
        if include_runtime:
            paths.append(task["runtime_manifest_path"])
        existing = [path for path in paths if path.exists()]
        if not existing:
            return
        directory = self.attempts_dir.parent / "replaced_outputs" / uuid4().hex
        directory.mkdir(parents=True, exist_ok=False)
        entries: list[dict[str, Any]] = [{"original_path": path.as_posix(), "archive_path": (directory / path.name).as_posix(),
                    "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in existing]
        journal = directory / "archive.json"
        _publish_run_summary(journal, {"schema_version": 1, "state": "pending", "entries": entries})
        for entry in entries:
            Path(entry["original_path"]).rename(entry["archive_path"])
        _publish_run_summary(journal, {"schema_version": 1, "state": "complete", "entries": entries})


def _build_planned_variants_from_manifest(
    config: AppConfig,
    scene_manifest: dict,
    runtime_manifest_dir: Path,
) -> list[PlannedVariant]:
    planned_variants: list[PlannedVariant] = []
    for hrtf in receiver_variants(scene_manifest):
        output_type = "circular_array" if hrtf.get("output_type") == "circular_array" else hrtf["hrtf_id"]
        receiver_output = get_receiver_output_config(config, output_type)
        planned_variants.append(
            {
                "scene_manifest": scene_manifest,
                "receiver_variant": hrtf,
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
    *,
    materialize: bool = True,
) -> RenderTask:
    runtime_manifest_path, expected = build_single_receiver_render_manifest(
        planned_variant["scene_manifest"],
        planned_variant["receiver_variant"],
        runtime_manifest_dir,
        config.outputs,
        planned_variant["receiver_output"],
        prepared_source_paths,
    )
    if materialize and not (
        runtime_manifest_path.is_file()
        and equivalent_render_inputs(expected, read_json_object(runtime_manifest_path))
    ):
        runtime_manifest_path = write_single_receiver_render_manifest(
            planned_variant["scene_manifest"], planned_variant["receiver_variant"], runtime_manifest_dir,
            config.outputs, planned_variant["receiver_output"], prepared_source_paths,
        )
    return {
        "runtime_manifest_path": runtime_manifest_path,
        "variant_paths": planned_variant["variant_paths"],
        "diagnostics_dir": resolve_artifact_layout(config.outputs, config.experiment.experiment_id)["matlab_diagnostics_dir"],
        "attempts_dir": resolve_artifact_layout(config.outputs, config.experiment.experiment_id)["render_attempts_dir"],
    }


def _discard_variant_outputs(variant_paths: RenderVariantPaths) -> None:
    Path(variant_paths["rendered_wav_path"]).unlink(missing_ok=True)
    Path(variant_paths["render_metadata_path"]).unlink(missing_ok=True)


def _archive_prepared_source_paths(paths: dict[str, str], metadata_root: Path) -> None:
    """Keep inputs of a rejected realization before preparing another one."""
    existing = [Path(path) for path in set(paths.values()) if Path(path).is_file()]
    if not existing:
        return
    directory = metadata_root / "replaced_prepared_audio" / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, Any]] = [
        {"original_path": path.as_posix(), "archive_path": (directory / path.name).as_posix(),
         "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in existing
    ]
    journal = directory / "archive.json"
    _publish_run_summary(journal, {"schema_version": 1, "state": "pending", "entries": entries})
    for entry in entries:
        Path(entry["original_path"]).rename(entry["archive_path"])
    _publish_run_summary(journal, {"schema_version": 1, "state": "complete", "entries": entries})


def _discard_failed_scene_variants(
    planned_variants: list[PlannedVariant],
    *,
    metadata_required: bool,
    index_path: Path,
    variant_records: dict,
    coordinator: _RenderCoordinator,
) -> None:
    for planned_variant in planned_variants:
        variant_paths = planned_variant["variant_paths"]
        task: RenderTask = {
            "variant_paths": variant_paths, "runtime_manifest_path": Path(variant_paths["runtime_manifest_path"]),
            "attempts_dir": coordinator.attempts_dir, "diagnostics_dir": coordinator.attempts_dir,
        }
        coordinator._archive_replaced_outputs(task, include_runtime=True)
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
) -> dict:
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
    return failure


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
    for manifest_path in manifest_paths:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sampling = manifest["reverberation_sampling"]
        observed[int(sampling["obtained_bin"]["index"])] += 1
        fallback_count += int(bool(sampling["fallback_used"]))
        rejections_by_reason.update(sampling.get("rejections_by_reason", {}))
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
    # Current batch statistics live outside immutable scene inputs.
    summary_path = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)["render_index_path"].parent / "static_run_summary.json"
    _publish_run_summary(summary_path, {
        "schema_version": 1,
        "generated_scenes": sampling_summary["generated_scenes"],
        "acoustically_accepted_scenes": len(manifest_paths),
        "sampling": sampling_summary,
    })


def _publish_run_summary(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


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
    attempt_records: list[dict[str, Any]] | None = None,
    coordinator: _RenderCoordinator,
) -> Exception | None:
    if num_workers <= 1:
        return _run_render_tasks_sequentially(
            render_tasks,
            matlab_executable=matlab_executable,
            metadata_required=metadata_required,
            index_path=index_path,
            variant_records=variant_records,
            attempt_records=attempt_records,
            coordinator=coordinator,
        )

    return _run_render_tasks_in_parallel(
        render_tasks,
        num_workers=num_workers,
        matlab_executable=matlab_executable,
        metadata_required=metadata_required,
        index_path=index_path,
        variant_records=variant_records,
        attempt_records=attempt_records,
        coordinator=coordinator,
    )


def _run_render_tasks_sequentially(
    render_tasks: list[RenderTask],
    *,
    matlab_executable: str,
    metadata_required: bool,
    index_path: Path,
    variant_records: dict,
    attempt_records: list[dict[str, Any]] | None = None,
    coordinator: _RenderCoordinator,
) -> Exception | None:
    for task in render_tasks:
        result = coordinator.execute(
            task, matlab_executable=matlab_executable, metadata_required=metadata_required,
            attempt_records=attempt_records,
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
    attempt_records: list[dict[str, Any]] | None = None,
    coordinator: _RenderCoordinator,
) -> Exception | None:
    if not render_tasks:
        return None

    first_render_error: Exception | None = None
    effective_workers = min(num_workers, len(render_tasks))
    pending = list(render_tasks)
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures: dict[Future[RenderResult], RenderTask] = {}
        while pending or futures:
            while pending and len(futures) < effective_workers and not coordinator.circuit.is_open and first_render_error is None:
                task = pending.pop(0)
                try:
                    coordinator.prepare(task)
                    future = executor.submit(
                        _run_single_render_task, task, matlab_executable=matlab_executable,
                        metadata_required=metadata_required,
                        progress=coordinator.progress,
                    )
                    futures[future] = task
                except Exception as exc:
                    first_render_error = exc
                    break
            if not futures:
                break
            completed, _ = wait(futures, return_when=FIRST_COMPLETED)
            # Resolve all observed completions before dispatching more work.
            for future in completed:
                task = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"task": task, "error": exc}
                try:
                    retry = coordinator.resolve(result, metadata_required=metadata_required, attempt_records=attempt_records)
                except Exception as exc:
                    retry = False
                    if result["error"] is not None:
                        result["error"].add_note(f"No se pudo resolver el intento: {exc}")
                    else:
                        result["error"] = exc
                if retry and first_render_error is None and not coordinator.circuit.is_open:
                    pending.insert(0, task)
                elif result["error"] is not None and first_render_error is None:
                    first_render_error = result["error"]
        if coordinator.circuit.is_open and first_render_error is None:
            first_render_error = RetryBlockedError(f"Corte de procesos MATLAB: {coordinator.circuit.path}")
    return first_render_error


def _run_single_render_task(
    task: RenderTask, *, matlab_executable: str, metadata_required: bool = False, canonical: bool = False,
    progress: ProgressReporter | None = None,
) -> RenderResult:
    render_error: Exception | None = None
    try:
        reserved = task.get("reserved_attempt")
        runner_options: dict[str, Any] = {"attempt": reserved} if reserved is not None else {}
        if progress is not None:
            progress.count("invocaciones")
            if reserved is not None and reserved.record.get("attempt_ordinal", 1) > 1:
                progress.count("reintentos")
            if progress.enabled:
                runner_options["progress"] = progress
        process_result = render_manifest_with_matlab(
            task["runtime_manifest_path"], matlab_executable=matlab_executable,
            diagnostics_dir=task["diagnostics_dir"],
            attempts_dir=task["attempts_dir"],
            **runner_options,
        )
        if not isinstance(process_result, MatlabProcessResult):
            raise TypeError("MATLAB runner did not return process evidence")
        acceptance = evaluate_render(process_result, task["variant_paths"],
                                     metadata_required=metadata_required, canonical=canonical)
        return {"task": task, "error": acceptance.error, "acceptance": acceptance}
    except Exception as exc:
        render_error = exc
    return {"task": task, "error": render_error}


def _upsert_attempted_render_result(
    result: RenderResult,
    *,
    metadata_required: bool,
    index_path: Path,
    variant_records: dict,
    attempt_records: list[dict[str, Any]] | None = None,
) -> None:
    acceptance = result.get("acceptance")
    record = inspect_variant(
        result["task"]["variant_paths"], metadata_required=metadata_required, attempted=True,
        render_validation=acceptance.validation if acceptance is not None else None,
    )
    if acceptance is not None:
        evidence = acceptance.as_record()
        for field_name in ("attempt_id", "attempt_outcome_path", "completion_path", "process_status", "exit_code",
                           "decision", "recovery_reason"):
            cast(dict[str, Any], record)[field_name] = evidence[field_name]
        try:
            persist_acceptance(acceptance, lambda: upsert_variant(index_path, variant_records, record))
        except Exception as storage_error:
            result["error"] = storage_error
        if attempt_records is not None:
            attempt_records.append(dict(acceptance.process.attempt.record))
        if acceptance.recovered and result["error"] is None:
            try:
                print(
                    f"ADVERTENCIA: MATLAB terminó con código {acceptance.process.exit_code}; "
                    f"render validado del intento {acceptance.process.attempt.attempt_id}. "
                    f"Evidencia: {acceptance.process.attempt.outcome_path}", file=sys.stderr,
                )
            except Exception:
                pass  # The durable warning exists even if the console is unavailable.
    else:
        upsert_variant(index_path, variant_records, record)
        if attempt_records is not None and isinstance(result["error"], subprocess.CalledProcessError):
            attempt_records.append({"process_status": "failed", "decision": "abort"})


def _build_planned_variants(config: AppConfig, manifest_paths: list[Path], runtime_manifest_dir: Path) -> list[PlannedVariant]:
    planned_variants: list[PlannedVariant] = []

    for manifest_path in manifest_paths:
        scene_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        planned_variants.extend(_build_planned_variants_from_manifest(config, scene_manifest, runtime_manifest_dir))

    return planned_variants


def _preflight_render_artifacts(config: AppConfig, scene_manifests: list[dict]) -> None:
    """Inspect every protected variant before publishing any mutable run state."""
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    records = load_variant_index(layout["render_index_path"])
    history = load_render_history(layout["render_attempts_dir"])
    chains_dir = layout["render_attempts_dir"].parent / "process_chains"
    force = not config.execution.resume_if_possible and config.execution.overwrite_existing
    circuit_path = layout["render_index_path"].parent / "matlab_process_circuit.json"
    if circuit_path.exists() and read_json_object(circuit_path).get("open") and not force:
        raise RetryBlockedError(f"Corte de procesos MATLAB persistido: {circuit_path}")
    for path in chains_dir.glob("*/chain.json"):
        chain_record = read_json_object(path)
        if chain_record.get("state") == "running":
            raise RetryBlockedError(f"Terminación del proceso no observada: {path}")
    for scene in scene_manifests:
        rewrites = prepare_scene_source_assets(
            scene, layout["prepared_audio_dir"] / scene["scene_id"], create=False,
        )
        for number, variant in enumerate(_build_planned_variants_from_manifest(config, scene, layout["runtime_manifest_dir"])):
            paths = variant["variant_paths"]
            runtime, expected = build_single_receiver_render_manifest(
                scene, variant["receiver_variant"], layout["runtime_manifest_dir"],
                config.outputs, variant["receiver_output"], rewrites,
            )
            runtime_matches = runtime.is_file() and equivalent_render_inputs(expected, read_json_object(runtime))
            has_outputs = Path(paths["rendered_wav_path"]).exists() or Path(paths["render_metadata_path"]).exists()
            reusable = (
                config.execution.resume_if_possible
                and all(Path(path).is_file() for path in rewrites.values())
                and should_resume_variant(
                    records, paths, metadata_required=True if number == 0 else config.execution.save_render_metadata,
                    history=history, expected_manifest=expected,
                )
            )
            if reusable and number == 0:
                reusable = validate_render(paths, metadata_required=True, canonical=True, profile="reuse").validity == "valid"
            # Existing compatible inputs own their exact bytes (including legacy
            # Windows CRLF). Never infer an attempt hash by reserializing them.
            digest = sha256_file(runtime) if runtime_matches else hashlib.sha256(
                json.dumps(expected, indent=2).encode("utf-8")).hexdigest()
            scheduled_chain = preflight_chain(
                runtime, chains_dir, input_sha256=digest, reusable=reusable,
                overwrite_existing=config.execution.overwrite_existing, force_reset=force,
            )
            if config.execution.overwrite_existing:
                continue
            if runtime.exists() and not runtime_matches:
                predecessor = inspect_chain(runtime, chains_dir)
                acoustic_transition = (
                    not has_outputs and predecessor is not None and predecessor.record["state"] == "not_retryable"
                    and predecessor.record["attempts"][-1].get("classification") == "acoustic_rejection"
                    and predecessor.record["input_sha256"] != digest
                )
                if not acoustic_transition:
                    raise FileExistsError(f"Conflicto de runtime protegido: {runtime}")
            if has_outputs and not reusable and scheduled_chain is None:
                raise FileExistsError(f"Conflicto de salida protegida no reutilizable: {paths['variant_id']}")


def _prepare_static_manifests(
    config_path: str | Path, *, lock: StaticWriterLock | None = None, for_render: bool = False,
    progress: ProgressReporter | None = None,
) -> tuple[AppConfig, list[Path], SamplingSummary]:
    progress = progress or ProgressReporter()
    config = load_config(config_path)
    validate_config(config)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)

    if lock is None:
        with static_writer_lock(layout) as acquired:
            return _prepare_static_manifests(config_path, lock=acquired, for_render=for_render, progress=progress)
    plan = inspect_static_plan(config, layout, sample_scene=sample_static_scene, progress=progress)
    planned_manifests = [(item.manifest, item.path) for item in plan.manifests]
    sampling_failures = plan.failures
    failures_path = layout["sampling_failures_path"].resolve()
    progress.begin("Verificando compatibilidad de artefactos")
    if for_render:
        _preflight_render_artifacts(config, [manifest for manifest, _ in planned_manifests])

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
    for item in plan.manifests:
        if item.needs_write:
            item.manifest["reverberation_batch"] = reverberation_batch
    progress.finish()
    manifest_paths = apply_static_plan(plan, lock=lock, progress=progress)
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
    attempt_records: list[dict[str, Any]] | None = None,
) -> RenderSummary:
    index_summary = summarize_variant_index(variant_records)
    attempts = attempt_records or []
    # A retried realization is one job, even when it launches several processes;
    # queued work never dispatched after a cutoff is not an attempted job.
    render_jobs = len({record.get("chain_id") or record.get("attempt_id") or f"legacy_{number}"
                       for number, record in enumerate(attempts)})
    circuit_path = index_path.parent / "matlab_process_circuit.json"
    circuit_open = circuit_path.exists() and bool(read_json_object(circuit_path).get("open"))
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
        "failed_matlab_processes": sum(record.get("process_status") == "failed" for record in (attempt_records or [])),
        "recovered_matlab_processes": sum(record.get("decision") == "continue_with_warning" and
                                         record.get("commit_status") == "committed" for record in (attempt_records or [])),
        "resumed_recovered_variants": sum(record.get("resumed", False) and record.get("recovery_reason") == "post_completion"
                                           for record in variant_records.values()),
        "matlab_invocations": len(attempts),
        "process_retries": sum(record.get("attempt_ordinal", 1) > 1 for record in attempts),
        "variants_completed_after_retry": len({record["variant_id"] for record in attempts
            if record.get("attempt_ordinal", 1) > 1 and record.get("decision") in {"continue", "continue_with_warning"}
            and record.get("commit_status") == "committed"}),
        "circuit_open": circuit_open,
        "circuit_reason": "max_consecutive_process_failures" if circuit_open else None,
    }
    path = index_path.parent / "static_run_summary.json"
    previous: dict[str, Any] = read_json_object(path) if path.exists() else {"schema_version": 1}
    previous.update(render=summary, completed_variants=summary["completed_variants"])
    _publish_run_summary(path, previous)
    return summary
