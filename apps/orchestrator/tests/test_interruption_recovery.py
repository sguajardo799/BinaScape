"""Controlled interruption recovery; all filesystem mutations use temp fixtures."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from acoustic_orchestrator.cli import app
from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline import interruption_recovery as recovery
from acoustic_orchestrator.pipeline import process_retries as retries
from acoustic_orchestrator.pipeline import render_pipeline
from acoustic_orchestrator.pipeline.output_index import should_resume_variant
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_history import load_render_history
from test_render_resilience import configure, evidence
from test_render_diagnostics_integration import successful_process
from render_fixtures import write_render_metadata, write_render_wav


def pending(tmp_path, *, attempts=3):
    config_path, layout = configure(tmp_path, scenes=1, attempts=attempts)
    config = load_config(config_path)
    manifest_path = render_pipeline.generate_static_manifests(config_path)[0]
    scene = json.loads(manifest_path.read_text())
    rewrites = render_pipeline.prepare_scene_source_assets(scene, layout["prepared_audio_dir"] / scene["scene_id"])
    variant = render_pipeline._build_planned_variants_from_manifest(config, scene, layout["runtime_manifest_dir"])[0]
    task = render_pipeline._prepare_render_task(config, variant, layout["runtime_manifest_dir"], rewrites)
    chain = retries.open_chain(task["runtime_manifest_path"], layout["run_root"] / "metadata/process_chains",
                               config.execution.matlab_process_retry)
    attempt = chain.reserve_attempt(task["runtime_manifest_path"], layout["render_attempts_dir"])
    attempt.record["command"] = ["matlab", "-batch", "interrupted command"]
    attempt.save()
    return config_path, layout, task, chain, attempt


def files(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def apply(layout, chain, attempt, **kwargs):
    return recovery.reconcile_interrupted_chain(
        layout["run_root"], chain.record["chain_id"], apply=True, confirm_stopped=True,
        expected_attempt=attempt.attempt_id, reason="Operator verified all writers stopped", process_probe=lambda: [],
        **kwargs,
    )


def test_inspection_does_not_write_or_unlock(tmp_path):
    _, layout, _, chain, attempt = pending(tmp_path)
    before = files(layout["run_root"])
    report = recovery.reconcile_interrupted_chain(layout["run_root"], chain.record["chain_id"], process_probe=lambda: [])
    assert report["applied"] is False and report["next_state"] == "scheduled"
    assert report["attempt_id"] == attempt.attempt_id
    assert report["consumed_attempts"] == 1 and report["remaining_attempts"] == 2
    assert files(layout["run_root"]) == before


def test_reconcile_retains_exact_preimages_unknown_exit_and_budget(tmp_path):
    config_path, layout, task, chain, attempt = pending(tmp_path)
    # Include Windows line endings in original evidence.
    original = attempt.outcome_path.read_bytes().replace(b"\n", b"\r\n")
    attempt.outcome_path.write_bytes(original)
    chain_bytes = chain.path.read_bytes()
    before = evidence(layout)
    report = apply(layout, chain, attempt)
    outcome = read_json_object(attempt.outcome_path)
    assert report["applied"] and report["remaining_attempts"] == 2
    assert outcome["exit_code"] is None and outcome["termination_observed"] is False
    assert outcome["finished_at"] is None and outcome["process_status"] == "interrupted"
    assert outcome["error"] is None
    assert outcome["decision"] == "abort" and outcome["commit_status"] == "committed"
    journal = Path(report["reconciliation_path"])
    assert (journal.parent / "outcome.before.json").read_bytes() == original
    assert (journal.parent / "chain.before.json").read_bytes() == chain_bytes
    assert read_json_object(journal)["reason"] == "Operator verified all writers stopped"
    assert evidence(layout) == before
    assert not should_resume_variant({}, task["variant_paths"], metadata_required=True,
                                     history=load_render_history(layout["render_attempts_dir"]))
    resumed = retries.open_chain(task["runtime_manifest_path"], layout["run_root"] / "metadata/process_chains",
                                 load_config(config_path).execution.matlab_process_retry)
    resumed.verify_resolved()
    resumed.archive_attempt()
    second = resumed.reserve_attempt(task["runtime_manifest_path"], layout["render_attempts_dir"])
    assert second.record["attempt_ordinal"] == 2 and second.record["previous_attempt_id"] == attempt.attempt_id
    assert second.attempt_id != attempt.attempt_id
    assert config_path.exists()


def test_reconciliation_is_idempotent_and_targets_only_the_pinned_attempt(tmp_path):
    _, layout, task, chain, attempt = pending(tmp_path)
    apply(layout, chain, attempt)
    before = files(layout["run_root"])
    apply(layout, chain, attempt)
    assert files(layout["run_root"]) == before
    resumed = retries.RetryChain(chain.path, read_json_object(chain.path))
    resumed.archive_attempt()
    resumed.reserve_attempt(task["runtime_manifest_path"], layout["render_attempts_dir"])
    newer = files(layout["run_root"])
    with pytest.raises(retries.RetryBlockedError, match="actual cambió"):
        apply(layout, chain, attempt)
    assert files(layout["run_root"]) == newer


@pytest.mark.parametrize("missing", ["confirm_stopped", "expected_attempt", "reason"])
def test_apply_requires_explicit_confirmations(tmp_path, missing):
    _, layout, _, chain, attempt = pending(tmp_path)
    options = {"apply": True, "confirm_stopped": True, "expected_attempt": attempt.attempt_id,
               "reason": "Confirmed stopped", "process_probe": lambda: []}
    options[missing] = False if missing == "confirm_stopped" else None if missing == "expected_attempt" else ""
    before = files(layout["run_root"])
    with pytest.raises(retries.RetryBlockedError, match="requiere"):
        recovery.reconcile_interrupted_chain(layout["run_root"], chain.record["chain_id"], **options)
    assert files(layout["run_root"]) == before


@pytest.mark.parametrize("check", ["active", "unavailable", "lock", "snapshot", "runtime", "completion", "failure", "circuit", "protected"])
def test_uncertain_or_contradictory_state_blocks_without_mutation(tmp_path, check):
    _, layout, task, chain, attempt = pending(tmp_path)
    def probe():
        if check == "unavailable":
            raise PermissionError("process table unavailable")
        return [{"pid": 123, "name": "MATLAB.exe"}] if check == "active" else []
    if check == "lock":
        (layout["render_index_path"].parent / "static_writer.lock").write_text("uncertain owner")
    elif check in {"snapshot", "runtime"}:
        path = attempt.snapshot_path if check == "snapshot" else task["runtime_manifest_path"]
        path.write_bytes(path.read_bytes() + b"\n")
    elif check in {"completion", "failure"}:
        (attempt.directory / f"{check}.json").write_text("{}")
    elif check == "circuit":
        (layout["render_index_path"].parent / "matlab_process_circuit.json").write_text('{"open":true}')
    else:
        record = read_json_object(chain.path)
        record["preexisting_artifacts"] = deepcopy(record["artifacts"])
        chain.path.write_text(json.dumps(record))
    before = files(layout["run_root"])
    with pytest.raises((RuntimeError, ValueError)):
        recovery.reconcile_interrupted_chain(layout["run_root"], chain.record["chain_id"], apply=True,
            confirm_stopped=True, expected_attempt=attempt.attempt_id, reason="Stopped", process_probe=probe)
    assert files(layout["run_root"]) == before


def test_exhaustion_does_not_replenish_attempts(tmp_path):
    _, layout, task, chain, attempt = pending(tmp_path, attempts=1)
    report = apply(layout, chain, attempt)
    assert report["state"] == "exhausted" and report["remaining_attempts"] == 0
    resumed = retries.RetryChain(chain.path, read_json_object(chain.path))
    resumed.verify_resolved()
    with pytest.raises(retries.RetryBlockedError):
        resumed.reserve_attempt(task["runtime_manifest_path"], layout["render_attempts_dir"])


@pytest.mark.parametrize("target", ["outcome", "chain"])
def test_partial_publication_can_only_resume_the_same_reconciliation(tmp_path, monkeypatch, target):
    _, layout, task, chain, attempt = pending(tmp_path)
    save = recovery._save
    interrupted = attempt.outcome_path if target == "outcome" else chain.path
    def fail(path, record):
        if path == interrupted:
            raise OSError("state publication interrupted")
        save(path, record)
    monkeypatch.setattr(recovery, "_save", fail)
    with pytest.raises(OSError):
        apply(layout, chain, attempt)
    with pytest.raises(retries.RetryBlockedError):
        retries.preflight_chain(task["runtime_manifest_path"], layout["run_root"] / "metadata/process_chains",
            input_sha256=chain.record["input_sha256"], reusable=False, overwrite_existing=False, force_reset=False)
    monkeypatch.setattr(recovery, "_save", save)
    apply(layout, chain, attempt)
    assert read_json_object(chain.path)["state"] == "scheduled"
    retries.RetryChain(chain.path, read_json_object(chain.path)).verify_resolved()


@pytest.mark.parametrize("tamper", ["marker", "preimage", "record"])
def test_reconciliation_cannot_be_forged_by_changing_running_to_resolved(tmp_path, tamper):
    _, layout, _, chain, attempt = pending(tmp_path)
    report = apply(layout, chain, attempt)
    journal = Path(report["reconciliation_path"])
    if tamper == "marker":
        record = read_json_object(attempt.outcome_path)
        record["interruption_reconciliation"]["sha256"] = "0" * 64
        attempt.outcome_path.write_text(json.dumps(record))
    elif tamper == "preimage":
        (journal.parent / "outcome.before.json").write_text("{}")
    else:
        journal.write_bytes(journal.read_bytes() + b"\n")
    with pytest.raises((retries.RetryBlockedError, ValueError)):
        retries.RetryChain(chain.path, read_json_object(chain.path)).verify_resolved()


def test_partial_outputs_are_archived_but_late_files_are_not_claimed(tmp_path):
    _, layout, task, chain, attempt = pending(tmp_path)
    wav = Path(task["variant_paths"]["rendered_wav_path"])
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(b"partial PCM from interrupted attempt")
    apply(layout, chain, attempt)
    resumed = retries.RetryChain(chain.path, read_json_object(chain.path))
    resumed.archive_attempt()
    assert not wav.exists()
    assert any(p.read_bytes() == b"partial PCM from interrupted attempt"
               for p in (attempt.directory / "archived_outputs").iterdir())

    _, layout2, task2, chain2, attempt2 = pending(tmp_path / "other")
    apply(layout2, chain2, attempt2)
    late = Path(task2["variant_paths"]["rendered_wav_path"])
    late.parent.mkdir(parents=True, exist_ok=True)
    late.write_bytes(b"unattributed later file")
    with pytest.raises(retries.RetryBlockedError, match="cambiaron"):
        retries.RetryChain(chain2.path, read_json_object(chain2.path)).archive_attempt()
    assert late.read_bytes() == b"unattributed later file"


def test_complete_outputs_require_separate_review_not_automatic_repeat(tmp_path):
    _, layout, task, chain, attempt = pending(tmp_path)
    manifest = read_json_object(task["runtime_manifest_path"])
    write_render_wav(manifest)
    write_render_metadata(manifest)
    before = files(layout["run_root"])
    with pytest.raises(retries.RetryBlockedError, match="completas"):
        apply(layout, chain, attempt)
    assert files(layout["run_root"]) == before


def test_cli_inspects_then_reconciles_without_rendering_and_resume_uses_new_uuid(tmp_path, monkeypatch):
    config_path, layout, _, chain, attempt = pending(tmp_path)
    monkeypatch.setattr(recovery, "active_render_writers", lambda: [])
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("reconciliation must never launch MATLAB"))
    runner = CliRunner()
    args = ["reconcile-static", str(layout["run_root"]), "--chain", chain.record["chain_id"]]
    before = files(layout["run_root"])
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "Solo lectura" in result.output and "restantes=2" in result.output
    assert files(layout["run_root"]) == before
    result = runner.invoke(app, args + ["--apply", "--attempt", attempt.attempt_id,
                                      "--confirm-stopped", "--reason", "Operator stopped all writers"])
    assert result.exit_code == 0, result.output
    assert "exit_code=null termination_observed=false" in result.output
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", successful_process)
    _, summary = render_pipeline.render_static_scenes(config_path)
    assert summary["completed_variants"] == summary["matlab_invocations"] == 1
    assert summary["process_retries"] == 1
    assert summary["failed_matlab_processes"] == summary["recovered_matlab_processes"] == 0
    records = [read_json_object(p) for p in layout["render_attempts_dir"].glob("*/outcome.json")]
    assert len(records) == 2
    assert len({o["chain_id"] for o in records}) == 1
    assert len({o["attempt_id"] for o in records}) == 2
    assert read_json_object(attempt.outcome_path)["process_status"] == "interrupted"


def test_process_probe_blocks_renderers_and_other_orchestrators_not_itself(monkeypatch):
    rows = [
        {"ProcessId": os.getpid(), "Name": "python.exe", "CommandLine": "render-static"},
        {"ProcessId": 100001, "Name": "MATLAB.exe", "CommandLine": "matlab -batch render"},
        {"ProcessId": 100002, "Name": "python.exe", "CommandLine": "acoustic-orchestrator render-static other.yml"},
        {"ProcessId": 100003, "Name": "python.exe", "CommandLine": "unrelated_server.py"},
    ]
    output = json.dumps(rows) if os.name == "nt" else "\n".join(
        f"{r['ProcessId']} {r['Name']} {r['CommandLine']}" for r in rows)
    monkeypatch.setattr(recovery.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=output))
    assert {row["pid"] for row in recovery.active_render_writers()} == {100001, 100002}


def test_failed_native_process_probe_never_means_no_active_processes(tmp_path, monkeypatch):
    _, layout, _, chain, attempt = pending(tmp_path)
    before = files(layout["run_root"])
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("process inspection", 30)
    monkeypatch.setattr(recovery.subprocess, "run", fail)
    with pytest.raises(retries.RetryBlockedError, match="ausencia"):
        recovery.reconcile_interrupted_chain(layout["run_root"], chain.record["chain_id"], apply=True,
            confirm_stopped=True, expected_attempt=attempt.attempt_id, reason="Stopped")
    assert files(layout["run_root"]) == before


@pytest.mark.parametrize("late", ["completion", "output"])
def test_repeated_reconciliation_does_not_ignore_new_contradictions(tmp_path, late):
    _, layout, task, chain, attempt = pending(tmp_path)
    apply(layout, chain, attempt)
    if late == "completion":
        attempt.completion_path.write_text("{}")
    else:
        path = Path(task["variant_paths"]["rendered_wav_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"unknown writer after confirmation")
    before = files(layout["run_root"])
    with pytest.raises(retries.RetryBlockedError):
        apply(layout, chain, attempt)
    assert files(layout["run_root"]) == before


def test_empty_native_process_inventory_is_not_evidence_of_no_writers(monkeypatch):
    output = "[]" if os.name == "nt" else ""
    monkeypatch.setattr(recovery.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=output))
    with pytest.raises(retries.RetryBlockedError, match="vacío"):
        recovery.active_render_writers()
