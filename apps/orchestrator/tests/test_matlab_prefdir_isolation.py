"""Per-process MATLAB preference isolation (MATLAB_PREFDIR) in the MATLAB runner."""

import json
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from acoustic_orchestrator.pipeline import matlab_runner
from acoustic_orchestrator.pipeline.matlab_runner import render_manifest_with_matlab
from acoustic_orchestrator.pipeline.render_attempt import read_json_object

RUN = "acoustic_orchestrator.pipeline.matlab_runner.subprocess.run"


def write_runtime(tmp_path: Path, name: str = "scene") -> Path:
    path = tmp_path / f"{name}__binaural_hrtf.json"
    path.write_text(json.dumps({
        "schema_version": "3.0", "scene_id": name,
        "render": {"active_output_type": "binaural_hrtf",
                   "output_wav_path": (tmp_path / f"{name}.wav").as_posix(),
                   "output_metadata_path": (tmp_path / f"{name}.json").as_posix()},
    }, indent=2) + "\n", encoding="utf-8")
    return path


def recording_run(seen: list[Path], returncode: int = 0):
    def run(command, **kwargs):
        prefdir = Path(kwargs["env"]["MATLAB_PREFDIR"])
        assert prefdir.is_dir() and not any(prefdir.iterdir())
        (prefdir / "matlabprefs.mat").write_bytes(b"written by MATLAB")
        seen.append(prefdir)
        return SimpleNamespace(returncode=returncode)
    return run


def test_matlab_receives_private_empty_prefdir_outside_run_root(tmp_path, monkeypatch):
    monkeypatch.delenv("MATLAB_PREFDIR", raising=False)
    seen: list[Path] = []
    monkeypatch.setattr(RUN, recording_run(seen))
    render_manifest_with_matlab(write_runtime(tmp_path))
    assert len(seen) == 1
    assert seen[0].name.startswith("binascape-matlab-prefs-")
    assert not seen[0].resolve().is_relative_to(tmp_path.resolve())


def test_inherited_prefdir_is_overridden(tmp_path, monkeypatch):
    inherited = tmp_path / "user_prefs"
    inherited.mkdir()
    monkeypatch.setenv("MATLAB_PREFDIR", str(inherited))
    seen: list[Path] = []
    monkeypatch.setattr(RUN, recording_run(seen))
    render_manifest_with_matlab(write_runtime(tmp_path))
    assert seen[0] != inherited
    assert not any(inherited.iterdir())


def test_concurrent_attempts_get_distinct_prefdirs(tmp_path, monkeypatch):
    barrier = threading.Barrier(2, timeout=10)
    seen: list[Path] = []
    record = recording_run(seen)

    def run(command, **kwargs):
        record(command, **kwargs)
        barrier.wait()  # Both processes are alive at the same time.
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(RUN, run)
    runtimes = [write_runtime(tmp_path, f"scene{index}") for index in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(render_manifest_with_matlab, runtimes))
    assert len(set(seen)) == 2
    assert all(result.process_status == "succeeded" for result in results)


@pytest.mark.parametrize(("returncode", "status"), [(0, "succeeded"), (1, "failed")])
def test_prefdir_removed_after_process_exit(tmp_path, monkeypatch, returncode, status):
    seen: list[Path] = []
    monkeypatch.setattr(RUN, recording_run(seen, returncode))
    result = render_manifest_with_matlab(write_runtime(tmp_path))
    assert result.process_status == status and result.exit_code == returncode
    assert isinstance(result.error, subprocess.CalledProcessError) == (returncode != 0)
    assert not seen[0].exists()
    outcome = read_json_object(result.attempt.outcome_path)
    assert outcome["process_status"] == status and outcome["exit_code"] == returncode
    assert outcome["matlab_prefdir"] == {"path": seen[0].as_posix(), "cleanup": "removed"}


def test_prefdir_removed_after_launch_failure(tmp_path, monkeypatch):
    seen: list[Path] = []

    def run(command, **kwargs):
        seen.append(Path(kwargs["env"]["MATLAB_PREFDIR"]))
        raise FileNotFoundError("matlab")

    monkeypatch.setattr(RUN, run)
    result = render_manifest_with_matlab(write_runtime(tmp_path))
    assert result.process_status == "launch_failed" and isinstance(result.error, FileNotFoundError)
    assert result.exit_code is None and not result.termination_observed
    assert not seen[0].exists()
    outcome = read_json_object(result.attempt.outcome_path)
    assert outcome["process_status"] == "launch_failed"
    assert outcome["matlab_prefdir"] == {"path": seen[0].as_posix(), "cleanup": "removed"}


@pytest.mark.parametrize("returncode", [0, 1])
def test_cleanup_failure_warns_and_keeps_outcome(tmp_path, monkeypatch, returncode):
    seen: list[Path] = []
    monkeypatch.setattr(RUN, recording_run(seen, returncode))
    baseline = render_manifest_with_matlab(write_runtime(tmp_path, "baseline"))

    real_rmtree = shutil.rmtree

    def failing_rmtree(path, *args, **kwargs):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(matlab_runner.shutil, "rmtree", failing_rmtree)
    try:
        with pytest.warns(RuntimeWarning, match="MATLAB_PREFDIR"):
            result = render_manifest_with_matlab(write_runtime(tmp_path, "locked"))
        assert seen[1].exists()
    finally:
        real_rmtree(seen[1], ignore_errors=True)

    for field in ("process_status", "exit_code", "termination_observed"):
        assert getattr(result, field) == getattr(baseline, field)
    assert type(result.error) is type(baseline.error)
    outcome = read_json_object(result.attempt.outcome_path)
    assert outcome["matlab_prefdir"] == {"path": seen[1].as_posix(), "cleanup": "failed"}
    expected = read_json_object(baseline.attempt.outcome_path)
    for field in ("process_status", "exit_code", "termination_observed", "decision"):
        assert outcome[field] == expected[field]
    assert (outcome["error"] or {}).get("type") == (expected["error"] or {}).get("type")
