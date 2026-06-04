import shutil
import subprocess
from pathlib import Path
from typing import Literal, TypedDict

from acoustic_orchestrator.config.models import ClarityRunnerConfig


SubmissionStatus = Literal["submitted", "blocked"]


class ClaritySubmissionResult(TypedDict):
    status: SubmissionStatus
    command: list[str] | None
    message: str


def build_clarity_command(runner: ClarityRunnerConfig, manifest_path: Path) -> list[str]:
    manifest_path = manifest_path.resolve()
    if runner.use_uv:
        if runner.backend_project_path is None:
            raise ValueError("backend_project_path es obligatorio para invocar con uv")
        return [
            "uv",
            "run",
            "--project",
            runner.backend_project_path.resolve().as_posix(),
            runner.entrypoint,
            "run-manifest",
            manifest_path.as_posix(),
        ]

    return [runner.entrypoint, "run-manifest", manifest_path.as_posix()]


def submit_clarity_manifest(runner: ClarityRunnerConfig, manifest_path: Path) -> ClaritySubmissionResult:
    blocked_message = _validate_submission_environment(runner)
    if blocked_message is not None:
        return {
            "status": "blocked",
            "command": None,
            "message": blocked_message,
        }

    command = build_clarity_command(runner, manifest_path)
    completed_process = subprocess.run(command, check=False)
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
