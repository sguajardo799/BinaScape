import json
import subprocess
from pathlib import Path

import pytest

from acoustic_orchestrator.config.models import MatlabProcessRetryConfig
from acoustic_orchestrator.pipeline import process_retries as retries
from acoustic_orchestrator.pipeline.matlab_runner import MatlabProcessResult, render_manifest_with_matlab
from acoustic_orchestrator.pipeline.render_acceptance import RenderAcceptance, persist_acceptance
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_validation import RenderValidation


@pytest.fixture
def manifest(tmp_path):
    path = tmp_path / "scene__hrtf.json"
    path.write_text(json.dumps({"scene_id": "scene", "render": {
        "active_output_type": "hrtf", "seed": 42,
        "output_wav_path": str(tmp_path / "out.wav"),
        "output_metadata_path": str(tmp_path / "out.json"),
    }}))
    return path


def chain(manifest, **policy):
    return retries.open_chain(manifest, manifest.parent / "chains", MatlabProcessRetryConfig(**policy))


def acceptance(attempt, *, code=1, decision="abort", completion="missing", observed=True):
    error = subprocess.CalledProcessError(code, ["matlab"] ) if code else None
    process = MatlabProcessResult(attempt, code, error, observed, "failed" if code else "succeeded")
    result = RenderAcceptance(process, RenderValidation(missing=["wav_missing"]),
                              {"status": completion, "issues": []}, decision, error, [])
    attempt.record.update(termination_observed=observed, exit_code=code)
    persist_acceptance(result, lambda: None)
    return result


def test_reservation_restart_backoff_and_links(manifest):
    first = chain(manifest)
    a = first.reserve_attempt(manifest)
    persisted = read_json_object(first.path)
    assert persisted["state"] == "running"
    assert persisted["attempts"][0]["attempt_id"] == a.attempt_id
    assert first.resolve_attempt(acceptance(a), clock=lambda: 100) == "scheduled"
    first.archive_attempt()
    resumed = chain(manifest, max_attempts=100)  # Policy is pinned, not renewed.
    sleeps = []
    b = resumed.reserve_attempt(manifest, clock=lambda: 101, sleep=sleeps.append)
    assert sleeps == [1]
    assert b.record["previous_attempt_id"] == a.attempt_id
    assert b.record["attempt_ordinal"] == 2
    assert b.manifest_sha256 == a.manifest_sha256
    assert a.attempt_id != b.attempt_id
    assert resumed.resolve_attempt(acceptance(b), clock=lambda: 200) == "scheduled"
    assert resumed.record["not_before"] == 204
    resumed.archive_attempt()
    c = resumed.reserve_attempt(manifest, clock=lambda: 300)
    assert resumed.resolve_attempt(acceptance(c)) == "exhausted"
    with pytest.raises(retries.RetryBlockedError):
        chain(manifest).reserve_attempt(manifest)


def test_max_attempts_one_and_explicit_new_chain(manifest):
    old = chain(manifest, max_attempts=1)
    attempt = old.reserve_attempt(manifest)
    assert old.resolve_attempt(acceptance(attempt)) == "exhausted"
    new = retries.open_chain(manifest, manifest.parent / "chains", MatlabProcessRetryConfig(),
                             resume_if_possible=False, overwrite_existing=True)
    assert old.path.exists() and old.record["chain_id"] != new.record["chain_id"]
    assert chain(manifest).path == new.path


def test_running_and_mutated_input_block(manifest):
    current = chain(manifest)
    current.reserve_attempt(manifest)
    with pytest.raises(retries.RetryBlockedError):
        chain(manifest).reserve_attempt(manifest)
    manifest.write_text(manifest.read_text() + " ")
    with pytest.raises(retries.RetryBlockedError):
        chain(manifest)


@pytest.mark.parametrize("code,decision,completion,observed,expected", [
    (1, "abort", "missing", True, "unknown_process_failure"),
    (0, "abort", "missing", True, "invalid_render"),
    (1, "continue_with_warning", "valid", True, "accepted"),
    (0, "continue", "missing", True, "accepted"),
    (1, "retry_acoustic", "valid", True, "acoustic_rejection"),
    (1, "abort", "invalid", True, "integrity_conflict"),
    (None, "abort", "missing", False, "termination_unobserved"),
])
def test_classification_matrix(manifest, code, decision, completion, observed, expected):
    attempt = chain(manifest).reserve_attempt(manifest)
    result = acceptance(attempt, code=code, decision=decision, completion=completion, observed=observed)
    assert retries.classify_attempt(result) == expected


def test_failure_report_and_persistence_fail_closed(manifest):
    result = acceptance(chain(manifest).reserve_attempt(manifest))
    assert retries.classify_attempt(result, failure={"status": "valid", "category": "invalid_input"}) == "deterministic_failure"
    assert retries.classify_attempt(result, failure={"status": "invalid"}) == "integrity_conflict"
    result.validation.invalid.append("render_metadata_scene_id_mismatch")
    assert retries.classify_attempt(result) == "integrity_conflict"
    result.process.attempt.record["commit_status"] = "pending"
    assert retries.classify_attempt(result) == "storage_failure"


def test_report_is_inspected_automatically_and_launch_failure_blocks(manifest):
    attempt = chain(manifest).reserve_attempt(manifest)
    result = acceptance(attempt)
    attempt.failure_path.write_text("not json")
    assert retries.classify_attempt(result) == "integrity_conflict"
    attempt.failure_path.unlink()
    result.process.termination_observed = False
    result.process.process_status = "launch_failed"
    assert retries.classify_attempt(result) == "launch_failed"


def test_reservation_storage_failure_prevents_launch(manifest, monkeypatch):
    current = chain(manifest)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(retries, "_save", fail)
    with pytest.raises(OSError):
        current.reserve_attempt(manifest)
    assert not list(manifest.parent.glob("**/input_manifest.json"))
    with pytest.raises(retries.RetryBlockedError):
        current.reserve_attempt(manifest)


@pytest.mark.parametrize("commit_number", [1, 2, 3, 4])
def test_archive_all_commit_interruptions(manifest, monkeypatch, commit_number):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    for artifact in current.record["artifacts"]:
        Path(artifact["path"]).write_bytes(b"evidence")
    current.resolve_attempt(acceptance(attempt), clock=lambda: 0)
    save = retries._save
    count = 0

    def interrupted(path, record):
        nonlocal count
        count += 1
        if count == commit_number:
            raise OSError("interrupted commit")
        save(path, record)

    monkeypatch.setattr(retries, "_save", interrupted)
    with pytest.raises(OSError):
        current.archive_attempt()
    monkeypatch.setattr(retries, "_save", save)
    resumed = chain(manifest)
    resumed.archive_attempt()
    assert len(list((attempt.directory / "archived_outputs").iterdir())) == 2
    resumed.reserve_attempt(manifest, clock=lambda: 100)


def test_archive_partial_outputs_and_reconcile_interrupted_move(manifest, monkeypatch):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    for item in current.record["artifacts"]:
        Path(item["path"]).write_bytes(b"partial bytes")
    current.resolve_attempt(acceptance(attempt), clock=lambda: 0)
    save = retries._save
    calls = 0

    def fail_after_move(path, record):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("journal storage failed")
        save(path, record)

    monkeypatch.setattr(retries, "_save", fail_after_move)
    with pytest.raises(OSError):
        current.archive_attempt()
    with pytest.raises(retries.RetryBlockedError):
        current.reserve_attempt(manifest)
    monkeypatch.setattr(retries, "_save", save)
    current = chain(manifest)
    current.archive_attempt()
    journal = read_json_object(attempt.directory / "archive.json")
    assert journal["state"] == "complete"
    for item in journal["entries"]:
        assert not Path(item["original_path"]).exists()
        assert Path(item["archive_path"]).read_bytes() == b"partial bytes"
        assert item["size_bytes"] == 13 and len(item["sha256"]) == 64
    current.reserve_attempt(manifest, clock=lambda: 100)


def test_archive_ambiguity_blocks(manifest):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    output = Path(current.record["artifacts"][0]["path"])
    output.write_bytes(b"partial")
    current.resolve_attempt(acceptance(attempt))
    current.archive_attempt()
    output.write_bytes(b"different writer")
    with pytest.raises(retries.RetryBlockedError):
        current.archive_attempt()
    with pytest.raises(retries.RetryBlockedError):
        current.reserve_attempt(manifest)


def test_protected_output_not_moved(manifest):
    output = manifest.parent / "out.wav"
    output.write_bytes(b"protected")
    with pytest.raises(retries.RetryBlockedError):
        chain(manifest)
    assert output.read_bytes() == b"protected"


def test_circuit_latched_restart_and_resolution_order(tmp_path):
    path = tmp_path / "circuit.json"
    circuit = retries.BatchCircuit(path, 2)
    assert not circuit.resolve("a", "unknown_process_failure", process_failed=True)
    assert not circuit.resolve("b", "accepted", process_failed=False)
    assert not circuit.resolve("c", "unknown_process_failure", process_failed=True)
    assert circuit.resolve("d", "unknown_process_failure", process_failed=True)
    assert circuit.resolve("e", "accepted", process_failed=False)
    assert retries.BatchCircuit(path, 100).is_open
    assert [item["attempt_id"] for item in circuit.record["resolutions"]] == list("abcde")
    assert not retries.BatchCircuit(path, 2, force_reset=True).is_open


def test_delay_cap():
    policy = MatlabProcessRetryConfig()
    assert [retries.retry_delay(policy, n) for n in range(1, 7)] == [2, 4, 8, 16, 30, 30]
    assert retries.retry_delay(policy, 1000000) == 30


def test_runner_uses_reserved_attempt_and_failure_path(manifest, monkeypatch):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    commands = []

    def run(command, **kwargs):
        assert read_json_object(current.path)["attempts"][0]["attempt_id"] == attempt.attempt_id
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    result = render_manifest_with_matlab(manifest, attempt=attempt)
    assert result.attempt is attempt
    assert "'failure_path'" in commands[0][2]
    assert attempt.record["failure_path"] == attempt.failure_path.as_posix()
    with pytest.raises(ValueError):
        render_manifest_with_matlab(manifest, attempt=attempt)


def change_realization(manifest):
    payload = read_json_object(manifest)
    payload["render"]["seed"] += 1
    manifest.write_text(json.dumps(payload))


@pytest.mark.parametrize("decision", ["continue", "continue_with_warning", "retry_acoustic"])
def test_new_acoustic_realization_has_new_budget_and_ancestry(manifest, decision):
    previous = chain(manifest)
    attempt = previous.reserve_attempt(manifest)
    previous.resolve_attempt(acceptance(attempt, code=0 if decision == "continue" else 1,
                                        decision=decision, completion="valid"))
    change_realization(manifest)
    current = chain(manifest)
    assert current.record["supersedes"] == [previous.record["chain_id"]]
    assert read_json_object(previous.path)["superseded_by"] == current.record["chain_id"]
    assert read_json_object(attempt.outcome_path)["decision"] == decision
    assert current.record["input_sha256"] != previous.record["input_sha256"]
    new_attempt = current.reserve_attempt(manifest)
    assert new_attempt.record["attempt_ordinal"] == 1
    assert new_attempt.record["previous_attempt_id"] is None
    assert new_attempt.manifest_sha256 != attempt.manifest_sha256


def test_exhausted_realization_requires_overwrite_to_change_input(manifest):
    previous = chain(manifest, max_attempts=1)
    previous.resolve_attempt(acceptance(previous.reserve_attempt(manifest)))
    change_realization(manifest)
    with pytest.raises(retries.RetryBlockedError):
        chain(manifest)
    current = retries.open_chain(manifest, manifest.parent / "chains", MatlabProcessRetryConfig(),
                                 overwrite_existing=True)
    assert current.record["supersedes"] == [previous.record["chain_id"]]


@pytest.mark.parametrize("state", ["ready", "running", "scheduled"])
def test_pending_realization_cannot_be_replaced_by_overwrite(manifest, state):
    previous = chain(manifest)
    if state != "ready":
        attempt = previous.reserve_attempt(manifest)
        if state == "scheduled":
            previous.resolve_attempt(acceptance(attempt))
    change_realization(manifest)
    with pytest.raises(retries.RetryBlockedError):
        retries.open_chain(manifest, manifest.parent / "chains", MatlabProcessRetryConfig(),
                           overwrite_existing=True)


@pytest.mark.parametrize("issue", ["wav_riff_size_mismatch", "wav_payload_truncated",
                                    "wav_channel_count_mismatch", "wav_sample_rate_mismatch"])
def test_nonzero_partial_wav_is_retryable_but_receipt_contradiction_is_not(manifest, issue):
    current = chain(manifest)
    result = acceptance(current.reserve_attempt(manifest))
    result.validation.invalid.append(issue)
    assert retries.classify_attempt(result) == "unknown_process_failure"
    result.completion["status"] = "invalid"
    assert retries.classify_attempt(result) == "integrity_conflict"
    result.completion["status"] = "missing"
    result.process.exit_code = 0
    assert retries.classify_attempt(result) == "invalid_render"


@pytest.mark.parametrize("issue", ["render_metadata_scene_id_mismatch", "render_metadata_hrtf_path_mismatch",
                                    "render_metadata_effective_seed_mismatch", "source_audio_path_mismatch: s"])
def test_metadata_provenance_conflicts_remain_non_retryable(manifest, issue):
    result = acceptance(chain(manifest).reserve_attempt(manifest))
    result.validation.invalid.append(issue)
    assert retries.classify_attempt(result) == "integrity_conflict"


def snapshot_tree(root):
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


def test_inspect_scheduled_chain_is_read_only_and_authorizes_own_partial_outputs(manifest):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    Path(current.record["artifacts"][0]["path"]).write_bytes(b"truncated wav")
    current.resolve_attempt(acceptance(attempt))
    before = snapshot_tree(manifest.parent)
    inspected = retries.inspect_chain(manifest, manifest.parent / "chains", require_scheduled=True)
    assert inspected is not None and inspected.path == current.path
    assert snapshot_tree(manifest.parent) == before
    current.archive_attempt()
    before = snapshot_tree(manifest.parent)
    assert retries.inspect_chain(manifest, manifest.parent / "chains", require_scheduled=True) is not None
    assert snapshot_tree(manifest.parent) == before


@pytest.mark.parametrize("state", ["absent", "ready", "running", "exhausted", "changed_input"])
def test_inspect_does_not_authorize_non_scheduled_outputs(manifest, state):
    if state != "absent":
        current = chain(manifest, max_attempts=1 if state == "exhausted" else 3)
        if state in {"running", "exhausted", "changed_input"}:
            attempt = current.reserve_attempt(manifest)
            if state != "running":
                current.resolve_attempt(acceptance(attempt))
            if state == "changed_input":
                change_realization(manifest)
    before = snapshot_tree(manifest.parent)
    assert retries.inspect_chain(manifest, manifest.parent / "chains", require_scheduled=True) is None
    assert snapshot_tree(manifest.parent) == before


@pytest.mark.parametrize("field,value", [("termination_observed", False), ("commit_status", "pending"),
                                          ("chain_id", "foreign"), ("manifest_sha256", "0" * 64),
                                          ("exit_code", 0)])
def test_inspect_rejects_unobserved_or_contradictory_scheduled_outcome(manifest, field, value):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    current.resolve_attempt(acceptance(attempt))
    outcome = read_json_object(attempt.outcome_path)
    outcome[field] = value
    attempt.outcome_path.write_text(json.dumps(outcome))
    before = snapshot_tree(manifest.parent)
    with pytest.raises(retries.RetryBlockedError):
        retries.inspect_chain(manifest, manifest.parent / "chains", require_scheduled=True)
    assert snapshot_tree(manifest.parent) == before


def preflight(manifest, *, digest=None, reusable=False, overwrite=False, force=False):
    return retries.preflight_chain(
        manifest, manifest.parent / "chains", input_sha256=digest or retries.sha256_file(manifest),
        reusable=reusable, overwrite_existing=overwrite, force_reset=force,
    )


@pytest.mark.parametrize("state", ["exhausted", "not_retryable", "circuit_open", "running"])
@pytest.mark.parametrize("runtime_exists", [False, True])
def test_preflight_terminal_or_running_veto_without_public_artifacts(manifest, state, runtime_exists):
    current = chain(manifest, max_attempts=1)
    attempt = current.reserve_attempt(manifest)
    if state != "running":
        result = acceptance(attempt, code=0 if state == "not_retryable" else 1)
        current.resolve_attempt(result, circuit_open=state == "circuit_open")
    digest = retries.sha256_file(manifest)
    if not runtime_exists:
        manifest.unlink()
    before = snapshot_tree(manifest.parent)
    with pytest.raises(retries.RetryBlockedError):
        preflight(manifest, digest=digest, reusable=True, overwrite=True)
    assert snapshot_tree(manifest.parent) == before


@pytest.mark.parametrize("same_input", [False, True])
def test_preflight_running_is_never_hidden_by_force_or_reuse(manifest, same_input):
    current = chain(manifest)
    current.reserve_attempt(manifest)
    if not same_input:
        change_realization(manifest)
    before = snapshot_tree(manifest.parent)
    with pytest.raises(retries.RetryBlockedError):
        preflight(manifest, reusable=True, overwrite=True, force=True)
    assert snapshot_tree(manifest.parent) == before


def test_preflight_ready_or_legacy_grants_no_ownership_without_writes(manifest):
    before = snapshot_tree(manifest.parent)
    assert preflight(manifest) is None
    assert snapshot_tree(manifest.parent) == before
    chain(manifest)
    before = snapshot_tree(manifest.parent)
    assert preflight(manifest) is None
    assert snapshot_tree(manifest.parent) == before


@pytest.mark.parametrize("outputs", ["absent", "corrupt", "reusable"])
def test_preflight_formerly_accepted_replacement_or_reuse(manifest, outputs):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    current.resolve_attempt(acceptance(attempt, code=0, decision="continue"))
    if outputs != "absent":
        Path(current.record["artifacts"][0]["path"]).write_bytes(b"existing")
    before = snapshot_tree(manifest.parent)
    assert preflight(manifest, reusable=outputs == "reusable", overwrite=outputs == "corrupt") is None
    assert snapshot_tree(manifest.parent) == before
    if outputs == "corrupt":
        with pytest.raises(retries.RetryBlockedError):
            preflight(manifest)


@pytest.mark.parametrize("tamper", ["snapshot", "outcome", "decision", "identity"])
def test_preflight_accepted_integrity_checked_before_authorized_replacement(manifest, tamper):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    current.resolve_attempt(acceptance(attempt, code=0, decision="continue"))
    output = Path(current.record["artifacts"][0]["path"])
    output.write_bytes(b"protected until evidence verified")
    if tamper == "snapshot":
        attempt.snapshot_path.write_bytes(b"altered historical input")
    else:
        record = read_json_object(attempt.outcome_path)
        if tamper == "outcome":
            record["retry_policy"]["max_attempts"] += 1
        elif tamper == "decision":
            record["decision"] = "abort"
        else:
            record["scene_id"] = "foreign-scene"
        attempt.outcome_path.write_text(json.dumps(record))
    before = snapshot_tree(manifest.parent)
    with pytest.raises(retries.RetryBlockedError):
        preflight(manifest, overwrite=True)
    assert snapshot_tree(manifest.parent) == before
    assert preflight(manifest, overwrite=True, force=True) is None
    assert snapshot_tree(manifest.parent) == before


@pytest.mark.parametrize("force", [False, True])
def test_preflight_unobserved_terminal_cannot_be_reset(manifest, force):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    current.resolve_attempt(acceptance(attempt, code=None, observed=False))
    before = snapshot_tree(manifest.parent)
    with pytest.raises(retries.RetryBlockedError):
        preflight(manifest, overwrite=True, force=force)
    assert snapshot_tree(manifest.parent) == before


def test_preflight_scheduled_owns_only_verified_archive_and_needs_no_runtime(manifest):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    output = Path(current.record["artifacts"][0]["path"])
    output.write_bytes(b"partial")
    current.resolve_attempt(acceptance(attempt))
    digest = retries.sha256_file(manifest)
    manifest.unlink()
    before = snapshot_tree(manifest.parent)
    inspected = preflight(manifest, digest=digest)
    assert inspected is not None and inspected.path == current.path
    assert snapshot_tree(manifest.parent) == before
    current.archive_attempt()
    before = snapshot_tree(manifest.parent)
    assert preflight(manifest, digest=digest) is not None
    assert snapshot_tree(manifest.parent) == before
    journal = read_json_object(attempt.directory / "archive.json")
    Path(journal["entries"][0]["archive_path"]).write_bytes(b"tampered")
    before = snapshot_tree(manifest.parent)
    with pytest.raises(retries.RetryBlockedError):
        preflight(manifest, digest=digest)
    assert snapshot_tree(manifest.parent) == before


def test_verify_resolved_blocks_archiving_tampered_snapshot(manifest):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    output = Path(current.record["artifacts"][0]["path"])
    output.write_bytes(b"evidence")
    current.resolve_attempt(acceptance(attempt))
    assert current.verify_resolved()["attempt_id"] == attempt.attempt_id
    attempt.snapshot_path.write_bytes(b"tampered")
    before = snapshot_tree(manifest.parent)
    with pytest.raises(retries.RetryBlockedError):
        current.archive_attempt()
    assert snapshot_tree(manifest.parent) == before


@pytest.mark.parametrize("decision", ["continue", "retry_acoustic"])
def test_preflight_new_realization_transition_is_verified_read_only(manifest, decision):
    current = chain(manifest)
    attempt = current.reserve_attempt(manifest)
    current.resolve_attempt(acceptance(attempt, code=0, decision=decision))
    change_realization(manifest)
    before = snapshot_tree(manifest.parent)
    assert preflight(manifest) is None
    assert snapshot_tree(manifest.parent) == before
    attempt.snapshot_path.write_bytes(b"tampered")
    before = snapshot_tree(manifest.parent)
    with pytest.raises(retries.RetryBlockedError):
        preflight(manifest, overwrite=True)
    assert snapshot_tree(manifest.parent) == before


@pytest.mark.parametrize("force", [False, True])
def test_preflight_incompatible_pending_realization_blocks(manifest, force):
    current = chain(manifest)
    current.resolve_attempt(acceptance(current.reserve_attempt(manifest)))
    change_realization(manifest)
    before = snapshot_tree(manifest.parent)
    with pytest.raises(retries.RetryBlockedError):
        preflight(manifest, overwrite=True, force=force)
    assert snapshot_tree(manifest.parent) == before


def test_preflight_changed_terminal_budget_requires_authorized_overwrite(manifest):
    current = chain(manifest, max_attempts=1)
    current.resolve_attempt(acceptance(current.reserve_attempt(manifest)))
    change_realization(manifest)
    before = snapshot_tree(manifest.parent)
    with pytest.raises(retries.RetryBlockedError):
        preflight(manifest)
    assert preflight(manifest, overwrite=True) is None
    assert snapshot_tree(manifest.parent) == before


def test_preflight_checks_ancestry_even_with_missing_runtime(manifest):
    current = chain(manifest)
    current.record["superseded_by"] = "missing-successor"
    current.save()
    digest = retries.sha256_file(manifest)
    manifest.unlink()
    before = snapshot_tree(manifest.parent)
    with pytest.raises(retries.RetryBlockedError):
        preflight(manifest, digest=digest, overwrite=True, force=True)
    assert snapshot_tree(manifest.parent) == before


def test_previous_process_error_preserves_recorded_cause_and_evidence_links(manifest):
    current = chain(manifest)
    assert current.previous_process_error() is None
    attempt = current.reserve_attempt(manifest)
    attempt.record["command"] = ["vendor-matlab", "-batch", "original command"]
    current.resolve_attempt(acceptance(attempt, code=73))
    stored = read_json_object(attempt.outcome_path)
    stored["error"]["message"] = "original vendor error preserved verbatim"
    attempt.outcome_path.write_text(json.dumps(stored))
    # Error reconstruction must remain possible when later snapshot/archive
    # integrity fails, without laundering the chain into executable state.
    attempt.snapshot_path.write_bytes(b"corrupt snapshot")
    before = snapshot_tree(manifest.parent)
    error = current.previous_process_error()
    assert isinstance(error, subprocess.CalledProcessError)
    assert error.returncode == 73
    assert error.cmd == ["vendor-matlab", "-batch", "original command"]
    notes = "\n".join(error.__notes__)
    assert "original vendor error preserved verbatim" in notes
    assert str(current.path) in notes and str(attempt.outcome_path) in notes
    assert snapshot_tree(manifest.parent) == before
    with pytest.raises(retries.RetryBlockedError):
        current.verify_resolved()
