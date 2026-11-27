import json
import re
from pathlib import Path
import subprocess
import yaml
from types import SimpleNamespace
from typer.testing import CliRunner
from acoustic_orchestrator.cli import app

import pytest

from acoustic_orchestrator.pipeline.matlab_runner import MatlabProcessResult
from acoustic_orchestrator.pipeline.render_acceptance import RenderValidationError, evaluate_render
from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt
from render_fixtures import write_completion_for_attempt, write_render_metadata, write_variant_fixture
from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.output_index import should_resume_variant
from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes
from acoustic_orchestrator.pipeline import render_pipeline
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_acceptance import persist_acceptance
from acoustic_orchestrator.pipeline.render_history import load_render_history
from acoustic_orchestrator.pipeline.render_validation import equivalent_render_inputs
from acoustic_orchestrator.pipeline.clarity_handoff import prepare_clarity_handoff
from acoustic_orchestrator.pipeline import clarity_handoff
from test_clarity_handoff import _hearing_profiles_yaml
from acoustic_orchestrator.pipeline.matlab_diagnostics import MatlabDiagnostics
from test_generate_manifests import _build_workspace, _write_config, _write_valid_raven_metadata
from test_generate_manifests import _prepare_legacy_render_fixture
from test_render_diagnostics_integration import process_manifest, successful_process


def make_process(tmp_path, code, *, receipt=True, t30=None):
    paths = {"variant_id": "scene__binaural_hrtf", "scene_id": "scene", "output_type": "binaural_hrtf",
             "runtime_manifest_path": str(tmp_path / "scene__binaural_hrtf.json"),
             "rendered_wav_path": str(tmp_path / "out.wav"), "render_metadata_path": str(tmp_path / "out.json"),
             "receiver_ir_path": str(tmp_path / "receiver.daff")}
    manifest = write_variant_fixture(paths, metadata=True)
    if t30 is not None:
        write_render_metadata(manifest, t30)
    attempt = RenderAttempt(Path(paths["runtime_manifest_path"]))
    if receipt:
        write_completion_for_attempt(attempt)
    error = subprocess.CalledProcessError(code, "matlab") if code else None
    if code is None:
        error = FileNotFoundError("matlab unavailable")
    process = MatlabProcessResult(attempt, code, error, code is not None,
                                  "succeeded" if code == 0 else "failed" if code is not None else "launch_failed")
    return process, paths


@pytest.mark.parametrize("code,receipt,decision", [
    (0, True, "continue"), (0, False, "continue"),
    (1, True, "continue_with_warning"), (7, True, "continue_with_warning"),
    (1, False, "abort"), (None, True, "abort"), (None, False, "abort"),
])
def test_acceptance_table_preserves_process_evidence(tmp_path, code, receipt, decision):
    process, paths = make_process(tmp_path, code, receipt=receipt)
    original_error = process.error
    result = evaluate_render(process, paths, metadata_required=True, canonical=True)
    assert result.decision == decision
    assert process.error is original_error
    assert result.as_record()["exit_code"] == code
    assert result.as_record()["process_status"] == process.process_status
    if decision == "abort":
        assert result.error is original_error
    else:
        assert result.error is None
    if code not in (0, None):
        assert process.process_status == "failed"
    assert process.attempt.record["decision"] == "pending"  # Evaluation alone is not durable acceptance.


@pytest.mark.parametrize("code", [0, 1])
def test_contradictory_receipt_blocks_even_zero_exit(tmp_path, code):
    process, paths = make_process(tmp_path, code)
    receipt = json.loads(process.attempt.completion_path.read_text())
    receipt["attempt_id"] = "other-attempt"
    process.attempt.completion_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = evaluate_render(process, paths, metadata_required=True)
    assert result.decision == "abort"
    assert "completion_attempt_id_mismatch" in result.issues


@pytest.mark.parametrize("code", [0, 1])
@pytest.mark.parametrize("case", ["wav", "metadata", "sources"])
def test_invalid_render_cannot_be_recovered_or_accepted(tmp_path, code, case):
    process, paths = make_process(tmp_path, code)
    if case == "wav":
        path = Path(paths["rendered_wav_path"])
        path.write_bytes(path.read_bytes()[:-4])
    else:
        path = Path(paths["render_metadata_path"])
        payload = json.loads(path.read_text())
        if case == "metadata":
            payload = {}
        else:
            payload["summary"]["sources"][0]["gain_db"] = 8
        path.write_text(json.dumps(payload), encoding="utf-8")
    write_completion_for_attempt(process.attempt)  # Even a matching receipt is insufficient.
    result = evaluate_render(process, paths, metadata_required=True)
    assert result.decision == "abort"
    assert not result.validation.artifacts_ok
    if code:
        assert result.error is process.error
    else:
        assert isinstance(result.error, RenderValidationError)


@pytest.mark.parametrize("code", [0, 1])
def test_acoustic_rejection_is_not_success_or_artifact_corruption(tmp_path, code):
    process, paths = make_process(tmp_path, code, t30=[0.5] * 9 + [0])
    result = evaluate_render(process, paths, metadata_required=True, canonical=True)
    assert result.validation.artifacts_ok
    assert result.validation.acoustic_rejection is not None
    assert result.decision == "retry_acoustic"
    assert not result.recovered
    assert result.error is None
    assert result.process.exit_code == code


def test_t30_retry_requires_completion_for_nonzero_exit(tmp_path):
    process, paths = make_process(tmp_path, 1, receipt=False, t30=[0.5] * 9)
    result = evaluate_render(process, paths, metadata_required=True, canonical=True)
    assert result.decision == "abort"
    assert result.error is process.error


def test_unknown_functional_error_is_not_hidden_by_valid_artifacts(tmp_path):
    process, paths = make_process(tmp_path, 1)
    process.error = RuntimeError("functional failure")
    result = evaluate_render(process, paths, metadata_required=True)
    assert result.decision == "abort"
    assert result.error is process.error


def test_recovery_requires_metadata_even_if_yaml_makes_it_optional(tmp_path):
    process, paths = make_process(tmp_path, 1)
    Path(paths["render_metadata_path"]).unlink()
    result = evaluate_render(process, paths, metadata_required=False)
    assert result.decision == "abort"
    assert result.validation.validity == "incomplete"


def publish_process_receipt(command):
    # Functional evidence is located from the invoked snapshot, not diagnostics.
    match = re.search(r"run_raven_static_render\('((?:[^']|'')*)'", command[2])
    functional = Path(match[1].replace("''", "'")).parent
    record = read_json_object(functional / "outcome.json")
    write_completion_for_attempt(SimpleNamespace(
        record=record, expected_artifacts=record["expected_artifacts"], completion_path=functional / "completion.json",
    ))


@pytest.mark.parametrize("invalid_t30", [False, True])
def test_canonical_recovery_keeps_t30_gate_and_retry_history(tmp_path, monkeypatch, invalid_t30):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "canonical_recovery", num_simulations=1, num_workers=1)
    calls = []
    original_sample = render_pipeline.sample_static_scene

    def sample(config, rng, scene_index, **kwargs):
        result = original_sample(config, rng, scene_index, **kwargs)
        if not kwargs:
            result["reverberation_sampling"]["attempts"] = 1
        return result

    monkeypatch.setattr(render_pipeline, "sample_static_scene", sample)

    def run(command, **kwargs):
        manifest = process_manifest(command)
        calls.append(manifest["render"]["active_output_type"])
        successful_process(command, **kwargs)
        if len(calls) == 1:
            if invalid_t30:
                _write_valid_raven_metadata(manifest, [0.5] * 9)
            publish_process_receipt(command)
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    manifests, summary = render_static_scenes(config_path)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    records = load_variant_index(layout["render_index_path"])
    assert len(manifests) == 1
    assert summary["completed_variants"] == 3
    assert summary["failed_matlab_processes"] == 1
    assert summary["recovered_matlab_processes"] == (0 if invalid_t30 else 1)
    assert calls[:2] == (["binaural_hrtf", "binaural_hrtf"] if invalid_t30 else ["binaural_hrtf", "bte_front_hartf"])
    outcomes = [read_json_object(path) for path in layout["render_attempts_dir"].glob("*/outcome.json")]
    failed = next(record for record in outcomes if record["exit_code"] == 1)
    assert failed["process_status"] == "failed"
    assert failed["decision"] == ("retry_acoustic" if invalid_t30 else "continue_with_warning")
    assert failed["completion_validation"]["status"] == "valid"
    if not invalid_t30:
        canonical = records["scene_static_0001__binaural_hrtf"]
        assert canonical["exit_code"] == 1
        assert canonical["recovery_reason"] == "post_completion"
    else:
        assert read_json_object(manifests[0])["reverberation_sampling"]["raven_retry_count"] == 1


def test_canonical_nonzero_without_completion_does_not_retry_t30(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "no_recovery", num_simulations=1)
    calls = []
    original_error = subprocess.CalledProcessError(1, "matlab")

    def run(command, **kwargs):
        manifest = process_manifest(command)
        calls.append(command)
        successful_process(command, **kwargs)
        _write_valid_raven_metadata(manifest, [0.5] * 9)
        raise original_error

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.raises(RenderStaticRunError) as caught:
        render_static_scenes(config_path)
    assert caught.value.__cause__ is original_error
    assert len(calls) == 1


def test_canonical_post_completion_t30_rejection_respects_exhausted_budget(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "exhausted_recovery", num_simulations=1)
    original_sample = render_pipeline.sample_static_scene
    calls = []

    def sample(config, rng, scene_index, **kwargs):
        result = original_sample(config, rng, scene_index, **kwargs)
        result["reverberation_sampling"]["attempts"] = config.scene_validation.max_scene_attempts
        return result

    def run(command, **kwargs):
        calls.append(command)
        successful_process(command, **kwargs)
        _write_valid_raven_metadata(process_manifest(command), [0.5] * 9)
        publish_process_receipt(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(render_pipeline, "sample_static_scene", sample)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    manifests, summary = render_static_scenes(config_path)
    assert manifests == []
    assert summary["sampling"]["status"] == "failed"
    assert len(calls) == 1
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    outcome = read_json_object(next(layout["render_attempts_dir"].glob("*/outcome.json")))
    assert outcome["process_status"] == "failed"
    assert outcome["decision"] == "retry_acoustic"
    assert outcome["exit_code"] == 1


def test_canonical_corrupt_metadata_keeps_original_process_error(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "corrupt_recovery", num_simulations=1)
    original_error = subprocess.CalledProcessError(17, "matlab")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        successful_process(command, **kwargs)
        Path(process_manifest(command)["render"]["output_metadata_path"]).write_text("{}", encoding="utf-8")
        publish_process_receipt(command)
        raise original_error

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.raises(RenderStaticRunError) as caught:
        render_static_scenes(config_path)
    assert caught.value.__cause__ is original_error
    assert len(calls) == 1
    assert caught.value.summary["completed_variants"] == 0


@pytest.mark.parametrize("workers", [1, 3])
@pytest.mark.parametrize("failure", [None, "missing_metadata", "wrong_variant"])
def test_noncanonical_policy_is_identical_for_sequential_and_parallel(tmp_path, monkeypatch, workers, failure):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "mixed_recovery", num_simulations=1, num_workers=workers)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    original_error = subprocess.CalledProcessError(23, "matlab")

    def run(command, **kwargs):
        manifest = process_manifest(command)
        output_type = manifest["render"]["active_output_type"]
        successful_process(command, **kwargs)
        if output_type == "binaural_hrtf":
            return SimpleNamespace(returncode=0)
        # Canonical acceptance must precede fanout, in both execution modes.
        canonical = load_variant_index(layout["render_index_path"])["scene_static_0001__binaural_hrtf"]
        assert canonical["status"] == "completed"
        assert canonical["decision"] == "continue"
        if output_type == "bte_rear_hartf" and failure == "missing_metadata":
            Path(manifest["render"]["output_metadata_path"]).unlink()
            raise original_error
        publish_process_receipt(command)
        if output_type == "bte_rear_hartf" and failure == "wrong_variant":
            directory = Path(kwargs["stdout"].name).parent
            functional = layout["render_attempts_dir"] / directory.name
            receipt = read_json_object(functional / "completion.json")
            receipt["variant_id"] = "scene_static_0001__bte_front_hartf"
            (functional / "completion.json").write_text(json.dumps(receipt), encoding="utf-8")
            raise original_error
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    if failure is None:
        manifests, summary = render_static_scenes(config_path)
        assert len(manifests) == 1
        assert summary["completed_variants"] == 3
    else:
        with pytest.raises(RenderStaticRunError) as caught:
            render_static_scenes(config_path)
        assert caught.value.__cause__ is original_error
    outcomes = [read_json_object(path) for path in layout["render_attempts_dir"].glob("*/outcome.json")]
    assert len(outcomes) == 3
    assert len({record["attempt_id"] for record in outcomes}) == 3
    front = next(record for record in outcomes if record["output_type"] == "bte_front_hartf")
    rear = next(record for record in outcomes if record["output_type"] == "bte_rear_hartf")
    assert front["decision"] == "continue_with_warning"
    assert front["exit_code"] == 1
    assert rear["decision"] == ("continue_with_warning" if failure is None else "abort")
    assert all(record["process_status"] == "failed" for record in (front, rear))


@pytest.mark.parametrize("failure", ["outcome", "index", "final_outcome", "all_outcome"])
def test_recovery_stops_on_functional_persistence_failure(tmp_path, monkeypatch, failure):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "persistence_failure", num_simulations=1)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    original_error = subprocess.CalledProcessError(1, "matlab")
    storage_error = PermissionError("functional storage denied")
    save = RenderAttempt.save
    upsert = render_pipeline.upsert_variant
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        successful_process(command, **kwargs)
        publish_process_receipt(command)
        raise original_error

    def denied_save(attempt):
        decision = attempt.record["decision"]
        if (failure == "outcome" and decision == "continue_with_warning"
                or failure == "final_outcome" and attempt.record.get("commit_status") == "committed"
                or failure == "all_outcome" and decision != "pending"):
            raise storage_error
        return save(attempt)

    def denied_index(index_path, records, record):
        if failure == "index" and record.get("decision") == "continue_with_warning":
            raise storage_error
        return upsert(index_path, records, record)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    monkeypatch.setattr(RenderAttempt, "save", denied_save)
    monkeypatch.setattr(render_pipeline, "upsert_variant", denied_index)
    with pytest.raises(RenderStaticRunError) as caught:
        render_static_scenes(config_path)
    assert caught.value.__cause__ is original_error
    assert original_error.__cause__ is storage_error
    assert len(calls) == 1
    outcome = read_json_object(next(layout["render_attempts_dir"].glob("*/outcome.json")))
    assert outcome["commit_status"] != "committed"
    assert outcome["process_status"] == "failed"
    assert outcome["exit_code"] == 1
    if failure != "all_outcome":
        assert outcome["decision"] == "abort"
        assert outcome["persistence_error"]["message"] == str(storage_error)


def test_recovery_persists_warning_and_original_error_before_next_variant(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "durable_recovery", num_simulations=1)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) > 1:
            canonical = load_variant_index(layout["render_index_path"])["scene_static_0001__binaural_hrtf"]
            outcome = read_json_object(Path(canonical["attempt_outcome_path"]))
            assert outcome["commit_status"] == "committed"
            assert outcome["warning"]["code"] == "matlab_process_failed_after_completion"
            assert outcome["error"]["type"] == "CalledProcessError"
            assert outcome["exit_code"] == 1
        successful_process(command, **kwargs)
        if len(calls) == 1:
            publish_process_receipt(command)
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    _, summary = render_static_scenes(config_path)
    assert summary["completed_variants"] == 3


def test_cli_shows_recovery_without_turning_it_into_clean_process_success(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "cli_recovery", num_simulations=1)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        successful_process(command, **kwargs)
        if len(calls) == 1:
            publish_process_receipt(command)
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    result = CliRunner().invoke(app, ["render-static", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "procesos_fallidos=1" in result.output
    assert "recuperados=1" in result.output
    assert "recuperaciones_reanudadas=0" in result.output
    assert "ADVERTENCIA:" in result.output


@pytest.mark.parametrize("failure", ["directory", "events", "summary"])
def test_recovery_is_independent_of_diagnostic_storage(tmp_path, monkeypatch, failure):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "diagnostic_independence", num_simulations=1)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    calls = []

    def unavailable(*args, **kwargs):
        raise PermissionError("diagnostic-only failure")

    if failure == "directory":
        mkdir = Path.mkdir

        def denied(path, *args, **kwargs):
            if path.is_relative_to(layout["matlab_diagnostics_dir"]):
                unavailable()
            return mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", denied)
    elif failure == "events":
        monkeypatch.setattr(MatlabDiagnostics, "read_events", unavailable)
    else:
        monkeypatch.setattr(MatlabDiagnostics, "save", unavailable)

    def run(command, **kwargs):
        calls.append(command)
        successful_process(command, **kwargs)
        if len(calls) == 1:
            publish_process_receipt(command)
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.warns(RuntimeWarning, match="MATLAB diagnostics"):
        _, summary = render_static_scenes(config_path)
    assert summary["completed_variants"] == 3
    assert summary["recovered_matlab_processes"] == 1
    record = load_variant_index(layout["render_index_path"])["scene_static_0001__binaural_hrtf"]
    outcome = read_json_object(Path(record["attempt_outcome_path"]))
    assert outcome["completion_validation"]["status"] == "valid"
    assert outcome["process_status"] == "failed"
    assert outcome["commit_status"] == "committed"
    diagnostic = layout["matlab_diagnostics_dir"] / record["attempt_id"] / "invocation.json"
    if diagnostic.is_file():
        assert read_json_object(diagnostic)["entrypoint_completed"] is False


def test_readme_counters_match_render_summary_contract():
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Aceptación funcional y fallos del proceso MATLAB", 1)[1].split("## Circular Array", 1)[0]
    assert "MATLAB procesos_fallidos=1 recuperados=1 recuperaciones_reanudadas=0" in section
    assert "post_completion" in section and "post_entrypoint" in section
    assert "commit_status=committed" in section
    assert "ya no propaga automáticamente `CalledProcessError`" in section


@pytest.mark.parametrize("remove_index", [True, False])
def test_failed_attempt_with_complete_files_cannot_resume_after_restart(tmp_path, monkeypatch, remove_index):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "restart_failed", num_simulations=1, resume_if_possible=True)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    original_error = subprocess.CalledProcessError(1, "matlab")

    def fail(command, **kwargs):
        successful_process(command, **kwargs)
        raise original_error  # Complete artifacts, but no functional completion.

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", fail)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(config_path)
    prior = load_variant_index(layout["render_index_path"])["scene_static_0001__binaural_hrtf"]
    assert prior["decision"] == "abort"
    evidence = {path: path.read_bytes() for path in layout["render_attempts_dir"].glob("*/*") if path.is_file()}
    if remove_index:
        layout["render_index_path"].unlink()
    calls = []

    def succeed(command, **kwargs):
        calls.append(process_manifest(command)["render"]["active_output_type"])
        if len(calls) == 1 and not remove_index:
            # Publishing a plan did not discard the previous veto.
            assert load_variant_index(layout["render_index_path"])[prior["variant_id"]]["decision"] == "abort"
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", succeed)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["execution"].update(resume_if_possible=True, overwrite_existing=False)
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError, FileExistsError)):
        render_static_scenes(config_path)
    assert calls == []
    assert {path: path.read_bytes() for path in layout["render_attempts_dir"].glob("*/*") if path.is_file()} == evidence
    if not remove_index:
        assert load_variant_index(layout["render_index_path"])[prior["variant_id"]]["decision"] == "abort"

    config["execution"].update(resume_if_possible=False, overwrite_existing=True)
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    _, summary = render_static_scenes(config_path)
    assert calls == ["binaural_hrtf", "bte_front_hartf", "bte_rear_hartf"]
    assert summary["render_jobs"] == summary["matlab_invocations"] == 3
    assert summary["resumed_variants"] == 0
    assert all(path.read_bytes() == content for path, content in evidence.items())
    assert read_json_object(Path(prior["attempt_outcome_path"]))["decision"] == "abort"


def test_new_pending_attempt_vetoes_an_older_completed_index(tmp_path):
    process, paths = make_process(tmp_path, 0)
    process.attempt.record.update(process_status="succeeded", exit_code=0, termination_observed=True)
    acceptance = evaluate_render(process, paths, metadata_required=True)
    persist_acceptance(acceptance, lambda: None)
    existing = {"status": "completed", "validation": {"ok": True}, "attempt_id": process.attempt.attempt_id}
    assert should_resume_variant({paths["variant_id"]: existing}, paths, metadata_required=True)
    pending = RenderAttempt(Path(paths["runtime_manifest_path"]))
    assert not should_resume_variant({paths["variant_id"]: existing}, paths, metadata_required=True)
    assert not should_resume_variant({}, paths, metadata_required=True)
    assert pending.record["process_status"] == "running"


def test_snapshot_without_outcome_is_not_silently_treated_as_legacy(tmp_path):
    process, paths = make_process(tmp_path, 0, receipt=False)
    process.attempt.outcome_path.unlink()
    history = load_render_history(process.attempt.directory.parent)
    assert not history.issues  # Identified interrupted preparation, not an unrelated global veto.
    assert not should_resume_variant({}, paths, metadata_required=True, history=history)


def test_failed_index_commit_cannot_become_legacy_after_restart(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "restart_commit", num_simulations=1, resume_if_possible=True)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    upsert = render_pipeline.upsert_variant

    def deny(index, records, record):
        if record.get("decision") == "continue_with_warning":
            raise PermissionError("index denied")
        return upsert(index, records, record)

    def fail(command, **kwargs):
        successful_process(command, **kwargs)
        publish_process_receipt(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(render_pipeline, "upsert_variant", deny)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", fail)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(config_path)
    evidence = {path: path.read_bytes() for path in layout["render_attempts_dir"].glob("*/*") if path.is_file()}
    outcomes = [read_json_object(path) for path in evidence if path.name == "outcome.json"]
    assert any(outcome["commit_status"] == "failed" for outcome in outcomes)
    layout["render_index_path"].unlink()
    monkeypatch.setattr(render_pipeline, "upsert_variant", upsert)
    calls = []

    def succeed(command, **kwargs):
        calls.append(command)
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", succeed)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["execution"].update(resume_if_possible=True, overwrite_existing=False)
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError, FileExistsError)):
        render_static_scenes(config_path)
    assert calls == []
    assert {path: path.read_bytes() for path in layout["render_attempts_dir"].glob("*/*") if path.is_file()} == evidence

    config["execution"].update(resume_if_possible=False, overwrite_existing=True)
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    _, summary = render_static_scenes(config_path)
    assert len(calls) == summary["render_jobs"] == summary["matlab_invocations"] == 3
    assert summary["resumed_variants"] == 0
    assert all(path.read_bytes() == content for path, content in evidence.items())


def recover_one_scene(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "resume_recovered", num_simulations=1, resume_if_possible=True)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        successful_process(command, **kwargs)
        if len(calls) == 1:
            publish_process_receipt(command)
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    render_static_scenes(config_path)
    record = load_variant_index(layout["render_index_path"])["scene_static_0001__binaural_hrtf"]
    return config_path, layout, record


def test_recovered_variant_resumes_without_new_invocation_or_fictitious_success(tmp_path, monkeypatch):
    config_path, layout, prior = recover_one_scene(tmp_path, monkeypatch)
    evidence = {path: path.read_bytes() for path in layout["render_attempts_dir"].glob("*/*")}
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", lambda *a, **k: pytest.fail("must resume"))
    _, summary = render_static_scenes(config_path)
    assert summary["render_jobs"] == 0
    assert summary["resumed_variants"] == 3
    assert summary["failed_matlab_processes"] == summary["recovered_matlab_processes"] == 0
    assert summary["resumed_recovered_variants"] == 1
    resumed = load_variant_index(layout["render_index_path"])[prior["variant_id"]]
    assert resumed["attempt_id"] == prior["attempt_id"]
    assert resumed["process_status"] == "failed"
    assert resumed["exit_code"] == 1
    assert resumed["decision"] == "continue_with_warning"
    assert resumed["recovery_reason"] == "post_completion"
    assert resumed["resumed"] and not resumed["attempted"]
    assert {path: path.read_bytes() for path in layout["render_attempts_dir"].glob("*/*")} == evidence
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    assert "MATLAB procesos_fallidos=0 recuperados=0 recuperaciones_reanudadas=1" in readme


@pytest.mark.parametrize("target", ["wav", "metadata", "snapshot"])
def test_recovered_artifact_hashes_are_rechecked_on_resume(tmp_path, monkeypatch, target):
    config_path, layout, prior = recover_one_scene(tmp_path, monkeypatch)
    if target == "wav":
        path = Path(prior["rendered_wav_path"])
        content = bytearray(path.read_bytes())
        content[-1] ^= 1  # Still a decodable PCM WAV with the same dimensions.
        path.write_bytes(content)
    elif target == "metadata":
        path = Path(prior["render_metadata_path"])
        payload = read_json_object(path)
        payload["note"] = "extra metadata, same acoustic fields"
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        outcome = read_json_object(Path(prior["attempt_outcome_path"]))
        path = Path(outcome["manifest_snapshot_path"])
        path.write_bytes(path.read_bytes() + b"\n")  # Same JSON semantics, different exact input evidence.
    calls = []

    def succeed(command, **kwargs):
        calls.append(process_manifest(command)["render"]["active_output_type"])
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", succeed)
    if target == "snapshot":
        outputs = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                   for path in layout["render_outputs_root"].rglob("*") if path.is_file()}
        evidence = {path: path.read_bytes() for path in layout["render_attempts_dir"].glob("*/*") if path.is_file()}
        with pytest.raises((ValueError, RuntimeError)):
            render_static_scenes(config_path)
        assert calls == []
        assert {path: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in layout["render_outputs_root"].rglob("*") if path.is_file()} == outputs
        assert {path: path.read_bytes() for path in layout["render_attempts_dir"].glob("*/*") if path.is_file()} == evidence
        return
    _, summary = render_static_scenes(config_path)
    assert calls == ["binaural_hrtf"]
    assert summary["render_jobs"] == 1
    assert summary["resumed_variants"] == 2
    assert load_variant_index(layout["render_index_path"])[prior["variant_id"]]["attempt_id"] != prior["attempt_id"]


def test_acoustic_input_change_invalidates_recovered_resume_before_runtime_overwrite(tmp_path, monkeypatch):
    config_path, _, _ = recover_one_scene(tmp_path, monkeypatch)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["source_sampling"]["gain_db"] = {"min": -10, "max": -10}
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    calls = []

    def succeed(command, **kwargs):
        calls.append(command)
        assert all(source["gain_db"] == -10 for source in process_manifest(command)["sources"])
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", succeed)
    _, summary = render_static_scenes(config_path)
    assert len(calls) == summary["render_jobs"] == 3
    assert summary["resumed_variants"] == 0


def test_batch_bookkeeping_changes_do_not_invalidate_valid_recovered_audio(tmp_path):
    process, paths = make_process(tmp_path, 1)
    acceptance = evaluate_render(process, paths, metadata_required=True)
    persist_acceptance(acceptance, lambda: None)
    expected = read_json_object(process.attempt.snapshot_path)
    expected["reverberation_batch"] = {"accepted_realizations": 42}
    expected["reverberation_sampling"] = {"raven_retry_count": 2}
    expected["sampling"] = {"scene_attempt": 99}
    assert equivalent_render_inputs(expected, read_json_object(process.attempt.snapshot_path))
    assert should_resume_variant({}, paths, metadata_required=True, expected_manifest=expected)
    expected["sources"][0]["gain_db"] = 5
    assert not should_resume_variant({}, paths, metadata_required=True, expected_manifest=expected)


def test_disabling_resume_rerenders_even_strictly_valid_legacy_files(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "legacy_no_resume", num_simulations=1, resume_if_possible=False)
    config = load_config(config_path)
    manifests = render_pipeline.generate_static_manifests(config_path)
    _prepare_legacy_render_fixture(config, read_json_object(manifests[0]), "binaural_hrtf")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    _, summary = render_static_scenes(config_path)
    assert len(calls) == summary["render_jobs"] == 3
    assert summary["resumed_variants"] == 0


@pytest.mark.parametrize("change", [None, "wav", "metadata", "runtime", "scene", "receipt", "pending", "index_abort"])
def test_clarity_revalidates_recovered_hrtf_and_blocks_stale_inputs(tmp_path, monkeypatch, change):
    config_path, layout, prior = recover_one_scene(tmp_path, monkeypatch)
    config = load_config(config_path)
    config.hearing_degradation.enabled = True
    config.hearing_degradation.input_targets = ["binaural_hrtf"]
    config.hearing_degradation.hearing_profiles_path.write_text(_hearing_profiles_yaml(["mild_loss"]), encoding="utf-8")
    if change == "wav":
        path = Path(prior["rendered_wav_path"])
        content = bytearray(path.read_bytes())
        content[-1] ^= 1
        path.write_bytes(content)
    elif change in ("metadata", "runtime", "scene", "receipt"):
        path = {
            "metadata": Path(prior["render_metadata_path"]), "runtime": Path(prior["runtime_manifest_path"]),
            "scene": layout["scene_manifest_dir"] / f"{prior['scene_id']}.json", "receipt": Path(prior["completion_path"]),
        }[change]
        payload = read_json_object(path)
        if change in ("scene", "runtime"):
            payload["sources"][0]["gain_db"] += 1
        elif change == "receipt":
            payload["attempt_id"] = "wrong"
        else:
            payload["note"] = "changed after indexing"
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif change == "pending":
        RenderAttempt(Path(prior["runtime_manifest_path"]), layout["render_attempts_dir"])
    elif change == "index_abort":
        records = load_variant_index(layout["render_index_path"])
        records[prior["variant_id"]]["decision"] = "abort"
        layout["render_index_path"].write_text("".join(json.dumps(record) + "\n" for record in records.values()), encoding="utf-8")
    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_handoff.submit_clarity_manifest",
                        lambda *a, **k: pytest.fail("invalid render must not be submitted"))
    summary = prepare_clarity_handoff(config, submit=change is not None)
    jobs = [json.loads(line) for line in layout["clarity_jobs_path"].read_text(encoding="utf-8").splitlines()]
    assert not summary["submitted"]
    if change is None:
        assert summary["planned_jobs"] == 1
        assert len(jobs) == 1
        assert jobs[0]["output_type"] == "binaural_hrtf"
        assert jobs[0]["input_wav_path"] == prior["rendered_wav_path"]
        assert Path(jobs[0]["expected_output_wav_path"]).name == Path(prior["rendered_wav_path"]).name
    else:
        assert summary["blocked_jobs"] == 1
        assert jobs == []


def test_clarity_rechecks_input_after_planning_before_submission(tmp_path, monkeypatch):
    config_path, layout, prior = recover_one_scene(tmp_path, monkeypatch)
    config = load_config(config_path)
    config.hearing_degradation.enabled = True
    config.hearing_degradation.input_targets = ["binaural_hrtf"]
    config.hearing_degradation.hearing_profiles_path.write_text(_hearing_profiles_yaml(["mild_loss"]), encoding="utf-8")
    publish = clarity_handoff.write_clarity_job_manifest

    def change_after_planning(path, jobs):
        publish(path, jobs)
        if jobs:
            wav = Path(prior["rendered_wav_path"])
            content = bytearray(wav.read_bytes())
            content[-1] ^= 1
            wav.write_bytes(content)

    monkeypatch.setattr(clarity_handoff, "write_clarity_job_manifest", change_after_planning)
    monkeypatch.setattr(clarity_handoff, "submit_clarity_manifest", lambda *a, **k: pytest.fail("must not submit changed audio"))
    summary = prepare_clarity_handoff(config, submit=True)
    assert not summary["submitted"]
    assert summary["blocked_jobs"] == 1
    assert layout["clarity_jobs_path"].read_text(encoding="utf-8") == ""


def test_end_to_end_recovery_restart_handoff_and_tampered_input_rejection(tmp_path, monkeypatch):
    config_path, layout, prior = recover_one_scene(tmp_path, monkeypatch)
    original_outcome = Path(prior["attempt_outcome_path"]).read_bytes()
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("restart must not invoke MATLAB"))
    _, summary = render_static_scenes(config_path)
    assert summary["render_jobs"] == 0
    assert summary["resumed_recovered_variants"] == 1
    config = load_config(config_path)
    config.hearing_degradation.enabled = True
    config.hearing_degradation.input_targets = ["binaural_hrtf"]
    config.hearing_degradation.hearing_profiles_path.write_text(_hearing_profiles_yaml(["mild_loss"]), encoding="utf-8")
    handoff = prepare_clarity_handoff(config, submit=False)
    assert handoff["planned_jobs"] == 1
    assert Path(prior["attempt_outcome_path"]).read_bytes() == original_outcome
    wav = Path(prior["rendered_wav_path"])
    content = bytearray(wav.read_bytes())
    content[-1] ^= 1
    wav.write_bytes(content)
    monkeypatch.setattr(clarity_handoff, "submit_clarity_manifest", lambda *a, **k: pytest.fail("tampered input must not be submitted"))
    blocked = prepare_clarity_handoff(config, submit=True)
    assert blocked["blocked_jobs"] == 1
    assert not blocked["submitted"]
    assert layout["clarity_jobs_path"].read_text(encoding="utf-8") == ""
