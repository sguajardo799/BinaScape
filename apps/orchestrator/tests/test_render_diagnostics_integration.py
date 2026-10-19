"""Real runner and orchestration, with only the MATLAB process simulated."""

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.output_index import should_resume_variant
from acoustic_orchestrator.pipeline.matlab_diagnostics import MatlabDiagnostics
from test_matlab_diagnostics import write_events
from acoustic_orchestrator.pipeline import render_pipeline
from test_generate_manifests import _build_workspace, _write_config, _write_valid_raven_metadata


def process_manifest(command):
    match = re.search(r"run_raven_static_render\('((?:[^']|'')*)'", command[2])
    return json.loads(Path(match[1].replace("''", "'")).read_text(encoding="utf-8"))


def successful_process(command, **kwargs):
    manifest = process_manifest(command)
    wav = Path(manifest["render"]["output_wav_path"])
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(b"simulated render")
    _write_valid_raven_metadata(manifest)
    if kwargs.get("stdout"):
        kwargs["stdout"].write(b"process completed\n")
    return SimpleNamespace(returncode=0)


@pytest.mark.parametrize("workers", [1, 3])
def test_pipeline_attempt_directory_and_resume(tmp_path, monkeypatch, workers):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "diagnostics", num_simulations=1, num_workers=workers, resume_if_possible=True)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return successful_process(command, **kwargs)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    _, first = render_static_scenes(config_path)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    attempts = list(layout["matlab_diagnostics_dir"].iterdir())
    assert len(attempts) == len(calls) == first["render_jobs"] == 3
    assert all((path / "invocation.json").is_file() for path in attempts)
    assert not (layout["runtime_manifest_dir"] / "matlab_diagnostics").exists()
    _, second = render_static_scenes(config_path)
    assert second["render_jobs"] == 0
    assert second["resumed_variants"] == 3
    assert len(calls) == 3
    assert set(layout["matlab_diagnostics_dir"].iterdir()) == set(attempts)


def test_t30_retries_preserve_rejected_attempt_snapshot_and_logs(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "retry_diagnostics", num_simulations=1, num_workers=3)
    original_sample = render_pipeline.sample_static_scene
    def sample(config, rng, scene_index, **kwargs):
        result = original_sample(config, rng, scene_index, **kwargs)
        if not kwargs:
            result["reverberation_sampling"]["attempts"] = 1
        return result
    calls = []
    def run(command, **kwargs):
        manifest = process_manifest(command)
        calls.append(manifest["render"]["active_output_type"])
        result = successful_process(command, **kwargs)
        if len(calls) == 1:
            _write_valid_raven_metadata(manifest, [0.5] * 9)
        directory = Path(kwargs["stdout"].name).parent
        kwargs["stdout"].write(directory.name.encode())
        return result
    monkeypatch.setattr(render_pipeline, "sample_static_scene", sample)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    manifests, summary = render_static_scenes(config_path)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    attempts = list(layout["matlab_diagnostics_dir"].iterdir())
    records = [json.loads((path / "invocation.json").read_text()) for path in attempts]
    assert len(attempts) == summary["render_jobs"] == 4
    assert summary["completed_variants"] == 3
    assert calls[:2] == ["binaural_hrtf", "binaural_hrtf"]
    canonical = [record for record in records if record["output_type"] == "binaural_hrtf"]
    snapshots = [json.loads(Path(record["manifest_snapshot_path"]).read_text()) for record in canonical]
    assert len(canonical) == 2
    assert len({record["attempt_id"] for record in canonical}) == 2
    assert len({snapshot["reverberation_sampling"]["attempts"] for snapshot in snapshots}) == 2
    assert sum(len(snapshot["reverberation_sampling"].get("raven_t30_rejections", [])) for snapshot in snapshots) == 1
    for record in records:
        log = Path(record["console_path"]).read_text()
        assert record["attempt_id"] in log
        assert not any(other["attempt_id"] in log for other in records if other is not record)
    final_scene = json.loads(manifests[0].read_text())
    assert final_scene["reverberation_sampling"]["raven_retry_count"] == 1


@pytest.mark.parametrize("preexisting", [False, True])
def test_completed_artifacts_and_failed_process_remain_independent(tmp_path, monkeypatch, preexisting):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "failed_process", num_simulations=1, resume_if_possible=False)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", successful_process)
    if preexisting:
        render_static_scenes(config_path)
    original_error = subprocess.CalledProcessError(31, "matlab")
    failed_directory = []
    def fail(command, **kwargs):
        directory = Path(kwargs["stdout"].name).parent
        failed_directory.append(directory)
        if not preexisting:
            successful_process(command, **kwargs)
            write_events(SimpleNamespace(attempt_id=directory.name, events_path=directory / "events.jsonl"),
                         [{"type": "entrypoint_completed"}])
        raise original_error
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", fail)
    with pytest.raises(RenderStaticRunError) as caught:
        render_static_scenes(config_path)
    assert caught.value.__cause__ is original_error
    assert caught.value.summary["completed_variants"] == 1
    records = load_variant_index(layout["render_index_path"])
    canonical = records["scene_static_0001__binaural_hrtf"]
    assert canonical["status"] == "completed"
    assert canonical["validation"]["ok"] is True
    evidence = json.loads((failed_directory[0] / "invocation.json").read_text())
    assert evidence["process_status"] == "failed"
    assert evidence["exit_code"] == 31
    assert evidence["entrypoint_completed"] is (not preexisting)
    assert evidence["failure_location"] == ("indeterminate" if preexisting else "post_entrypoint")
    assert all(a["observed_before"] is preexisting for a in evidence["artifacts"])
    assert all(a["observed_after"] is True for a in evidence["artifacts"])
    assert all(a["write_confirmed"] is False for a in evidence["artifacts"])


@pytest.mark.parametrize("process_fails", [False, True])
@pytest.mark.parametrize("diagnostic_failure", ["directory", "events", "summary"])
def test_diagnostic_failure_does_not_change_pipeline_state(tmp_path, monkeypatch, process_fails, diagnostic_failure):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "state_invariance", num_simulations=1, resume_if_possible=False)
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, "sim_test")
    original_error = subprocess.CalledProcessError(41, "matlab")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        result = successful_process(command, **kwargs)
        if process_fails:
            raise original_error
        return result
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    def execute_and_observe():
        if process_fails:
            with pytest.raises(RenderStaticRunError) as caught:
                render_static_scenes(config_path)
            assert caught.value.__cause__ is original_error
            result = caught.value.summary
        else:
            _, result = render_static_scenes(config_path)
        records = load_variant_index(layout["render_index_path"])
        resume = {key: should_resume_variant(records, record, metadata_required=True) for key, record in records.items()}
        normalized = {key: {field: value for field, value in record.items() if field != "updated_at"}
                      for key, record in records.items()}
        return result, normalized, resume
    baseline = execute_and_observe()
    baseline_calls = len(calls)
    def fail(*args, **kwargs):
        raise OSError("diagnostic system unavailable")
    if diagnostic_failure == "directory":
        original_mkdir = Path.mkdir
        def mkdir(path, *args, **kwargs):
            if path.is_relative_to(layout["matlab_diagnostics_dir"]):
                fail()
            return original_mkdir(path, *args, **kwargs)
        monkeypatch.setattr(Path, "mkdir", mkdir)
    elif diagnostic_failure == "events":
        monkeypatch.setattr(MatlabDiagnostics, "read_events", fail)
    else:
        monkeypatch.setattr(MatlabDiagnostics, "save", fail)
    with pytest.warns(RuntimeWarning):
        actual = execute_and_observe()
    assert actual == baseline
    assert len(calls) == 2 * baseline_calls
