"""Operator reconciliation of a pool attempt whose published result could not be read."""
import hashlib
import json
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from acoustic_orchestrator.cli import app
from acoustic_orchestrator.pipeline import matlab_pool
from acoustic_orchestrator.pipeline import pool_result_recovery as recovery
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.process_retries import RetryBlockedError
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes
from test_matlab_pool_pipeline import Recorder, _use_pool, render_snapshot
from test_render_retry_dispatch import chains, configure, outcomes


def render_with_receipt(request: dict) -> int:
    """Fake pool job that, like MATLAB, also publishes the completion receipt."""
    render_snapshot(request)
    context = request["completion_context"]
    snapshot = json.loads(Path(request["snapshot_path"]).read_text(encoding="utf-8"))
    artifacts = [{"kind": kind, "path": Path(snapshot["render"][field]).resolve().as_posix()}
                 for kind, field in (("wav", "output_wav_path"), ("metadata", "output_metadata_path"))]
    Path(context["completion_path"]).write_text(json.dumps({
        "schema_version": 1, "attempt_id": context["attempt_id"], "scene_id": snapshot["scene_id"],
        "variant_id": context["variant_id"], "output_type": snapshot["render"]["active_output_type"],
        "manifest_sha256": context["manifest_sha256"],
        "artifacts": [{**item, "size_bytes": Path(item["path"]).stat().st_size,
                       "sha256": hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()}
                      for item in artifacts],
    }), encoding="utf-8")
    return 0


def result_path(layout, outcome) -> Path:
    found = list((layout["run_root"] / "metadata/matlab_pool").glob(f"*/results/{outcome['attempt_id']}.json"))
    assert len(found) == 1
    return found[0]


def files(root: Path) -> dict:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def blocked_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Reproduce experiment 111: the first result stays locked past the read retry, the run aborts and resume is blocked."""
    recorder = Recorder(render_with_receipt)
    monkeypatch.setattr(matlab_pool, "_launch_subprocess", recorder.launch)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("pool mode launched a per-attempt MATLAB process"))
    original = matlab_pool._read_result
    locked: list[str] = []
    lock = threading.Lock()

    def read(path: Path, attempt_id: str) -> dict:
        with lock:
            if not locked:
                locked.append(attempt_id)
        if attempt_id == locked[0]:
            raise PermissionError(13, "Permission denied", str(path))
        return original(path, attempt_id)

    monkeypatch.setattr(matlab_pool, "_read_result", read)
    path, layout = configure(tmp_path, scenes=2, workers=1)
    _use_pool(path)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)
    monkeypatch.setattr(matlab_pool, "_read_result", original)
    attempt_id = locked[0]
    outcome = read_json_object(layout["render_attempts_dir"] / attempt_id / "outcome.json")
    return path, layout, recorder, outcome


def apply(layout, outcome, **kwargs):
    options = {"apply": True, "confirm_stopped": True, "expected_attempt": outcome["attempt_id"],
               "reason": "Pool result was locked by antivirus", "process_probe": lambda: []}
    options.update(kwargs)
    return recovery.reconcile_pool_result(layout["run_root"], outcome["chain_id"], **options)


def test_locked_result_blocks_resume_until_reconciled(blocked_run) -> None:
    path, layout, recorder, outcome = blocked_run
    assert outcome["process_status"] == "launch_failed" and outcome["termination_observed"] is False
    assert outcome["decision"] == "abort" and outcome["retry_classification"] == "launch_failed"
    with pytest.raises((RetryBlockedError, RenderStaticRunError), match="termination was not observed"):
        render_static_scenes(path)

    report = apply(layout, outcome)

    assert report["applied"] and report["pool_exit_code"] == 0
    reconciled = read_json_object(layout["render_attempts_dir"] / outcome["attempt_id"] / "outcome.json")
    assert reconciled["process_status"] == "succeeded" and reconciled["exit_code"] == 0
    assert reconciled["termination_observed"] is True and reconciled["error"] is None
    assert reconciled["decision"] == "continue" and reconciled["retry_classification"] == "accepted"
    assert reconciled["retry_decision"] == "not_retryable" and reconciled["pool_worker_id"] == "worker-7"
    row = load_variant_index(layout["render_index_path"])[reconciled["variant_id"]]
    assert row["decision"] == "continue" and row["process_status"] == "succeeded" and row["exit_code"] == 0

    _, summary = render_static_scenes(path)
    records = outcomes(layout)
    variant_attempts = [r for r in records if r["variant_id"] == reconciled["variant_id"]]
    assert [r["attempt_id"] for r in variant_attempts] == [outcome["attempt_id"]]  # Not rendered again.
    assert all(r["decision"] == "continue" for r in records)
    assert summary["completed_variants"] == summary["total_variants"]


def test_inspection_reports_without_writing(blocked_run) -> None:
    _, layout, _, outcome = blocked_run
    before = files(layout["run_root"])
    report = recovery.reconcile_pool_result(layout["run_root"], outcome["chain_id"], process_probe=lambda: [])
    assert report["applied"] is False and report["attempt_id"] == outcome["attempt_id"]
    assert report["pool_exit_code"] == 0
    assert files(layout["run_root"]) == before


def test_reconciliation_keeps_preimages_and_is_idempotent(blocked_run) -> None:
    _, layout, _, outcome = blocked_run
    outcome_path = layout["render_attempts_dir"] / outcome["attempt_id"] / "outcome.json"
    original = outcome_path.read_bytes()
    report = apply(layout, outcome)
    journal = Path(report["reconciliation_path"])
    assert (journal.parent / "outcome.before.json").read_bytes() == original
    audit = read_json_object(journal)
    assert audit["reason"] == "Pool result was locked by antivirus" and audit["pool_result"]["exit_code"] == 0
    after = files(layout["run_root"])
    apply(layout, outcome)
    assert files(layout["run_root"]) == after


@pytest.mark.parametrize("missing", ["confirm_stopped", "expected_attempt", "reason"])
def test_apply_requires_explicit_confirmations(blocked_run, missing) -> None:
    _, layout, _, outcome = blocked_run
    value = {"confirm_stopped": False, "expected_attempt": None, "reason": ""}[missing]
    before = files(layout["run_root"])
    with pytest.raises(RetryBlockedError, match="requiere"):
        apply(layout, outcome, **{missing: value})
    assert files(layout["run_root"]) == before


def _corrupt_result(layout, outcome):
    path = result_path(layout, outcome)
    result = json.loads(path.read_text(encoding="utf-8"))
    result["exit_code"] = 1
    path.write_text(json.dumps(result), encoding="utf-8")


def _remove_result(layout, outcome):
    result_path(layout, outcome).unlink()


def _remove_completion(layout, outcome):
    Path(outcome["completion_path"]).unlink()


def _truncate_wav(layout, outcome):
    Path(outcome["expected_artifacts"][0]["path"]).write_bytes(b"")


@pytest.mark.parametrize("tamper", [_corrupt_result, _remove_result, _remove_completion, _truncate_wav])
def test_missing_or_contradictory_evidence_blocks_without_mutation(blocked_run, tamper) -> None:
    _, layout, _, outcome = blocked_run
    tamper(layout, outcome)
    before = files(layout["run_root"])
    with pytest.raises(RetryBlockedError):
        apply(layout, outcome)
    assert files(layout["run_root"]) == before


def test_active_writers_block_reconciliation(blocked_run) -> None:
    _, layout, _, outcome = blocked_run
    before = files(layout["run_root"])
    with pytest.raises(RetryBlockedError, match="escritores"):
        apply(layout, outcome, process_probe=lambda: [{"pid": 1, "name": "MATLAB.exe", "command": None}])
    assert files(layout["run_root"]) == before


def test_accepted_attempt_is_not_eligible(blocked_run) -> None:
    path, layout, _, outcome = blocked_run
    apply(layout, outcome)
    render_static_scenes(path)
    accepted = next(c for c in chains(layout) if c["attempts"][-1]["attempt_id"] != outcome["attempt_id"])
    with pytest.raises(RetryBlockedError, match="no es un resultado del pool"):
        recovery.reconcile_pool_result(layout["run_root"], accepted["chain_id"], process_probe=lambda: [])


def test_forged_reconciled_outcome_blocks_resume(blocked_run) -> None:
    path, layout, _, outcome = blocked_run
    apply(layout, outcome)
    outcome_path = layout["render_attempts_dir"] / outcome["attempt_id"] / "outcome.json"
    forged = read_json_object(outcome_path)
    forged["pool_worker_id"] = "worker-forged"
    outcome_path.write_text(json.dumps(forged, indent=2) + "\n", encoding="utf-8")
    with pytest.raises((RetryBlockedError, RenderStaticRunError)):
        render_static_scenes(path)


def test_cli_inspects_then_applies(blocked_run, monkeypatch: pytest.MonkeyPatch) -> None:
    _, layout, _, outcome = blocked_run
    monkeypatch.setattr(recovery, "active_render_writers", lambda: [])
    runner = CliRunner()
    root = str(layout["run_root"])
    inspected = runner.invoke(app, ["reconcile-pool-result", root, "--chain", outcome["chain_id"]])
    assert inspected.exit_code == 0, inspected.output
    assert "Solo lectura" in inspected.output and "exit_code=0" in inspected.output
    applied = runner.invoke(app, ["reconcile-pool-result", root, "--chain", outcome["chain_id"],
                                  "--attempt", outcome["attempt_id"], "--apply", "--confirm-stopped",
                                  "--reason", "locked result"])
    assert applied.exit_code == 0, applied.output
    assert "Reconciliado" in applied.output
