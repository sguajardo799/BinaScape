import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from acoustic_orchestrator.pipeline import render_pipeline
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.persistence_recovery import inspect_persistence_repair
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from test_acoustic_transactions import snapshot
from test_generate_manifests import _write_valid_raven_metadata
from test_render_acceptance import publish_process_receipt
from test_render_diagnostics_integration import process_manifest
from test_render_resilience import configure


def observed_pending(tmp_path, monkeypatch, *, kind="accepted", code=1, scenes=1, limit=5):
    from uuid import uuid4
    config, layout = configure(tmp_path.parent / ("obs_" + uuid4().hex[:8]), scenes=scenes, workers=scenes)
    import yaml
    raw = yaml.safe_load(config.read_text())
    raw["execution"]["matlab_process_retry"]["max_consecutive_failures"] = limit
    config.write_text(yaml.safe_dump(raw))
    from threading import Barrier
    barrier = Barrier(scenes, timeout=20)
    (layout["render_outputs_root"] / "binaural_hrtf").mkdir(parents=True, exist_ok=True)

    def run(command, **kwargs):
        actual_kind, actual_code = kind, code
        if kind == "mixed":
            actual_kind, actual_code = (("unknown", 1) if process_manifest(command)["scene_id"].endswith("0001") else ("accepted", 0))
        if actual_kind in {"accepted", "t30"}:
            _write_valid_raven_metadata(process_manifest(command), [0.5] * 9 if actual_kind == "t30" else None)
            publish_process_receipt(command)
        elif actual_kind == "deterministic":
            import re
            match = re.search(r"run_raven_static_render\('((?:[^']|'')*)'", command[2])
            directory = Path(match[1].replace("''", "'")).parent
            record = read_json_object(directory / "outcome.json")
            report = {key: record[key] for key in ("attempt_id", "variant_id", "manifest_sha256")}
            report.update(schema_version=1, category="invalid_input", error={"identifier": "", "message": "invalid input"})
            (directory / "failure.json").write_text(json.dumps(report))
        barrier.wait()
        if actual_code:
            raise subprocess.CalledProcessError(actual_code, command)
        return SimpleNamespace(returncode=0)

    with monkeypatch.context() as patch:
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
        patch.setattr(render_pipeline._RenderCoordinator, "resolve", lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            render_pipeline.render_static_scenes(config)
    outcome_path = next(layout["render_attempts_dir"].glob("*/outcome.json"))
    outcome = read_json_object(outcome_path)
    paths = load_variant_index(layout["render_index_path"])[outcome["variant_id"]]
    assert outcome["commit_status"] == outcome["decision"] == "pending"
    assert outcome["termination_observed"]
    return config, layout, paths, outcome_path


def inspect(layout, paths):
    return inspect_persistence_repair(paths, read_json_object(Path(paths["runtime_manifest_path"])),
                                      load_variant_index(layout["render_index_path"]),
                                      layout["render_attempts_dir"].parent / "process_chains",
                                      metadata_required=True, canonical=True)


@pytest.mark.parametrize("kind,code,classification,decision", [
    ("accepted", 0, "accepted", "not_retryable"), ("accepted", 1, "accepted", "not_retryable"),
    ("unknown", 1, "unknown_process_failure", "scheduled"),
    ("t30", 0, "acoustic_rejection", "not_retryable"),
    ("deterministic", 1, "deterministic_failure", "not_retryable"),
])
def test_observed_pending_evaluates_without_writes_or_backend(tmp_path, monkeypatch, kind, code, classification, decision):
    _, layout, paths, outcome_path = observed_pending(tmp_path, monkeypatch, kind=kind, code=code)
    before = snapshot(layout["run_root"])
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **kw: pytest.fail("evaluation must not invoke a backend"))
    candidate = inspect(layout, paths)
    assert candidate.action == "evaluate_observed_attempt"
    assert candidate.outcome["retry_classification"] == candidate.resolution["classification"] == classification
    assert candidate.chain["state"] == candidate.outcome["retry_decision"] == decision
    assert candidate.outcome["schema_version"] == 3
    original = read_json_object(outcome_path)
    for key in ("attempt_id", "chain_id", "exit_code", "error", "finished_at", "duration_s", "command"):
        assert candidate.outcome[key] == original[key]
    assert before == snapshot(layout["run_root"])
    if kind == "accepted" and code:
        assert candidate.outcome["decision"] == "continue_with_warning"
        assert candidate.outcome["recovery_reason"] == "post_completion"
    if kind == "unknown":
        assert candidate.outcome["decision"] == "abort"


@pytest.mark.parametrize("damage", ["identity", "hash", "termination", "foreign_receipt", "classification"])
def test_observed_pending_rejects_unaccredited_evidence(tmp_path, monkeypatch, damage):
    _, layout, paths, outcome_path = observed_pending(tmp_path, monkeypatch)
    record = read_json_object(outcome_path)
    if damage == "identity":
        record["chain_id"] = "foreign"
    elif damage == "hash":
        Path(record["manifest_snapshot_path"]).write_text("{}")
    elif damage == "termination":
        record["termination_observed"] = False
    elif damage == "classification":
        record["retry_classification"] = "accepted"
    else:
        path = Path(record["completion_path"])
        receipt = read_json_object(path)
        receipt["attempt_id"] = "foreign"
        path.write_text(json.dumps(receipt))
    if damage in {"identity", "termination", "classification"}:
        outcome_path.write_text(json.dumps(record))
    before = snapshot(layout["run_root"])
    with pytest.raises((RuntimeError, ValueError, OSError)):
        inspect(layout, paths)
    assert before == snapshot(layout["run_root"])


def prepare_batch_fixture(candidate, layout):
    """Publish only the administrative plan; no active result is modified."""
    import base64
    from copy import deepcopy
    from uuid import uuid4
    from acoustic_orchestrator.pipeline.persistence_journal import json_bytes, observed_plan_digest, journal_path
    circuit_path = layout["render_index_path"].parent / "matlab_process_circuit.json"
    before = read_json_object(circuit_path)
    after = deepcopy(before)
    resolution = candidate.resolution
    after["consecutive_failures"] = 0 if resolution["classification"] == "accepted" else int(resolution["process_failed"])
    after["open"] = after["consecutive_failures"] >= after["limit"]
    after["resolutions"].append(resolution)
    if candidate.resource_inventory is not None:
        resource_path = journal_path(candidate.outcome_path).parent / "raven_resources.json"
        import hashlib
        candidate.outcome.update(raven_resources_path=resource_path.resolve().as_posix(),
                                 raven_resources_sha256=hashlib.sha256(json_bytes(candidate.resource_inventory)).hexdigest(),
                                 raven_isolation="verified_private")
    identifier = uuid4().hex
    path = layout["run_root"] / "metadata/observed_recoveries" / identifier / "plan.json"
    entry = {"attempt_id": candidate.outcome["attempt_id"], "outcome_path": candidate.outcome_path.resolve().as_posix(),
             "chain_path": candidate.chain_path.resolve().as_posix(), "before_outcome": candidate.before_outcome,
             "before_chain": candidate.before_chain, "before_index": candidate.before_index,
             "resolution": resolution, "circuit_after": after, "resource_inventory": candidate.resource_inventory,
             "targets": {"outcome": candidate.outcome, "chain": candidate.chain, "index": candidate.index}}
    plan = {"schema_version": 1, "action": "evaluate_observed_batch", "plan_id": identifier,
            "root": layout["run_root"].resolve().as_posix(), "index_path": layout["render_index_path"].resolve().as_posix(),
            "circuit_path": circuit_path.resolve().as_posix(), "circuit_before": before,
            "circuit_before_bytes": base64.b64encode(circuit_path.read_bytes()).decode("ascii"), "entries": [entry]}
    plan["plan_sha256"] = observed_plan_digest(plan)
    path.parent.mkdir(parents=True)
    path.write_bytes(json_bytes(plan))
    candidate.recovery_plan_path, candidate.recovery_position = path, 0
    candidate.circuit_path, candidate.before_circuit = circuit_path, before
    return path, plan


@pytest.mark.parametrize("kind,code", [("accepted", 0), ("accepted", 1), ("unknown", 1), ("t30", 0), ("deterministic", 1)])
def test_v3_journal_preserves_process_facts_and_supports_acceptance_and_rejection(tmp_path, monkeypatch, kind, code):
    from acoustic_orchestrator.pipeline.persistence_journal import read_journal, verify_repaired_outcome

    _, layout, paths, outcome_path = observed_pending(tmp_path, monkeypatch, kind=kind, code=code)
    candidate = inspect(layout, paths)
    prepare_batch_fixture(candidate, layout)
    before = {p: p.read_bytes() for p in (outcome_path, candidate.chain_path, candidate.circuit_path, layout["render_index_path"])}
    journal = candidate.prepare(layout["render_index_path"])
    assert journal == read_journal(outcome_path)
    assert journal["schema_version"] == 3 and journal["action"] == "evaluate_observed_attempt"
    assert journal["original_error"] is None and len(journal["preimages"]) == 4
    assert journal["targets"]["outcome"]["exit_code"] == code
    assert journal["targets"]["outcome"]["error"] == candidate.before_outcome["error"]
    assert "evaluated_at" in journal["targets"]["outcome"]
    assert before == {p: p.read_bytes() for p in before}
    with pytest.raises(ValueError):
        verify_repaired_outcome(read_json_object(outcome_path), outcome_path)


@pytest.mark.parametrize("damage", ["version", "action", "preimage", "facts", "chain", "resources"])
def test_v3_journal_rejects_unknown_format_or_changed_evidence(tmp_path, monkeypatch, damage):
    from acoustic_orchestrator.pipeline.persistence_journal import read_journal, journal_path, plan_digest

    _, layout, paths, outcome_path = observed_pending(tmp_path, monkeypatch)
    candidate = inspect(layout, paths)
    prepare_batch_fixture(candidate, layout)
    journal = candidate.prepare(layout["render_index_path"])
    path = journal_path(outcome_path)
    if damage == "preimage":
        (path.parent / "outcome.before.json").write_text("{}")
    elif damage == "resources":
        (path.parent / "raven_resources.json").write_text("{}")
    else:
        if damage == "version":
            journal["schema_version"] = 4
        elif damage == "action":
            journal["action"] = "unknown"
        elif damage == "facts":
            journal["targets"]["outcome"]["exit_code"] = 0
        else:
            journal["targets"]["chain"]["policy"]["max_attempts"] += 1
        journal["plan_sha256"] = plan_digest(journal)
        path.write_text(json.dumps(journal))
    with pytest.raises((ValueError, RuntimeError)):
        read_journal(outcome_path)


def candidates_for(layout):
    records = load_variant_index(layout["render_index_path"])
    return [inspect(layout, records[record["variant_id"]])
            for record in (read_json_object(p) for p in layout["render_attempts_dir"].glob("*/outcome.json"))]


def test_batch_plan_records_administrative_order_and_can_resume_every_circuit_prefix(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from acoustic_orchestrator.pipeline.observed_recovery import inspect_observed_recovery
    from acoustic_orchestrator.pipeline.persistence_journal import read_journal
    from acoustic_orchestrator.pipeline.static_plan import static_writer_lock

    _, layout, _, _ = observed_pending(tmp_path, monkeypatch, kind="mixed", scenes=2, limit=1)
    now = datetime.now(UTC)
    for path in layout["render_attempts_dir"].glob("*/outcome.json"):
        record = read_json_object(path)
        record["started_at"] = (now - timedelta(seconds=5)).isoformat()
        record["finished_at"] = (now - timedelta(seconds=3 if record["scene_id"].endswith("0001") else 2)).isoformat()
        path.write_text(json.dumps(record))
    before = snapshot(layout["run_root"])
    candidates = candidates_for(layout)
    batch = inspect_observed_recovery(layout, candidates)
    assert before == snapshot(layout["run_root"])
    assert batch.plan["order"] == "administrative_recovery_finished_at_then_uuid"
    assert [e["resolution"]["classification"] for e in batch.plan["entries"]] == ["unknown_process_failure", "accepted"]
    assert all(e["circuit_after"]["open"] for e in batch.plan["entries"])
    assert batch.plan["entries"][-1]["circuit_after"]["consecutive_failures"] == 0  # cutoff remains latched
    with static_writer_lock(layout) as lock:
        batch.prepare(lock=lock)
        for entry in batch.plan["entries"]:
            Path(batch.plan["circuit_path"]).write_text(json.dumps(entry["circuit_after"]))
            resumed = inspect_observed_recovery(layout)
            assert resumed.path == batch.path and resumed.plan == batch.plan
        for position in range(2):
            candidate = batch.candidate(position, load_variant_index(layout["render_index_path"]))
            assert candidate.chain["state"] == "circuit_open"
            journal = candidate.prepare(layout["render_index_path"])
            assert journal == read_journal(candidate.outcome_path)
            assert journal["recovery_position"] == position
    final = read_json_object(Path(batch.plan["circuit_path"]))
    assert len(final["resolutions"]) == len({r["attempt_id"] for r in final["resolutions"]}) == 2


@pytest.mark.parametrize("existing", ["consistent", "contradictory", "duplicate"])
def test_batch_plan_preserves_existing_resolution_or_blocks_contradictions(tmp_path, monkeypatch, existing):
    from acoustic_orchestrator.pipeline.observed_recovery import inspect_observed_recovery

    _, layout, paths, _ = observed_pending(tmp_path, monkeypatch)
    candidate = inspect(layout, paths)
    circuit_path = layout["render_index_path"].parent / "matlab_process_circuit.json"
    circuit = read_json_object(circuit_path)
    circuit["resolutions"].append(candidate.resolution.copy())
    if existing == "contradictory":
        circuit["resolutions"][0]["classification"] = "unknown_process_failure"
    elif existing == "duplicate":
        circuit["resolutions"].append(candidate.resolution.copy())
    circuit_path.write_text(json.dumps(circuit))
    before = snapshot(layout["run_root"])
    if existing == "consistent":
        batch = inspect_observed_recovery(layout, [candidate])
        assert batch.plan["entries"][0]["circuit_after"] == circuit
    else:
        with pytest.raises(RuntimeError):
            inspect_observed_recovery(layout, [candidate])
    assert before == snapshot(layout["run_root"])


@pytest.mark.parametrize("kind,code", [("accepted", 0), ("accepted", 1), ("unknown", 1), ("t30", 0), ("deterministic", 1)])
def test_batch_publication_closes_observed_result_without_reinvocation(tmp_path, monkeypatch, kind, code):
    from acoustic_orchestrator.pipeline.observed_recovery import inspect_observed_recovery
    from acoustic_orchestrator.pipeline.persistence_journal import verify_repaired_outcome
    from acoustic_orchestrator.pipeline.output_index import should_resume_variant
    from acoustic_orchestrator.pipeline.static_plan import static_writer_lock

    config_path, layout, paths, outcome_path = observed_pending(tmp_path, monkeypatch, kind=kind, code=code)
    config = __import__("acoustic_orchestrator.config.loader", fromlist=["load_config"]).load_config(config_path)
    candidate = inspect(layout, paths)
    batch = inspect_observed_recovery(layout, [candidate], config=config)
    protected = {p: value for p, value in snapshot(layout["run_root"]).items()
                 if p.name in {"input_manifest.json", "completion.json", "failure.json"} or p.suffix == ".wav"}
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", lambda *a, **kw: pytest.fail("no reinvocation"))
    with static_writer_lock(layout) as lock:
        assert batch.publish(config, layout, lock=lock) == [outcome_path.parent.name]
        assert batch.publish(config, layout, lock=lock) == []
    verify_repaired_outcome(read_json_object(outcome_path), outcome_path)
    assert should_resume_variant(load_variant_index(layout["render_index_path"]), paths, metadata_required=True) == (kind == "accepted")
    if kind == "accepted":
        from acoustic_orchestrator.pipeline.clarity_handoff import _revalidate_render_input
        assert "render_history_not_accepted" not in _revalidate_render_input(config, load_variant_index(layout["render_index_path"])[paths["variant_id"]])
    for p, value in protected.items():
        assert (p.read_bytes(), p.stat().st_mtime_ns) == value


@pytest.mark.parametrize("boundary", ["plan", "journal", "pending", "index", "circuit", "outcome", "chain", "journal_complete", "batch_complete"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_observed_recovery_resumes_every_publication_boundary(tmp_path, monkeypatch, boundary, when):
    from acoustic_orchestrator.config.loader import load_config
    from acoustic_orchestrator.pipeline import observed_recovery as recovery, persistence_recovery, output_index
    from acoustic_orchestrator.pipeline.persistence_journal import verify_repaired_outcome
    from acoustic_orchestrator.pipeline.static_plan import static_writer_lock
    from acoustic_orchestrator.pipeline.output_index import should_resume_variant

    config_path, layout, paths, outcome_path = observed_pending(tmp_path, monkeypatch)
    config = load_config(config_path)
    candidate = inspect(layout, paths)
    batch = recovery.inspect_observed_recovery(layout, [candidate])
    actual_write, actual_upsert = recovery.atomic_write, output_index.upsert_variant
    fired = []

    def matches(path, data):
        record = json.loads(data)
        return ((boundary == "plan" and path == batch.path)
                or (boundary == "journal" and path.name == "record.json" and record.get("state") == "pending")
                or (boundary == "pending" and path == outcome_path and record["commit_status"] == "pending")
                or (boundary == "outcome" and path == outcome_path and record["commit_status"] == "committed")
                or (boundary == "circuit" and path.name == "matlab_process_circuit.json")
                or (boundary == "chain" and path == candidate.chain_path)
                or (boundary == "journal_complete" and path.name == "record.json" and record.get("state") == "complete")
                or (boundary == "batch_complete" and path.name == "state.json" and record["state"] == "complete"))

    def interrupt_if(selected, phase):
        if selected and when == phase and not fired:
            fired.append(True)
            raise PermissionError("injected observed publication interruption")

    def write(path, data):
        selected = matches(path, data)
        interrupt_if(selected, "before")
        actual_write(path, data)
        interrupt_if(selected, "after")

    def upsert(*args, **kwargs):
        interrupt_if(boundary == "index", "before")
        actual_upsert(*args, **kwargs)
        interrupt_if(boundary == "index", "after")

    with static_writer_lock(layout) as lock:
        with monkeypatch.context() as patch:
            patch.setattr(recovery, "atomic_write", write)
            patch.setattr(persistence_recovery, "atomic_write", write)
            patch.setattr(output_index, "upsert_variant", upsert)
            with pytest.raises(PermissionError):
                batch.publish(config, layout, lock=lock)
        assert fired
        if not (boundary == "batch_complete" and when == "after"):
            assert not should_resume_variant(load_variant_index(layout["render_index_path"]), paths, metadata_required=True)
        resumed = recovery.inspect_observed_recovery(layout) if batch.path.exists() else batch
        if resumed is not None:
            assert resumed.path == batch.path and resumed.plan == batch.plan
            resumed.publish(config, layout, lock=lock)
        verify_repaired_outcome(read_json_object(outcome_path), outcome_path)
        assert should_resume_variant(load_variant_index(layout["render_index_path"]), paths, metadata_required=True)


def test_publication_finishes_own_open_circuit_plan_but_never_reopens_it(tmp_path, monkeypatch):
    from acoustic_orchestrator.config.loader import load_config
    from acoustic_orchestrator.pipeline import observed_recovery as recovery
    from acoustic_orchestrator.pipeline.static_plan import static_writer_lock

    config_path, layout, _, _ = observed_pending(tmp_path, monkeypatch, kind="unknown", scenes=2, limit=1)
    config = load_config(config_path)
    batch = recovery.inspect_observed_recovery(layout, candidates_for(layout))
    real_write = recovery.atomic_write
    fired = []

    def write(path, data):
        real_write(path, data)
        if path.name == "matlab_process_circuit.json" and not fired:
            fired.append(True)
            raise PermissionError("interrupted after cutoff")

    with static_writer_lock(layout) as lock:
        with monkeypatch.context() as patch:
            patch.setattr(recovery, "atomic_write", write)
            with pytest.raises(PermissionError):
                batch.publish(config, layout, lock=lock)
        assert read_json_object(Path(batch.plan["circuit_path"]))["open"]
        resumed = recovery.inspect_observed_recovery(layout)
        assert len(resumed.publish(config, layout, lock=lock)) == 2
    circuit = read_json_object(Path(batch.plan["circuit_path"]))
    assert circuit["open"] and circuit["consecutive_failures"] == 2
    assert len(circuit["resolutions"]) == 2


@pytest.mark.parametrize("kind,code", [("accepted", 0), ("accepted", 1), ("unknown", 1), ("t30", 0)])
def test_normal_relaunch_repairs_observed_attempt_and_counts_only_new_work(tmp_path, monkeypatch, kind, code):
    from acoustic_orchestrator.pipeline.persistence_journal import read_journal

    config_path, layout, paths, outcome_path = observed_pending(tmp_path, monkeypatch, kind=kind, code=code)
    original = read_json_object(outcome_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        _write_valid_raven_metadata(process_manifest(command))
        publish_process_receipt(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    _, summary = render_pipeline.render_static_scenes(config_path)
    assert summary["persistence_repairs"] == 1
    assert summary["matlab_invocations"] == len(calls)
    assert summary["recovered_matlab_processes"] == 0
    assert read_journal(outcome_path)["state"] == "complete"
    repaired = read_json_object(outcome_path)
    assert repaired["schema_version"] == 3 and repaired["error"] == original["error"] and repaired["exit_code"] == code
    if kind == "accepted":
        assert not calls and summary["resumed_variants"] == 1
        assert summary["resumed_recovered_variants"] == bool(code)
    elif kind == "unknown":
        assert len(calls) == summary["process_retries"] == 1
        latest = max((read_json_object(p) for p in layout["render_attempts_dir"].glob("*/outcome.json")), key=lambda r: r["started_at"])
        assert latest["attempt_ordinal"] == 2 and latest["chain_id"] == original["chain_id"]
    else:
        assert (outcome_path.parent / "acoustic_continuation/transaction.json").exists()
        assert summary["process_retries"] == 0
    _, again = render_pipeline.render_static_scenes(config_path)
    assert again["persistence_repairs"] == again["matlab_invocations"] == 0


@pytest.mark.parametrize("blocker", ["other_scene", "unknown_process", "ancestry", "private_lock", "writer_lock", "circuit"])
def test_normal_global_conflict_precedes_all_observed_repair_writes(tmp_path, monkeypatch, blocker):
    from uuid import uuid4

    config_path, layout, paths, outcome_path = observed_pending(tmp_path, monkeypatch)
    candidate = inspect(layout, paths)
    if blocker == "other_scene":
        path = layout["scene_manifest_dir"] / "foreign_scene.json"
        path.write_text("{}")
    elif blocker in {"unknown_process", "ancestry"}:
        record = read_json_object(candidate.chain_path)
        path = candidate.chain_path.parent.parent / uuid4().hex / "chain.json"
        path.parent.mkdir()
        record["chain_id"] = path.parent.name
        if blocker == "unknown_process":
            record["manifest_path"] = str(path.parent / "unknown.json")
            record["attempts"][-1]["state"] = "reserved"
        path.write_text(json.dumps(record))
    elif blocker == "private_lock":
        runtime = read_json_object(Path(paths["runtime_manifest_path"]))
        (Path(runtime["base_rpf_file"]).parent / "writer.lock").write_text("unknown owner")
    elif blocker == "writer_lock":
        (layout["render_index_path"].parent / "static_writer.lock").write_text("unknown owner")
    else:
        path = layout["render_index_path"].parent / "matlab_process_circuit.json"
        record = read_json_object(path)
        record["open"] = True
        path.write_text(json.dumps(record))
    before = snapshot(layout["run_root"])
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", lambda *a, **kw: pytest.fail("no dispatch"))
    with pytest.raises((RuntimeError, ValueError, OSError)):
        render_pipeline.render_static_scenes(config_path)
    assert before == snapshot(layout["run_root"])
    assert not (outcome_path.parent / "persistence_repair/record.json").exists()


def test_mixed_legacy_repair_observed_evaluation_t30_and_pending_work(tmp_path, monkeypatch):
    from threading import Barrier, Event
    from uuid import uuid4
    from acoustic_orchestrator.pipeline.stop_control import StopControl

    config_path, layout = configure(tmp_path.parent / ("mix_" + uuid4().hex[:8]), scenes=4, workers=3)
    (layout["render_outputs_root"] / "binaural_hrtf").mkdir(parents=True, exist_ok=True)
    control = StopControl()
    barrier = Barrier(3, timeout=20)
    old_resolved, acoustic_deferred = Event(), Event()
    real_resolve = render_pipeline._RenderCoordinator.resolve
    real_complete = render_pipeline._CanonicalStage.complete
    real_upsert = render_pipeline.upsert_variant

    def upsert(index, records, record):
        if record["scene_id"].endswith("0001") and record.get("decision") == "continue":
            raise PermissionError("old acceptance publication denied")
        return real_upsert(index, records, record)

    def resolve(coordinator, result, **kwargs):
        scene_id = result["task"]["variant_paths"]["scene_id"]
        if scene_id.endswith("0002"):
            raise KeyboardInterrupt()
        outcome = real_resolve(coordinator, result, **kwargs)
        if scene_id.endswith("0001"):
            control.request()
            old_resolved.set()
        return outcome

    def complete(stage, scene, result, **kwargs):
        outcome = real_complete(stage, scene, result, **kwargs)
        if scene.scene_id.endswith("0003"):
            acoustic_deferred.set()
        return outcome

    def first_run(command, **kwargs):
        manifest = process_manifest(command)
        scene_id = manifest["scene_id"]
        barrier.wait()
        if scene_id.endswith("0003"):
            assert old_resolved.wait(20)
        elif scene_id.endswith("0002"):
            assert acoustic_deferred.wait(20)
        _write_valid_raven_metadata(manifest, [0.5] * 9 if scene_id.endswith("0003") else None)
        publish_process_receipt(command)
        if scene_id.endswith("0002"):
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    with monkeypatch.context() as patch:
        patch.setattr(render_pipeline, "upsert_variant", upsert)
        patch.setattr(render_pipeline._RenderCoordinator, "resolve", resolve)
        patch.setattr(render_pipeline._CanonicalStage, "complete", complete)
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", first_run)
        with pytest.raises(KeyboardInterrupt):
            render_pipeline.render_static_scenes(config_path, stop=control)
    outcomes = {r["scene_id"]: r for r in (read_json_object(p) for p in layout["render_attempts_dir"].glob("*/outcome.json"))}
    assert len(outcomes) == 3
    assert outcomes["scene_static_0001"]["retry_classification"] == "storage_failure"
    assert outcomes["scene_static_0002"]["commit_status"] == "pending"
    assert outcomes["scene_static_0003"]["retry_classification"] == "acoustic_rejection"
    calls = []

    def remaining(command, **kwargs):
        manifest = process_manifest(command)
        assert manifest["scene_id"] not in {"scene_static_0001", "scene_static_0002"}
        calls.append(manifest["scene_id"])
        _write_valid_raven_metadata(manifest)
        publish_process_receipt(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", remaining)
    _, summary = render_pipeline.render_static_scenes(config_path)
    assert summary["persistence_repairs"] == summary["resumed_variants"] == 2
    assert summary["matlab_invocations"] == len(calls) >= 1
    assert "scene_static_0004" in calls
    assert summary["process_retries"] == summary["failed_matlab_processes"] == summary["recovered_matlab_processes"] == 0
    assert summary["resumed_recovered_variants"] == 1
    journals = [read_json_object(p) for p in layout["render_attempts_dir"].glob("*/persistence_repair/record.json")]
    assert sorted(j["schema_version"] for j in journals) == [1, 3]
    _, again = render_pipeline.render_static_scenes(config_path)
    assert again["persistence_repairs"] == again["matlab_invocations"] == 0


def test_repaired_deterministic_rejection_reports_repair_without_dispatch(tmp_path, monkeypatch):
    config_path, layout, _, outcome_path = observed_pending(tmp_path, monkeypatch, kind="deterministic")
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", lambda *a, **kw: pytest.fail("deterministic failure must not dispatch"))
    with pytest.raises(render_pipeline.RenderStaticRunError) as caught:
        render_pipeline.render_static_scenes(config_path)
    assert caught.value.summary["persistence_repairs"] == 1
    assert caught.value.summary["matlab_invocations"] == caught.value.summary["failed_matlab_processes"] == 0
    assert read_json_object(outcome_path)["retry_classification"] == "deterministic_failure"
