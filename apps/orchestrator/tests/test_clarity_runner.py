from pathlib import Path

import pytest

from acoustic_orchestrator.config.models import ClarityRunnerConfig
from acoustic_orchestrator.pipeline.clarity_runner import build_clarity_command, submit_clarity_manifest


def test_build_clarity_command_prefers_uv_project_execution(tmp_path: Path) -> None:
    runner = ClarityRunnerConfig(
        backend_project_path=tmp_path / "clarity-backend",
        entrypoint="clarity-backend",
        auto_submit=True,
        use_uv=True,
    )

    command = build_clarity_command(runner, tmp_path / "clarity_jobs.jsonl")

    assert command[:4] == ["uv", "run", "--project", (tmp_path / "clarity-backend").resolve().as_posix()]
    assert command[-2:] == ["run-manifest", (tmp_path / "clarity_jobs.jsonl").resolve().as_posix()]


def test_submit_clarity_manifest_returns_blocked_when_backend_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_runner.shutil.which", lambda _: "uv")
    runner = ClarityRunnerConfig(
        backend_project_path=tmp_path / "missing-backend",
        entrypoint="clarity-backend",
        auto_submit=True,
        use_uv=True,
    )

    result = submit_clarity_manifest(runner, tmp_path / "clarity_jobs.jsonl")

    assert result["status"] == "blocked"
    assert result["command"] is None


def test_submit_clarity_manifest_runs_subprocess_when_environment_is_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_dir = tmp_path / "clarity-backend"
    backend_dir.mkdir()
    (backend_dir / "pyproject.toml").write_text("[project]\nname='clarity-backend'\nversion='0.1.0'\n", encoding="utf-8")
    manifest_path = tmp_path / "clarity_jobs.jsonl"
    manifest_path.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    class CompletedProcess:
        returncode = 0

    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_runner.shutil.which", lambda _: "uv")
    monkeypatch.setattr(
        "acoustic_orchestrator.pipeline.clarity_runner.subprocess.run",
        lambda command, check: calls.append(command) or CompletedProcess(),
    )
    runner = ClarityRunnerConfig(
        backend_project_path=backend_dir,
        entrypoint="clarity-backend",
        auto_submit=True,
        use_uv=True,
    )

    result = submit_clarity_manifest(runner, manifest_path)

    assert result["status"] == "submitted"
    assert calls and calls[0][-2:] == ["run-manifest", manifest_path.resolve().as_posix()]


def test_submit_clarity_manifest_keeps_submission_state_on_non_zero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_dir = tmp_path / "clarity-backend"
    backend_dir.mkdir()
    (backend_dir / "pyproject.toml").write_text("[project]\nname='clarity-backend'\nversion='0.1.0'\n", encoding="utf-8")
    manifest_path = tmp_path / "clarity_jobs.jsonl"
    manifest_path.write_text("", encoding="utf-8")

    class CompletedProcess:
        returncode = 1

    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_runner.shutil.which", lambda _: "uv")
    monkeypatch.setattr(
        "acoustic_orchestrator.pipeline.clarity_runner.subprocess.run",
        lambda command, check: CompletedProcess(),
    )
    runner = ClarityRunnerConfig(
        backend_project_path=backend_dir,
        entrypoint="clarity-backend",
        auto_submit=True,
        use_uv=True,
    )

    result = submit_clarity_manifest(runner, manifest_path)

    assert result["status"] == "submitted"
    assert "exit code 1" in result["message"]
