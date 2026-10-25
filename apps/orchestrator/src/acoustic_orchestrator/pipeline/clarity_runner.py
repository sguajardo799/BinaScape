import shutil
import json
import subprocess
from pathlib import Path
from typing import Literal, TypedDict

from acoustic_orchestrator.config.models import ClarityRunnerConfig
from acoustic_orchestrator.pipeline.clarity_eligibility import is_clarity_output_eligible
from acoustic_orchestrator.pipeline.progress import ProgressReporter


SubmissionStatus = Literal["submitted", "blocked"]


class ClaritySubmissionResult(TypedDict):
    status: SubmissionStatus
    command: list[str] | None
    message: str


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
    if blocked_message is None:
        try:
            for line in manifest_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                job = json.loads(line)
                output_type = job.get("output_type") if isinstance(job, dict) else None
                if not isinstance(output_type, str) or not is_clarity_output_eligible(output_type):
                    blocked_message = f"Clarity rechaza output_type no elegible: {output_type}"
                    break
        except (OSError, ValueError) as exc:
            blocked_message = f"No se pudo validar el manifiesto Clarity: {exc}"
    if blocked_message is not None:
        return {
            "status": "blocked",
            "command": None,
            "message": blocked_message,
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
        }

    return {
        "status": "submitted",
        "command": command,
        "message": "Backend de Clarity enviado correctamente",
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
