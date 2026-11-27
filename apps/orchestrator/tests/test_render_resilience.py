"""Pipeline-level restart/regression tests; no real MATLAB or experiment files."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline import render_pipeline
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.matlab_runner import render_manifest_with_matlab
from acoustic_orchestrator.pipeline.render_acceptance import evaluate_render, persist_acceptance
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes
from test_generate_manifests import _build_workspace, _write_config, _prepare_legacy_render_fixture
from test_render_diagnostics_integration import process_manifest, successful_process


def configure(tmp_path, *, scenes=2, workers=1, attempts=3, overwrite=False, resume=True):
    workspace = _build_workspace(tmp_path)
    path = _write_config(workspace, "resilience", num_simulations=scenes, num_workers=workers,
                         resume_if_possible=resume, include_optional_hartf_outputs=False,
                         total_duration_s=0.01, save_render_metadata=True)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["execution"].update(overwrite_existing=overwrite, matlab_process_retry={
        "max_attempts": attempts, "initial_delay_s": 0, "max_delay_s": 0,
        "max_consecutive_failures": 5,
    })
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path, resolve_artifact_layout(load_config(path).outputs, "sim_test")


def evidence(layout):
    roots = [layout[key] for key in ("scene_manifest_dir", "runtime_manifest_dir", "prepared_audio_dir", "render_outputs_root")]
    return {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for root in roots for p in root.rglob("*") if p.is_file()}


def test_protected_complete_restart_does_not_rewrite_any_inputs_or_outputs(tmp_path, monkeypatch):
    path, layout = configure(tmp_path)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", successful_process)
    render_static_scenes(path)
    before = evidence(layout)
    attempts = set(layout["render_attempts_dir"].rglob("outcome.json"))
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("valid renders must resume"))
    _, summary = render_static_scenes(path)
    assert summary["resumed_variants"] == 2
    assert summary["render_jobs"] == summary["matlab_invocations"] == 0
    assert evidence(layout) == before
    assert set(layout["render_attempts_dir"].rglob("outcome.json")) == attempts


def test_partial_lot_restart_preserves_valid_results_and_retries_only_pending(tmp_path, monkeypatch):
    path, layout = configure(tmp_path, attempts=1)
    config = load_config(path)
    manifests = render_pipeline.generate_static_manifests(path)
    _prepare_legacy_render_fixture(config, read_json_object(manifests[0]), "binaural_hrtf")
    # Build a genuine historical standalone attempt (no new chain metadata),
    # matching an experiment created before automatic retry chains existed.
    variant = render_pipeline._build_planned_variants_from_manifest(
        config, read_json_object(manifests[1]), layout["runtime_manifest_dir"])[0]
    from acoustic_orchestrator.pipeline.runtime_audio import prepare_scene_source_assets
    rewrites = prepare_scene_source_assets(variant["scene_manifest"],
                                           layout["prepared_audio_dir"] / variant["scene_manifest"]["scene_id"])
    task = render_pipeline._prepare_render_task(config, variant, layout["runtime_manifest_dir"], rewrites)
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", fail)
    process = render_manifest_with_matlab(task["runtime_manifest_path"],
                                         attempts_dir=layout["render_attempts_dir"],
                                         diagnostics_dir=layout["matlab_diagnostics_dir"])
    persist_acceptance(evaluate_render(process, task["variant_paths"], metadata_required=True, canonical=True),
                       lambda: None)
    before = {p: value for p, value in evidence(layout).items() if "0001" in str(p)}
    calls = []

    def succeed(command, **kwargs):
        calls.append(process_manifest(command)["scene_id"])
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", succeed)
    render_static_scenes(path)
    assert calls == ["scene_static_0002"]
    assert all(evidence(layout)[p] == value for p, value in before.items())


def test_generic_process_failure_retries_with_independent_attempts(tmp_path, monkeypatch):
    path, layout = configure(tmp_path, scenes=1)
    calls = []

    def run(command, **kwargs):
        manifest = process_manifest(command)
        calls.append(manifest)
        if len(calls) <= 2:
            if kwargs.get("stdout"):
                kwargs["stdout"].write(b"unrelated vendor startup failure\n")
            raise subprocess.CalledProcessError(17, command)
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    _, summary = render_static_scenes(path)
    assert summary["render_jobs"] == 1
    assert summary["matlab_invocations"] == 3
    assert summary["process_retries"] == summary["failed_matlab_processes"] == 2
    assert summary["variants_completed_after_retry"] == 1
    assert summary["recovered_matlab_processes"] == 0
    outcomes = [read_json_object(p) for p in layout["render_attempts_dir"].rglob("outcome.json")]
    assert len({o["attempt_id"] for o in outcomes}) == 3
    assert len({o["manifest_sha256"] for o in outcomes}) == 1
    assert sorted(o["exit_code"] for o in outcomes) == [0, 17, 17]
    assert len(calls) == 3


def test_corrupt_late_scene_conflicts_before_mutating_existing_files(tmp_path, monkeypatch):
    path, layout = configure(tmp_path)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", successful_process)
    render_static_scenes(path)
    late = layout["scene_manifest_dir"] / "scene_static_0002.json"
    late.write_text('{"scene_id":"wrong","scene_id":"duplicate"}', encoding="utf-8")
    before = evidence(layout)
    index = layout["render_index_path"].read_bytes()
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("preflight must stop before MATLAB"))
    with pytest.raises((ValueError, RuntimeError)):
        render_static_scenes(path)
    assert evidence(layout) == before
    assert layout["render_index_path"].read_bytes() == index


@pytest.mark.parametrize("artifact", ["rendered_wav_path", "render_metadata_path"])
def test_invalid_preexisting_output_is_not_overwritten(tmp_path, monkeypatch, artifact):
    path, layout = configure(tmp_path, scenes=1)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", successful_process)
    render_static_scenes(path)
    record = next(iter(load_variant_index(layout["render_index_path"]).values()))
    Path(record[artifact]).write_bytes(b"partial")
    before = evidence(layout)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("protected output must not be overwritten"))
    with pytest.raises((ValueError, RuntimeError, FileExistsError)):
        render_static_scenes(path)
    assert evidence(layout) == before


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("overwrite", [False, True])
def test_execution_option_matrix_on_valid_existing_run(tmp_path, monkeypatch, resume, overwrite):
    path, layout = configure(tmp_path, scenes=1, resume=True, overwrite=False)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return successful_process(command, **kwargs)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    render_static_scenes(path)
    before = evidence(layout)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["execution"].update(resume_if_possible=resume, overwrite_existing=overwrite)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    if not resume and not overwrite:
        with pytest.raises((ValueError, FileExistsError, RuntimeError)):
            render_static_scenes(path)
        assert len(calls) == 1
        assert evidence(layout) == before
    else:
        _, summary = render_static_scenes(path)
        assert len(calls) == (1 if resume else 2)
        assert summary["resumed_variants"] == (1 if resume else 0)
        if resume:
            assert evidence(layout) == before


def test_exhausted_chain_restart_does_not_renew_budget(tmp_path, monkeypatch):
    path, layout = configure(tmp_path, scenes=1, attempts=2)
    calls = []
    def fail(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(9, command)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", fail)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)
    assert len(calls) == 2
    before = set(layout["render_attempts_dir"].rglob("outcome.json"))
    with pytest.raises((RenderStaticRunError, RuntimeError, ValueError)):
        render_static_scenes(path)
    assert len(calls) == 2
    assert set(layout["render_attempts_dir"].rglob("outcome.json")) == before


def test_new_attempt_archives_partial_output_without_accepting_it(tmp_path, monkeypatch):
    path, layout = configure(tmp_path, scenes=1)
    calls = []
    def run(command, **kwargs):
        manifest = process_manifest(command)
        output = Path(manifest["render"]["output_wav_path"])
        calls.append(command)
        if len(calls) == 1:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"incomplete PCM")
            raise subprocess.CalledProcessError(8, command)
        assert not output.exists()
        return successful_process(command, **kwargs)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    _, summary = render_static_scenes(path)
    assert len(calls) == 2
    assert summary["completed_variants"] == 1
    assert summary["variants_completed_after_retry"] == 1
    archived = [p for p in layout["render_attempts_dir"].rglob("*")
                if p.is_file() and p.suffix == ".wav"]
    assert any(p.read_bytes() == b"incomplete PCM" for p in archived)


@pytest.mark.parametrize("code", [0, 1])
def test_functional_input_rejection_never_accepts_or_retries(tmp_path, monkeypatch, code):
    path, layout = configure(tmp_path, scenes=1)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        attempt_id = Path(kwargs["stdout"].name).parent.name
        directory = layout["render_attempts_dir"] / attempt_id
        outcome = read_json_object(directory / "outcome.json")
        report = {key: outcome[key] for key in ("attempt_id", "variant_id", "manifest_sha256")}
        report.update(schema_version=1, category="invalid_input",
                      error={"identifier": "Input:Invalid", "message": "invalid renderer input"})
        (directory / "failure.json").write_text(json.dumps(report), encoding="utf-8")
        if code == 0:
            return successful_process(command, **kwargs)
        raise subprocess.CalledProcessError(code, command)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)
    assert len(calls) == 1


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("state", ["absent", "partial", "incompatible"])
def test_execution_option_matrix_on_pending_or_invalid_outputs(tmp_path, monkeypatch, resume, overwrite, state):
    path, layout = configure(tmp_path, scenes=1)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return successful_process(command, **kwargs)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    render_static_scenes(path)
    record = next(iter(load_variant_index(layout["render_index_path"]).values()))
    wav = Path(record["rendered_wav_path"])
    metadata = Path(record["render_metadata_path"])
    if state == "absent":
        wav.unlink()
        metadata.unlink()
    elif state == "partial":
        wav.write_bytes(b"truncated")
    else:
        payload = read_json_object(metadata)
        payload["summary"]["sources"][0]["gain_db"] += 1
        metadata.write_text(json.dumps(payload), encoding="utf-8")
    before = evidence(layout)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["execution"].update(resume_if_possible=resume, overwrite_existing=overwrite)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    if not overwrite and (not resume or state != "absent"):
        with pytest.raises((ValueError, RuntimeError, FileExistsError)):
            render_static_scenes(path)
        assert len(calls) == 1
        assert evidence(layout) == before
    else:
        _, summary = render_static_scenes(path)
        assert len(calls) == 2
        assert summary["completed_variants"] == 1
        assert summary["resumed_variants"] == 0


def test_exhausted_t30_is_terminal_not_a_missing_scene_to_regenerate(tmp_path, monkeypatch):
    path, layout = configure(tmp_path, scenes=1, attempts=1)
    original_sample = render_pipeline.sample_static_scene
    calls = []
    def sample(config, rng, index, **kwargs):
        scene = original_sample(config, rng, index, **kwargs)
        scene["reverberation_sampling"]["attempts"] = config.scene_validation.max_scene_attempts
        return scene
    def run(command, **kwargs):
        calls.append(command)
        result = successful_process(command, **kwargs)
        from test_generate_manifests import _write_valid_raven_metadata
        _write_valid_raven_metadata(process_manifest(command), [0.5] * 9)
        return result
    monkeypatch.setattr(render_pipeline, "sample_static_scene", sample)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    manifests, first = render_static_scenes(path)
    assert manifests == [] and first["sampling"]["failed_scenes"] == 1
    assert len(calls) == 1
    history = [json.loads(line) for line in layout["sampling_failures_path"].read_text().splitlines()]
    assert history[-1]["stage"] == "raven_t30"
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("restart must not renew acoustic budget"))
    manifests, second = render_static_scenes(path)
    assert manifests == [] and second["sampling"]["failed_scenes"] == 1
    assert second["matlab_invocations"] == 0
    assert [json.loads(line) for line in layout["sampling_failures_path"].read_text().splitlines()] == history


@pytest.mark.parametrize("exhausted", [False, True])
def test_cli_reports_process_attempts_separately_from_render_jobs(tmp_path, monkeypatch, exhausted):
    from typer.testing import CliRunner
    from acoustic_orchestrator.cli import app
    path, layout = configure(tmp_path, scenes=1, attempts=2)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1 or exhausted:
            raise subprocess.CalledProcessError(29, command)
        return successful_process(command, **kwargs)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    result = CliRunner().invoke(app, ["render-static", str(path)])
    assert result.exit_code == (1 if exhausted else 0), result.output
    assert "invocaciones=2 reintentos=1" in result.output
    assert f"variantes_tras_reintento={0 if exhausted else 1}" in result.output
    summary = read_json_object(layout["render_index_path"].parent / "static_run_summary.json")["render"]
    assert summary["render_jobs"] == 1
    assert summary["matlab_invocations"] == len(calls) == 2
    outcomes = [read_json_object(p) for p in layout["render_attempts_dir"].glob("*/outcome.json")]
    assert all(o.get("chain_id") and o.get("retry_decision") for o in outcomes)


def test_restarted_archive_failure_preserves_original_process_error(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.process_retries import RetryChain
    path, _ = configure(tmp_path, scenes=1)
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(37, command)
    def interrupt_archive(self):
        raise KeyboardInterrupt("orchestrator interrupted after durable retry scheduling")
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", fail)
    monkeypatch.setattr(RetryChain, "archive_attempt", interrupt_archive)
    with pytest.raises(KeyboardInterrupt):
        render_static_scenes(path)
    def unavailable_archive(self):
        raise OSError("archive storage unavailable")
    monkeypatch.setattr(RetryChain, "archive_attempt", unavailable_archive)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("must not launch after archive storage error"))
    with pytest.raises(RenderStaticRunError) as caught:
        render_static_scenes(path)
    original = caught.value.__cause__
    assert isinstance(original, subprocess.CalledProcessError)
    assert original.returncode == 37
    assert isinstance(original.__cause__, OSError)
    assert any("chain" in note.lower() or "cadena" in note.lower() for note in original.__notes__)
    assert caught.value.summary["matlab_invocations"] == 0


def test_explicit_fresh_execution_supersedes_a_scheduled_chain(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.process_retries import RetryChain
    path, layout = configure(tmp_path, scenes=1)
    original_archive = RetryChain.archive_attempt
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(31, command)
    def interrupt(self):
        raise KeyboardInterrupt("interrupt before retry archive")
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", fail)
    monkeypatch.setattr(RetryChain, "archive_attempt", interrupt)
    with pytest.raises(KeyboardInterrupt):
        render_static_scenes(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["execution"].update(resume_if_possible=False, overwrite_existing=True)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setattr(RetryChain, "archive_attempt", original_archive)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", successful_process)
    _, summary = render_static_scenes(path)
    outcomes = [read_json_object(p) for p in layout["render_attempts_dir"].glob("*/outcome.json")]
    assert len(outcomes) == 2
    assert len({o["chain_id"] for o in outcomes}) == 2
    assert all(o["attempt_ordinal"] == 1 for o in outcomes)
    assert summary["process_retries"] == 0
    assert sorted(o["decision"] for o in outcomes) == ["abort", "continue"]


def test_interrupted_partial_lot_resumes_valid_scene_and_remaining_retry_budget(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.process_retries import RetryChain
    path, layout = configure(tmp_path, scenes=3, attempts=3)
    original_archive = RetryChain.archive_attempt
    calls = []
    def initial(command, **kwargs):
        scene_id = process_manifest(command)["scene_id"]
        calls.append(scene_id)
        if scene_id.endswith("0002"):
            raise subprocess.CalledProcessError(17, command)
        return successful_process(command, **kwargs)
    def interrupt(self):
        raise KeyboardInterrupt("orchestrator interrupted before the next process")
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", initial)
    monkeypatch.setattr(RetryChain, "archive_attempt", interrupt)
    with pytest.raises(KeyboardInterrupt):
        render_static_scenes(path)
    assert calls == ["scene_static_0001", "scene_static_0002"]
    protected = {p: value for p, value in evidence(layout).items() if "0001" in str(p)}
    second_calls = []
    def remaining(command, **kwargs):
        scene_id = process_manifest(command)["scene_id"]
        second_calls.append(scene_id)
        assert not scene_id.endswith("0001"), "accepted first scene must not run again"
        if len(second_calls) == 1:
            raise subprocess.CalledProcessError(19, command)
        return successful_process(command, **kwargs)
    monkeypatch.setattr(RetryChain, "archive_attempt", original_archive)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", remaining)
    _, summary = render_static_scenes(path)
    assert second_calls == ["scene_static_0002", "scene_static_0002", "scene_static_0003"]
    assert summary["resumed_variants"] == 1
    assert summary["completed_variants"] == 3
    assert summary["render_jobs"] == 2
    assert summary["matlab_invocations"] == 3
    assert summary["variants_completed_after_retry"] == 1
    assert all(evidence(layout)[p] == value for p, value in protected.items())
    second_outcomes = [read_json_object(p) for p in layout["render_attempts_dir"].glob("*/outcome.json")
                       if read_json_object(p)["scene_id"] == "scene_static_0002"]
    assert len({o["chain_id"] for o in second_outcomes}) == 1
    assert sorted(o["attempt_ordinal"] for o in second_outcomes) == [1, 2, 3]
    assert sum(o["decision"] == "abort" for o in second_outcomes) == 2
    assert sum(o["decision"] == "continue" for o in second_outcomes) == 1
