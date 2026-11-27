from pathlib import Path
import json

import pytest

from acoustic_orchestrator.config.models import ClarityRunnerConfig
from acoustic_orchestrator.pipeline.clarity_runner import build_clarity_command, submit_clarity_manifest


@pytest.mark.parametrize("output_type", ["circular_array", "bte_rear_hartf", "bte_front_hartf", "bte_rear_hartf__1"])
def test_submission_blocks_ineligible_stale_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output_type: str) -> None:
    manifest = tmp_path / "jobs.jsonl"
    manifest.write_text(json.dumps({"output_type": output_type}), encoding="utf-8")
    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_runner.subprocess.run", lambda *a, **k: pytest.fail("must not submit"))
    result = submit_clarity_manifest(ClarityRunnerConfig(use_uv=False), manifest)
    assert result["status"] == "blocked"
    assert "no elegible" in result["message"]


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
    assert calls and calls[0][-3:] == ["run-manifest", manifest_path.resolve().as_posix(), "--no-progress"]


@pytest.mark.parametrize("use_uv", [False, True])
def test_progress_policy_and_parent_suspension(tmp_path, monkeypatch, use_uv):
    from io import StringIO
    from acoustic_orchestrator.pipeline.progress import ProgressReporter
    from acoustic_orchestrator.terminal_progress import TerminalProgress

    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text("[project]\nname='backend'\nversion='0.1.0'\n")
    runner = ClarityRunnerConfig(use_uv=use_uv, backend_project_path=backend)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_runner.shutil.which", lambda _: "uv")
    manifest = tmp_path / "jobs.jsonl"
    manifest.write_text('{"output_type":"binaural_hrtf"}')
    stream = StringIO()
    stream.isatty = lambda: True
    calls = []

    with TerminalProgress(stream=stream) as terminal:
        report = ProgressReporter(terminal)

        def run(command, **kwargs):
            calls.append(command)
            assert terminal.suspended
            assert terminal.live is None
            return type("Completed", (), {"returncode": 0})()

        monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_runner.subprocess.run", run)
        submit_clarity_manifest(runner, manifest, progress=report)
        assert not terminal.suspended
    assert "--no-progress" not in calls[0]
    assert build_clarity_command(runner, manifest, no_progress=True)[-1] == "--no-progress"


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


def _whis_install(root: Path, *, gcfb: bool = True) -> Path:
    (root / "WHIS" / "WHISv302").mkdir(parents=True)
    if gcfb:
        (root / "gammachirp-filterbank" / "GCFBv234").mkdir(parents=True)
    return root


def _mixed_manifest(path: Path, install_root: Path, matlab: str = "matlab") -> None:
    jobs = [
        {"job_id": "clarity__v__p__msbg", "output_type": "binaural_hrtf", "processor": "msbg",
         "processor_config": {"level_handling": "true_level"}},
        {"job_id": "clarity__v__p__whis", "output_type": "binaural_hrtf", "processor": "whis",
         "processor_config": {"install_root": install_root.as_posix(), "matlab_executable": matlab, "synth_method": "dtvf"}},
    ]
    path.write_text("".join(json.dumps(job) + "\n" for job in jobs), encoding="utf-8")


def _direct_runner(monkeypatch: pytest.MonkeyPatch, which=lambda name: f"/bin/{name}") -> tuple[ClarityRunnerConfig, list]:
    calls: list[list[str]] = []
    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_runner.shutil.which", which)
    monkeypatch.setattr(
        "acoustic_orchestrator.pipeline.clarity_runner.subprocess.run",
        lambda command, check: calls.append(command) or type("Completed", (), {"returncode": 0})(),
    )
    return ClarityRunnerConfig(use_uv=False), calls


def test_missing_gcfb_blocks_whis_jobs_and_still_submits_msbg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_root = _whis_install(tmp_path / "whis", gcfb=False)
    manifest = tmp_path / "jobs.jsonl"
    _mixed_manifest(manifest, install_root)
    runner, calls = _direct_runner(monkeypatch)

    result = submit_clarity_manifest(runner, manifest)

    assert result["status"] == "submitted"
    assert len(calls) == 1
    assert list(result["blocked_jobs"]) == ["clarity__v__p__whis"]
    assert "gammachirp-filterbank/GCFBv234" in result["blocked_jobs"]["clarity__v__p__whis"]
    remaining = [json.loads(line)["job_id"] for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert remaining == ["clarity__v__p__msbg"]


def test_missing_matlab_blocks_whis_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_root = _whis_install(tmp_path / "whis")
    manifest = tmp_path / "jobs.jsonl"
    _mixed_manifest(manifest, install_root, matlab="matlab-r2099")
    runner, calls = _direct_runner(monkeypatch, which=lambda name: None if name.startswith("matlab") else f"/bin/{name}")

    result = submit_clarity_manifest(runner, manifest)

    assert "no se encontró el ejecutable de MATLAB 'matlab-r2099'" in result["blocked_jobs"]["clarity__v__p__whis"]
    assert len(calls) == 1


def test_complete_whis_environment_submits_every_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "jobs.jsonl"
    _mixed_manifest(manifest, _whis_install(tmp_path / "whis"))
    runner, calls = _direct_runner(monkeypatch)

    result = submit_clarity_manifest(runner, manifest)

    assert result["status"] == "submitted" and result["blocked_jobs"] == {}
    assert len(manifest.read_text(encoding="utf-8").splitlines()) == 2


def test_manifest_with_only_blocked_whis_jobs_is_not_submitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "jobs.jsonl"
    manifest.write_text(json.dumps({
        "job_id": "clarity__v__p__whis", "output_type": "binaural_hrtf", "processor": "whis",
        "processor_config": {"install_root": (tmp_path / "missing").as_posix(), "matlab_executable": "matlab"},
    }) + "\n", encoding="utf-8")
    runner, calls = _direct_runner(monkeypatch)

    result = submit_clarity_manifest(runner, manifest)

    assert result["status"] == "blocked" and not calls
    assert "WHIS/WHISv302" in result["message"]
