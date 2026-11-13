import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, NotRequired, TypedDict

from acoustic_orchestrator.config.models import CircularArrayOutputConfig, OutputsConfig, ReceiverOutputConfig
from acoustic_orchestrator.pipeline.output_paths import resolve_output_paths
from acoustic_orchestrator.pipeline.matlab_diagnostics import MatlabDiagnostics, diagnostic_warning
from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt
from acoustic_orchestrator.pipeline.progress import ProgressReporter

if TYPE_CHECKING:
    from acoustic_orchestrator.pipeline.matlab_pool import MatlabPoolSession, PoolSessionFailure


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MATLAB_APP_DIR = (PROJECT_ROOT.parent / "matlab").resolve()
MATLAB_ENTRYPOINT = MATLAB_APP_DIR / "run_raven_static_render.m"
MATLAB_POOL_ENTRYPOINT = MATLAB_APP_DIR / "run_raven_pool_server.m"
PYROOMACOUSTICS_WORKER_MODULE = "acoustic_orchestrator.backends.pyroomacoustics.worker"


class RenderVariantPaths(TypedDict):
    variant_id: str
    scene_id: str
    output_type: str
    output_subdir: str
    runtime_manifest_path: str
    rendered_wav_path: str
    render_metadata_path: str
    receiver_ir_path: str | None
    circular_array: NotRequired[dict]
    sample_rate_hz: NotRequired[int]


@dataclass
class MatlabProcessResult:
    attempt: RenderAttempt
    exit_code: int | None
    error: Exception | None
    termination_observed: bool
    process_status: str


def render_manifest_with_matlab(
    manifest_path: Path, matlab_executable: str = "matlab", *, diagnostics_dir: Path | None = None,
    attempts_dir: Path | None = None,
    attempt: RenderAttempt | None = None,
    progress: ProgressReporter | None = None,
) -> MatlabProcessResult:
    attempt, diagnostics = _prepare_process(manifest_path, attempts_dir, diagnostics_dir, attempt)
    context = "[]"
    if diagnostics.ready:
        context = (
            f"struct('attempt_id', '{diagnostics.attempt_id}', "
            f"'events_path', '{_escape_matlab_path(diagnostics.events_path)}')"
        )
    escaped_variant = attempt.variant_id.replace("'", "''")
    completion_context = (
        f"struct('attempt_id', '{attempt.attempt_id}', 'variant_id', '{escaped_variant}', "
        f"'manifest_sha256', '{attempt.manifest_sha256}', "
        f"'completion_path', '{_escape_matlab_path(attempt.completion_path)}', "
        f"'failure_path', '{_escape_matlab_path(attempt.failure_path)}')"
    )
    batch_command = (
        f"addpath(genpath('{_escape_matlab_path(MATLAB_APP_DIR)}')); "
        f"run_raven_static_render('{_escape_matlab_path(attempt.snapshot_path)}', {context}, {completion_context});"
    )

    def require_entrypoint() -> None:
        if not MATLAB_ENTRYPOINT.is_file():
            raise FileNotFoundError(f"No se encontró el entrypoint de MATLAB: {MATLAB_ENTRYPOINT}")

    environment, prefdir = isolated_matlab_env()
    try:
        result = _run_render_process(attempt, diagnostics, [matlab_executable, "-batch", batch_command],
                                     progress=progress, before_launch=require_entrypoint, env=environment)
    finally:
        cleanup = remove_matlab_prefdir(prefdir)
    attempt.record["matlab_prefdir"] = {"path": prefdir.as_posix(), "cleanup": cleanup}
    try:
        attempt.save()
    except Exception as storage_error:
        if result.error is not None:
            result.error.add_note(f"Functional process evidence could not be persisted: {storage_error}")
            raise result.error from storage_error
        raise
    return result


def render_manifest_with_matlab_pool(
    manifest_path: Path, session: "MatlabPoolSession", *, diagnostics_dir: Path | None = None,
    attempts_dir: Path | None = None, attempt: RenderAttempt | None = None,
    progress: ProgressReporter | None = None,
) -> MatlabProcessResult:
    """Run one attempt as a job of the shared MATLAB pool session (same contract as a process)."""
    from acoustic_orchestrator.pipeline.matlab_pool import PoolSessionFailure

    attempt, diagnostics = _prepare_process(manifest_path, attempts_dir, diagnostics_dir, attempt)
    command = ["matlab_pool", "run_raven_static_render", attempt.snapshot_path.as_posix()]
    request = {
        "schema_version": 1, "attempt_id": attempt.attempt_id,
        "snapshot_path": attempt.snapshot_path.resolve().as_posix(),
        "diagnostics": ({"attempt_id": diagnostics.attempt_id, "events_path": diagnostics.events_path.resolve().as_posix()}
                        if diagnostics.ready else None),
        "completion_context": {
            "attempt_id": attempt.attempt_id, "variant_id": attempt.variant_id,
            "manifest_sha256": attempt.manifest_sha256,
            "completion_path": attempt.completion_path.resolve().as_posix(),
            "failure_path": attempt.failure_path.resolve().as_posix(),
        },
        "console_path": diagnostics.console_path.resolve().as_posix(),
    }
    attempt.record.update(matlab_runner="pool", pool_session_id=None, pool_worker_id=None)

    def require_entrypoint() -> None:
        if not MATLAB_POOL_ENTRYPOINT.is_file():
            raise FileNotFoundError(f"No se encontró el servidor de pool de MATLAB: {MATLAB_POOL_ENTRYPOINT}")

    def execute(options: dict[str, Any]) -> subprocess.CompletedProcess:
        try:
            exit_code, worker_id, session_id = session.run_job(attempt.attempt_id, request)
        except PoolSessionFailure as failure:
            attempt.record.update(pool_session_id=failure.session_id, pool_session_failure=failure.as_record())
            _append_session_console(diagnostics.console_path, failure)
            raise subprocess.CalledProcessError(failure.exit_code, command) from failure
        attempt.record.update(pool_session_id=session_id, pool_worker_id=worker_id)
        if exit_code != 0:
            raise subprocess.CalledProcessError(exit_code, command)
        return subprocess.CompletedProcess(command, 0)

    return _run_render_process(attempt, diagnostics, command, progress=progress,
                               before_launch=require_entrypoint, execute=execute)


def _append_session_console(console_path: Path, failure: "PoolSessionFailure") -> None:
    """Give a failed attempt the session console, where startup errors (e.g. licensing) appear."""
    try:
        session_console = failure.console_path.read_bytes()
        with console_path.open("ab") as console:
            console.write(f"\n[pool session {failure.session_id}: {failure.reason}, exit {failure.exit_code}]\n".encode())
            console.write(session_console)
    except Exception as exc:
        diagnostic_warning(f"could not copy MATLAB pool console for {failure.session_id}: {exc}")


def isolated_matlab_env() -> tuple[dict[str, str], Path]:
    """Environment with a fresh, private ``MATLAB_PREFDIR`` (outside batch outputs) and its path.

    Concurrent MATLAB processes sharing the user's preferences directory race on
    ``matlabprefs.mat`` (ITA-Toolbox rewrites it on first access). The caller owns
    the directory and must remove it with :func:`remove_matlab_prefdir`.
    """
    prefdir = Path(tempfile.mkdtemp(prefix="binascape-matlab-prefs-"))
    return {**os.environ, "MATLAB_PREFDIR": str(prefdir)}, prefdir


def remove_matlab_prefdir(prefdir: Path) -> str:
    """Best-effort removal; a failure only warns and never changes the render outcome."""
    try:
        shutil.rmtree(prefdir)
    except Exception as exc:
        diagnostic_warning(f"could not remove MATLAB_PREFDIR {prefdir}: {exc}")
        return "failed"
    return "removed"


def render_manifest_with_pyroomacoustics(
    manifest_path: Path, *, diagnostics_dir: Path | None = None, attempts_dir: Path | None = None,
    attempt: RenderAttempt | None = None, progress: ProgressReporter | None = None,
) -> MatlabProcessResult:
    """Run one attempt in a separate Pyroomacoustics worker process (same contract as MATLAB)."""
    attempt, diagnostics = _prepare_process(manifest_path, attempts_dir, diagnostics_dir, attempt)
    command = [
        sys.executable, "-m", PYROOMACOUSTICS_WORKER_MODULE, attempt.snapshot_path.as_posix(),
        "--attempt-id", attempt.attempt_id, "--variant-id", attempt.variant_id,
        "--manifest-sha256", attempt.manifest_sha256,
        "--completion-path", attempt.completion_path.as_posix(),
        "--failure-path", attempt.failure_path.as_posix(),
    ]
    if diagnostics.ready:
        command += ["--events-path", diagnostics.events_path.as_posix()]
    # One thread per worker: avoids oversubscription under num_workers and thread-order effects.
    environment = {**os.environ, "OMP_NUM_THREADS": "1", "PRA_NUM_THREADS": "1"}
    return _run_render_process(attempt, diagnostics, command, progress=progress, env=environment)


def _prepare_process(
    manifest_path: Path, attempts_dir: Path | None, diagnostics_dir: Path | None, attempt: RenderAttempt | None,
) -> tuple[RenderAttempt, MatlabDiagnostics]:
    manifest_path = manifest_path.resolve()
    attempt = attempt or RenderAttempt(manifest_path, attempts_dir)
    if attempt.manifest_path != manifest_path or attempt.record["command"] is not None:
        raise ValueError("attempt must be fresh and belong to this manifest")
    diagnostics = MatlabDiagnostics(manifest_path, diagnostics_dir, attempt_id=attempt.attempt_id,
                                    variant_id=attempt.variant_id, input_snapshot_path=attempt.snapshot_path)
    return attempt, diagnostics


def _run_render_process(
    attempt: RenderAttempt, diagnostics: MatlabDiagnostics, command: list[str], *,
    progress: ProgressReporter | None, before_launch: Callable[[], None] | None = None,
    env: dict[str, str] | None = None,
    execute: Callable[[dict[str, Any]], subprocess.CompletedProcess] | None = None,
) -> MatlabProcessResult:
    """Shared render-process core: attempt record, diagnostics, execution, status.

    ``execute`` receives the console options (``stdout``/``stderr``/``env``) and
    must behave like ``subprocess.run(command, check=True, **options)``: return a
    completed process or raise ``CalledProcessError`` for a non-zero exit and
    ``OSError`` when the render could not be launched. The default runs
    ``command`` as a subprocess; the pool runner submits a job instead.
    """
    if execute is None:
        def execute(options: dict[str, Any]) -> subprocess.CompletedProcess:
            return subprocess.run(command, check=True, **options)
    started = time.monotonic()
    attempt.record["command"] = command
    attempt.save()  # Functional storage must be ready before launching the process.
    console = diagnostics.begin(command)
    if progress is not None:
        progress.emit("attempt_begin", key=attempt.variant_id, attempt_id=attempt.attempt_id,
                      events_path=diagnostics.events_path.as_posix())
    exit_code = None
    error: Exception | None = None
    finished = False
    termination_observed = False
    try:
        if before_launch is not None:
            before_launch()
        options: dict[str, Any] = {} if console is None else {"stdout": console, "stderr": subprocess.STDOUT}
        if env is not None:
            options["env"] = env
        completed = execute(options)
        if type(completed.returncode) is not int:
            raise RuntimeError("No se observó un código de salida válido del proceso de render")
        exit_code = completed.returncode
        termination_observed = True
        if exit_code != 0:
            error = subprocess.CalledProcessError(exit_code, command)
        finished = True
    except Exception as exc:
        error = exc
        if isinstance(exc, subprocess.CalledProcessError):
            exit_code = exc.returncode
            termination_observed = type(exit_code) is int
        finished = True
    finally:
        diagnostics.close_console(console)
        if finished:
            diagnostics.safely(lambda: diagnostics.finish(exit_code, error))
        if progress is not None:
            progress.emit("attempt_end", key=attempt.variant_id, attempt_id=attempt.attempt_id)
    process_status = ("succeeded" if exit_code == 0 else "failed" if termination_observed else
                      "launch_failed" if isinstance(error, OSError) else "termination_unobserved")
    attempt.record.update(
        process_status=process_status, exit_code=exit_code, termination_observed=termination_observed,
        finished_at=datetime.now(UTC).isoformat(), duration_s=max(0.0, time.monotonic() - started),
        error=None if error is None else {"type": type(error).__name__, "message": str(error)},
    )
    try:
        attempt.save()
    except Exception as storage_error:
        if error is not None:
            error.add_note(f"Functional process evidence could not be persisted: {storage_error}")
            raise error from storage_error
        raise
    return MatlabProcessResult(attempt, exit_code, error, termination_observed, process_status)


def write_single_receiver_render_manifest(
    scene_manifest: dict,
    hrtf: dict,
    output_dir: Path,
    outputs: OutputsConfig,
    receiver_output: ReceiverOutputConfig | CircularArrayOutputConfig,
    source_path_rewrites: dict[str, str] | None = None,
) -> Path:
    manifest_path, variant_manifest = build_single_receiver_render_manifest(
        scene_manifest, hrtf, output_dir, outputs, receiver_output, source_path_rewrites,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(variant_manifest, indent=2), encoding="utf-8", newline="\n")
    return manifest_path


def build_single_receiver_render_manifest(
    scene_manifest: dict, hrtf: dict, output_dir: Path, outputs: OutputsConfig,
    receiver_output: ReceiverOutputConfig | CircularArrayOutputConfig,
    source_path_rewrites: dict[str, str] | None = None,
) -> tuple[Path, dict]:
    """Build expected runtime input without overwriting resume evidence."""
    variant_paths = build_render_variant_paths(
        scene_manifest=scene_manifest,
        hrtf=hrtf,
        runtime_manifest_dir=output_dir,
        outputs=outputs,
        receiver_output=receiver_output,
    )
    variant_manifest = deepcopy(scene_manifest)
    variant_manifest["project_name"] = build_raven_project_name(
        scene_manifest["project_name"],
        variant_paths["variant_id"],
    )
    receiver = variant_manifest["receiver"]
    receiver.pop("hrtf", None)
    receiver.pop("active_hrtf", None)
    if variant_paths["output_type"] == "circular_array":
        receiver["hrtfs"] = []
        receiver["circular_array"] = deepcopy(hrtf)
    else:
        receiver["hrtfs"] = [deepcopy(hrtf)]
        receiver.pop("circular_array", None)
    variant_manifest["render"]["active_output_type"] = variant_paths["output_type"]
    variant_manifest["render"]["output_wav_path"] = variant_paths["rendered_wav_path"]
    variant_manifest["render"]["output_metadata_path"] = variant_paths["render_metadata_path"]

    for source in variant_manifest["sources"]:
        rewritten_path = (source_path_rewrites or {}).get(source["source_id"])
        if rewritten_path is not None:
            source["audio_path"] = rewritten_path

    manifest_path = Path(variant_paths["runtime_manifest_path"])
    return manifest_path, variant_manifest


# Compatibilidad para consumidores del helper anterior, sin cambiar el contrato.
write_single_hrtf_render_manifest = write_single_receiver_render_manifest


def build_raven_project_name(project_name: str, variant_id: str) -> str:
    """Return a deterministic RAVEN-safe project name for one render variant."""
    return _filesystem_safe_name(f"{project_name}__{variant_id}")


def _filesystem_safe_name(value: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    safe_value = safe_value.strip("._-")
    if not safe_value:
        raise ValueError("RAVEN project name cannot be empty")
    return safe_value


def build_render_variant_paths(
    scene_manifest: dict,
    hrtf: dict,
    runtime_manifest_dir: Path,
    outputs: OutputsConfig,
    receiver_output: ReceiverOutputConfig | CircularArrayOutputConfig,
) -> RenderVariantPaths:
    scene_id = scene_manifest["scene_id"]
    is_array = hrtf.get("output_type") == "circular_array"
    output_type = "circular_array" if is_array else hrtf["hrtf_id"]
    resolved_paths = resolve_output_paths(
        scene_id,
        output_type,
        outputs,
        receiver_output,
        scene_manifest["project_name"],
    )
    paths: RenderVariantPaths = {
        "variant_id": resolved_paths["variant_id"],
        "scene_id": scene_id,
        "output_type": output_type,
        "output_subdir": resolved_paths["output_subdir"],
        "runtime_manifest_path": (runtime_manifest_dir / f"{resolved_paths['variant_id']}.json").resolve().as_posix(),
        "rendered_wav_path": resolved_paths["wav_path"],
        "render_metadata_path": resolved_paths["metadata_path"],
        "receiver_ir_path": None if is_array else Path(hrtf["hrtf_path"]).resolve().as_posix(),
    }
    if is_array:
        paths["circular_array"] = deepcopy(hrtf)
        paths["sample_rate_hz"] = scene_manifest["render"]["sample_rate_hz"]
    return paths


def receiver_variants(scene_manifest: dict) -> list[dict]:
    variants = sorted(scene_manifest["receiver"].get("hrtfs", []), key=lambda item: item["hrtf_id"])
    array = scene_manifest["receiver"].get("circular_array")
    if array is not None:
        variants.append(array)
    return variants


def _escape_matlab_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")
