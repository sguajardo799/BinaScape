"""Explicit reset of a latched batch circuit; all mutations use temp fixtures."""

import json
import subprocess
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest
from typer.testing import CliRunner

from acoustic_orchestrator.cli import app
from acoustic_orchestrator.pipeline import circuit_reset
from acoustic_orchestrator.pipeline import process_retries as retries
from acoustic_orchestrator.pipeline.process_retries import BatchCircuit, RetryBlockedError
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes
from test_render_diagnostics_integration import process_manifest, successful_process
from test_render_retry_dispatch import chains, circuit_path, configure, outcomes

REASON = "MathWorks license restored; no MATLAB running"


def latched_batch(tmp_path, monkeypatch, *, attempts=3):
    """Reproduce the observed cutoff: one failed and one late-accepted in-flight chain.

    Two canonical renders are accepted, two optional workers start, the first
    fails (limit 1 opens the circuit) and the in-flight second is accepted after
    the cutoff, so both optional chains end `circuit_open`. Two optional
    variants remain planned without a chain.
    """
    path, layout = configure(tmp_path, limit=1, attempts=attempts)
    started = Barrier(2, timeout=15)
    cutoff = Event()
    lock = Lock()
    optional = []
    original_resolve = BatchCircuit.resolve

    def observe(self, *args, **kwargs):
        opened = original_resolve(self, *args, **kwargs)
        if opened:
            cutoff.set()
        return opened

    def run(command, **kwargs):
        manifest = process_manifest(command)
        if manifest["render"]["active_output_type"] == "binaural_hrtf":
            return successful_process(command, **kwargs)
        with lock:
            ordinal = len(optional)
            optional.append(manifest["render"]["output_wav_path"])
        started.wait()
        if ordinal == 1:
            assert cutoff.wait(15)
            return successful_process(command, **kwargs)
        raise subprocess.CalledProcessError(29, command)

    monkeypatch.setattr(BatchCircuit, "resolve", observe)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)
    monkeypatch.setattr(BatchCircuit, "resolve", original_resolve)
    states = sorted((chain["state"], chain["attempts"][-1]["classification"]) for chain in chains(layout)
                    if "binaural_hrtf" not in chain["artifacts"][0]["path"])
    assert states == [("circuit_open", "accepted"), ("circuit_open", "unknown_process_failure")]
    assert read_json_object(circuit_path(layout))["open"] is True
    return path, layout


def files(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def chain_by(layout, classification):
    for path in (layout["run_root"] / "metadata/process_chains").glob("*/chain.json"):
        record = read_json_object(path)
        if record["state"] != "not_retryable" and record["attempts"][-1]["classification"] == classification:
            return path, record
    raise AssertionError(classification)


def reset(layout, **kwargs):
    options = {"apply": True, "confirm_stopped": True, "reason": REASON, "process_probe": lambda: []}
    options.update(kwargs)
    return circuit_reset.reset_batch_circuit(layout["run_root"], **options)


def test_inspection_reports_plan_without_writes(tmp_path, monkeypatch):
    _, layout = latched_batch(tmp_path, monkeypatch)
    failed_path, failed = chain_by(layout, "unknown_process_failure")
    accepted_path, _ = chain_by(layout, "accepted")
    before = files(layout["run_root"])
    report = circuit_reset.reset_batch_circuit(layout["run_root"], process_probe=lambda: [])
    assert report["applied"] is False and report["circuit_open"] is True
    assert report["limit"] == 1 and report["consecutive_failures"] == 0  # late acceptance reset the streak
    assert [item["chain_id"] for item in report["chains"]] == [failed["chain_id"]]
    assert report["chains"][0]["next_state"] == "scheduled"
    assert report["chains"][0]["consumed_attempts"] == 1 and report["chains"][0]["max_attempts"] == 3
    assert report["untouched_chains"] == [accepted_path.as_posix()]
    assert [f["classification"] for f in report["recent_failures"]] == ["unknown_process_failure"]
    assert files(layout["run_root"]) == before
    assert failed_path.exists()


@pytest.mark.parametrize("circuit", ["missing", "closed"])
@pytest.mark.parametrize("applied", [False, True])
def test_closed_or_missing_circuit_writes_nothing(tmp_path, circuit, applied):
    _, layout = configure(tmp_path)
    layout["render_index_path"].parent.mkdir(parents=True, exist_ok=True)
    if circuit == "closed":
        circuit_path(layout).write_text(json.dumps(
            {"schema_version": 1, "limit": 5, "consecutive_failures": 0, "open": False, "resolutions": []}))
    before = files(layout["run_root"])
    report = reset(layout) if applied else circuit_reset.reset_batch_circuit(layout["run_root"], process_probe=lambda: [])
    assert report["applied"] is False and report["circuit_open"] is False and report["chains"] == []
    assert files(layout["run_root"]) == before


@pytest.mark.parametrize("missing", ["confirm_stopped", "reason"])
def test_apply_requires_explicit_confirmations(tmp_path, monkeypatch, missing):
    _, layout = latched_batch(tmp_path, monkeypatch)
    before = files(layout["run_root"])
    with pytest.raises(RetryBlockedError, match="requiere"):
        reset(layout, **({"confirm_stopped": False} if missing == "confirm_stopped" else {"reason": "  "}))
    assert files(layout["run_root"]) == before


@pytest.mark.parametrize("check", ["active", "unavailable", "lock", "running"])
def test_uncertain_state_blocks_without_mutation(tmp_path, monkeypatch, check):
    _, layout = latched_batch(tmp_path, monkeypatch)

    def probe():
        if check == "unavailable":
            raise PermissionError("process table unavailable")
        return [{"pid": 123, "name": "MATLAB.exe"}] if check == "active" else []

    if check == "lock":
        (layout["render_index_path"].parent / "static_writer.lock").write_text("uncertain owner")
    if check == "running":
        path, record = chain_by(layout, "accepted")
        record["state"] = "running"
        path.write_text(json.dumps(record))
    before = files(layout["run_root"])
    with pytest.raises((RetryBlockedError, Exception), match="escritores|lock|reconcile-static|Writer lock"):
        reset(layout, process_probe=probe)
    assert files(layout["run_root"]) == before


def test_unsupported_latched_chain_shape_fails_closed(tmp_path, monkeypatch):
    _, layout = latched_batch(tmp_path, monkeypatch)
    path, record = chain_by(layout, "accepted")
    record["attempts"][-1]["retry_decision"] = "scheduled"
    path.write_text(json.dumps(record))
    before = files(layout["run_root"])
    with pytest.raises(RetryBlockedError, match="sin un intento resuelto por el corte"):
        reset(layout)
    assert files(layout["run_root"]) == before


def test_apply_publishes_record_archive_projection_and_closed_circuit(tmp_path, monkeypatch):
    _, layout = latched_batch(tmp_path, monkeypatch)
    failed_path, failed = chain_by(layout, "unknown_process_failure")
    accepted_path, accepted = chain_by(layout, "accepted")
    outcome_path = Path(failed["attempts"][-1]["outcome_path"])
    accepted_outcome = Path(accepted["attempts"][-1]["outcome_path"])
    untouched = {p: p.read_bytes() for p in (accepted_path, accepted_outcome,
                                               *(Path(a["path"]) for a in accepted["artifacts"]))}
    circuit_bytes, chain_bytes, outcome_bytes = (circuit_path(layout).read_bytes(), failed_path.read_bytes(),
                                                 outcome_path.read_bytes())

    report = reset(layout)

    journal = Path(report["reset_path"])
    record = read_json_object(journal)
    assert report["applied"] and record["reason"] == REASON and record["operator"]
    assert record["circuit_before_sha256"] and [e["chain_id"] for e in record["chains"]] == [failed["chain_id"]]
    assert (journal.parent / "circuit.before.json").read_bytes() == circuit_bytes
    assert (journal.parent / f"{failed['chain_id']}.chain.before.json").read_bytes() == chain_bytes
    assert (journal.parent / f"{failed['chain_id']}.outcome.before.json").read_bytes() == outcome_bytes
    assert Path(record["circuit_archive_path"]).read_bytes() == circuit_bytes
    closed = read_json_object(circuit_path(layout))
    assert closed == {"schema_version": 1, "limit": 1, "consecutive_failures": 0, "open": False,
                      "resolutions": [], "reset_id": record["reset_id"]}
    chain, outcome = read_json_object(failed_path), read_json_object(outcome_path)
    assert chain["state"] == "scheduled" and chain["attempts"][-1]["retry_decision"] == "scheduled"
    assert outcome["retry_decision"] == "scheduled" and outcome["retry_classification"] == "unknown_process_failure"
    assert outcome["circuit_reset"]["id"] == record["reset_id"] == chain["attempts"][-1]["circuit_reset"]["id"]
    assert outcome["exit_code"] == 29 and outcome["decision"] == "abort"
    assert {p: p.read_bytes() for p in untouched} == untouched
    retries.RetryChain(failed_path, chain).verify_resolved()


def test_exhausted_chain_is_not_renewed(tmp_path, monkeypatch):
    _, layout = latched_batch(tmp_path, monkeypatch, attempts=1)
    failed_path, _ = chain_by(layout, "unknown_process_failure")
    report = reset(layout)
    assert report["chains"][0]["next_state"] == "exhausted"
    assert read_json_object(failed_path)["state"] == "exhausted"


@pytest.mark.parametrize("fail_at", [1, 2, 3, 4])
def test_torn_publication_stays_blocked_and_completes_same_record(tmp_path, monkeypatch, fail_at):
    path, layout = latched_batch(tmp_path, monkeypatch)
    original = circuit_reset._save
    calls = []

    def flaky(target, record):
        calls.append(target)
        if len(calls) == fail_at:
            raise OSError("disk full")
        return original(target, record)

    monkeypatch.setattr(circuit_reset, "_save", flaky)
    with pytest.raises(OSError):
        reset(layout)
    monkeypatch.setattr(circuit_reset, "_save", original)
    assert read_json_object(circuit_path(layout))["open"] is True
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("torn reset authorized dispatch"))
    with pytest.raises(RetryBlockedError):
        render_static_scenes(path)
    if fail_at == 1:  # Record itself not published: a fresh one is created.
        assert not list((layout["render_index_path"].parent / "circuit_resets").glob("*/record.json"))
    pending = circuit_reset.reset_batch_circuit(layout["run_root"], process_probe=lambda: [])
    assert pending["pending_publication"] is (fail_at > 1)
    report = reset(layout)
    records = list((layout["render_index_path"].parent / "circuit_resets").glob("*/record.json"))
    assert len(records) == 1 and Path(report["reset_path"]) == records[0]
    assert read_json_object(circuit_path(layout))["open"] is False


def test_repeated_reset_is_a_no_op(tmp_path, monkeypatch):
    _, layout = latched_batch(tmp_path, monkeypatch)
    reset(layout)
    before = files(layout["run_root"])
    report = reset(layout)
    assert report["applied"] is False and report["circuit_open"] is False
    assert files(layout["run_root"]) == before


def test_batch_circuit_loads_reset_record(tmp_path, monkeypatch):
    _, layout = latched_batch(tmp_path, monkeypatch)
    reset(layout)
    circuit = BatchCircuit(circuit_path(layout), 1)
    assert circuit.is_open is False and circuit.record["reset_id"]


def test_resume_after_reset_reuses_accepted_and_dispatches_only_pending(tmp_path, monkeypatch):
    path, layout = latched_batch(tmp_path, monkeypatch)
    failed_path, failed = chain_by(layout, "unknown_process_failure")
    accepted_path, accepted = chain_by(layout, "accepted")
    accepted_bytes = {Path(a["path"]): Path(a["path"]).read_bytes() for a in accepted["artifacts"]}
    reset(layout)
    launched = []

    def run(command, **kwargs):
        launched.append(process_manifest(command)["render"]["output_wav_path"])
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    _, summary = render_static_scenes(path)

    assert accepted["artifacts"][0]["path"] not in {Path(p).resolve().as_posix() for p in launched}
    assert len(launched) == 3  # Reprogrammed failure plus the two never-dispatched variants.
    assert {p: p.read_bytes() for p in accepted_bytes} == accepted_bytes
    assert read_json_object(accepted_path) == accepted
    chain = read_json_object(failed_path)
    assert [a["ordinal"] for a in chain["attempts"]] == [1, 2]
    assert chain["attempts"][1]["attempt_id"] != failed["attempts"][0]["attempt_id"]
    assert chain["attempts"][1]["classification"] == "accepted"
    # The run summary counts this invocation's processes; the prior failure stays in its outcome.
    assert summary["process_retries"] == 1 and summary["matlab_invocations"] == 3
    prior = read_json_object(Path(failed["attempts"][0]["outcome_path"]))
    assert prior["process_status"] == "failed" and prior["exit_code"] == 29
    assert prior["retry_decision"] == "scheduled" and prior["circuit_reset"]
    assert summary["circuit_open"] is False and summary["planned_variants"] == 0


def test_new_cutoff_after_reset_counts_only_new_failures(tmp_path, monkeypatch):
    path, layout = latched_batch(tmp_path, monkeypatch)
    reset(layout)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda command, **kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(29, command)))
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)
    circuit = read_json_object(circuit_path(layout))
    assert circuit["open"] is True and circuit["reset_id"]
    assert 1 <= len(circuit["resolutions"]) <= 2  # Only post-reset invocations (worker bound 2).
    assert all(item["process_failed"] for item in circuit["resolutions"])


def test_manual_circuit_edit_without_record_still_blocks(tmp_path, monkeypatch):
    path, layout = latched_batch(tmp_path, monkeypatch)
    record = read_json_object(circuit_path(layout))
    record["open"] = False
    circuit_path(layout).write_text(json.dumps(record))
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("manual edit authorized dispatch"))
    with pytest.raises(RetryBlockedError, match="terminal \\(circuit_open\\)"):
        render_static_scenes(path)


def test_tampered_record_blocks_resume(tmp_path, monkeypatch):
    path, layout = latched_batch(tmp_path, monkeypatch)
    report = reset(layout)
    journal = Path(report["reset_path"])
    record = read_json_object(journal)
    record["reason"] = "rewritten later"
    journal.write_text(json.dumps(record))
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("tampered reset authorized dispatch"))
    with pytest.raises(RetryBlockedError, match="reinicio"):
        render_static_scenes(path)


def test_open_circuit_error_keeps_message_and_suggests_reset(tmp_path, monkeypatch):
    path, layout = latched_batch(tmp_path, monkeypatch)
    with pytest.raises(RetryBlockedError, match="Corte de procesos MATLAB persistido") as caught:
        render_static_scenes(path)
    assert any("reset-circuit" in note and "--apply" in note for note in caught.value.__notes__)


def test_cli_inspect_reject_and_apply(tmp_path, monkeypatch):
    _, layout = latched_batch(tmp_path, monkeypatch)
    monkeypatch.setattr(circuit_reset, "active_render_writers", lambda: [])
    runner = CliRunner()
    root = str(layout["run_root"])
    before = files(layout["run_root"])
    result = runner.invoke(app, ["reset-circuit", root])
    assert result.exit_code == 0 and "Solo lectura" in result.output and "scheduled" in result.output
    assert files(layout["run_root"]) == before
    result = runner.invoke(app, ["reset-circuit", root, "--apply"])
    assert result.exit_code == 1 and "Reinicio bloqueado" in result.output
    assert files(layout["run_root"]) == before
    result = runner.invoke(app, ["reset-circuit", root, "--apply", "--confirm-stopped", "--reason", REASON])
    assert result.exit_code == 0 and "Reiniciado" in result.output and "No se ha lanzado MATLAB" in result.output
    assert read_json_object(circuit_path(layout))["open"] is False


def test_outcome_outside_run_root_blocks(tmp_path, monkeypatch):
    _, layout = latched_batch(tmp_path, monkeypatch)
    path, record = chain_by(layout, "unknown_process_failure")
    record["attempts"][-1]["outcome_path"] = (tmp_path / "elsewhere" / "outcome.json").as_posix()
    path.write_text(json.dumps(record))
    before = files(layout["run_root"])
    with pytest.raises(RetryBlockedError, match="no pertenece al lote"):
        reset(layout)
    assert files(layout["run_root"]) == before
