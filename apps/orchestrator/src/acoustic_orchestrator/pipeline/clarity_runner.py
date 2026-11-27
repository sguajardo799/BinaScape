import shutil
import json
import subprocess
from pathlib import Path
from typing import Literal, TypedDict

from acoustic_orchestrator.config.models import ClarityRunnerConfig
from acoustic_orchestrator.pipeline.clarity_eligibility import is_clarity_output_eligible
from acoustic_orchestrator.pipeline.progress import ProgressReporter


SubmissionStatus = Literal["submitted", "blocked"]


class ClaritySubmissionResult(TypedDict, total=False):
    status: SubmissionStatus
    command: list[str] | None
    message: str
    # job_id -> reason for jobs withheld before submission (e.g. WHIS environment missing).
    blocked_jobs: dict[str, str]


WHIS_REQUIRED_DIRS = (("WHIS", "WHISv302"), ("gammachirp-filterbank", "GCFBv234"))


def check_whis_environment(processor_config: dict) -> str | None:
    """Return why a WHIS job cannot run on this machine, or None when its environment is complete."""
    install_root = Path(str(processor_config.get("install_root") or ""))
    for parts in WHIS_REQUIRED_DIRS:
        required = install_root.joinpath(*parts)
        if not required.is_dir():
            return f"WHIS no disponible: falta {required.as_posix()} bajo hearing_degradation.whis.install_root"
    executable = str(processor_config.get("matlab_executable") or "matlab")
    if shutil.which(executable) is None and not Path(executable).is_file():
        return f"WHIS no disponible: no se encontró el ejecutable de MATLAB '{executable}'"
    return None


def build_clarity_command(runner: ClarityRunnerConfig, manifest_path: Path, *, no_progress: bool = False) -> list[str]:
    manifest_path = manifest_path.resolve()
    if runner.use_uv:
        if runner.backend_project_path is None:
            raise ValueError("backend_project_path es obligatorio para invocar con uv")
        command = [
            "uv",
            "run",
            "--project",
            runner.backend_project_path.resolve().as_posix(),
            runner.entrypoint,
            "run-manifest",
            manifest_path.as_posix(),
        ]

    else:
        command = [runner.entrypoint, "run-manifest", manifest_path.as_posix()]
    if no_progress:
        command.append("--no-progress")
    return command


def submit_clarity_manifest(
    runner: ClarityRunnerConfig, manifest_path: Path, *, progress: ProgressReporter | None = None,
) -> ClaritySubmissionResult:
    blocked_message = _validate_submission_environment(runner)
    blocked_jobs: dict[str, str] = {}
    if blocked_message is None:
        try:
            lines = [line for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            runnable_lines: list[str] = []
            whis_checks: dict[tuple[str, str], str | None] = {}
            for line in lines:
                job = json.loads(line)
                output_type = job.get("output_type") if isinstance(job, dict) else None
                if not isinstance(output_type, str) or not is_clarity_output_eligible(output_type):
                    blocked_message = f"Clarity rechaza output_type no elegible: {output_type}"
                    break
                if job.get("processor") == "whis":
                    config = job.get("processor_config") or {}
                    key = (str(config.get("install_root")), str(config.get("matlab_executable")))
                    if key not in whis_checks:
                        whis_checks[key] = check_whis_environment(config)
                    if whis_checks[key] is not None:
                        blocked_jobs[str(job.get("job_id"))] = whis_checks[key]
                        continue
                runnable_lines.append(line)
            if blocked_message is None and blocked_jobs:
                # Only the WHIS jobs are withheld; MSBG jobs of the same manifest still run.
                manifest_path.write_text("".join(f"{line}\n" for line in runnable_lines), encoding="utf-8")
                if not runnable_lines:
                    blocked_message = next(iter(blocked_jobs.values()))
        except (OSError, ValueError) as exc:
            blocked_message = f"No se pudo validar el manifiesto Clarity: {exc}"
    if blocked_message is not None:
        return {
            "status": "blocked",
            "command": None,
            "message": blocked_message,
            "blocked_jobs": blocked_jobs,
        }

    command = build_clarity_command(runner, manifest_path, no_progress=not (progress and progress.enabled))
    if progress is not None:
        progress.finish("entradas verificadas; enviando backend")
        progress.begin("Clarity backend en ejecucion")
        progress.emit("suspend")
    try:
        completed_process = subprocess.run(command, check=False)
    finally:
        if progress is not None:
            progress.emit("resume")
            progress.begin("Clarity reinspeccionando resultados")
    if completed_process.returncode != 0:
        return {
            "status": "submitted",
            "command": command,
            "message": (
                "El backend de Clarity devolvió exit code "
                f"{completed_process.returncode}; se reinspeccionarán los resultados por job"
            ),
            "blocked_jobs": blocked_jobs,
        }

    return {
        "status": "submitted",
        "command": command,
        "message": "Backend de Clarity enviado correctamente",
        "blocked_jobs": blocked_jobs,
    }


def _validate_submission_environment(runner: ClarityRunnerConfig) -> str | None:
    if runner.entrypoint.strip() == "":
        return "No se configuró hearing_degradation.runner.entrypoint"

    if runner.use_uv:
        if shutil.which("uv") is None:
            return "No se encontró 'uv' en PATH para ejecutar el backend de Clarity"
        if runner.backend_project_path is None:
            return "Falta hearing_degradation.runner.backend_project_path para ejecutar Clarity con uv"
        if not runner.backend_project_path.is_dir():
            return (
                "No se encontró el backend de Clarity en "
                f"{runner.backend_project_path.resolve().as_posix()}"
            )
        if not (runner.backend_project_path / "pyproject.toml").is_file():
            return "El backend de Clarity configurado no contiene pyproject.toml"

    return None
