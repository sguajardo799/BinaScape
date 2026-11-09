import json
from pathlib import Path
import subprocess

import pytest

from acoustic_orchestrator.pipeline import process_retries
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes
from test_persistence_recovery import inspect
from test_render_acceptance import publish_process_receipt
from test_render_diagnostics_integration import successful_process
from test_render_resilience import configure


def failed_finalization(tmp_path, monkeypatch, *, code=0, classified=False, scenes=1):
    config, layout = configure(tmp_path, scenes=scenes, attempts=1)
    real_save = process_retries.RetryChain.save

    def save(chain):
        if chain.record["state"] == "not_retryable":
            raise PermissionError("chain publication denied")
        return real_save(chain)

    real_attempt_save = process_retries.RenderAttempt.save

    def attempt_save(attempt):
        if not classified and attempt.record.get("retry_classification") == "accepted":
            raise PermissionError("classification publication denied")
        return real_attempt_save(attempt)

    def run(command, **kwargs):
        result = successful_process(command, **kwargs)
        publish_process_receipt(command)
        if code:
            raise subprocess.CalledProcessError(code, command)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(process_retries.RetryChain, "save", save)
        patch.setattr(process_retries.RenderAttempt, "save", attempt_save)
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
        with pytest.raises(RenderStaticRunError):
            render_static_scenes(config)
    outcome_path = next(layout["render_attempts_dir"].glob("*/outcome.json"))
    outcome = read_json_object(outcome_path)
    paths = load_variant_index(layout["render_index_path"])[outcome["variant_id"]]
    assert outcome["commit_status"] == "committed"
    assert "persistence_error" not in outcome
    return config, layout, paths, outcome_path


@pytest.mark.parametrize("code", [0, 7])
@pytest.mark.parametrize("classified", [False, True])
def test_finalization_candidate_revalidates_without_writes(tmp_path, monkeypatch, code, classified):
    _, layout, paths, outcome_path = failed_finalization(tmp_path, monkeypatch, code=code, classified=classified)
    root = layout["render_attempts_dir"].parent
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    repair = inspect(layout, paths)
    assert repair.action == "finalize_chain"
    assert repair.outcome["exit_code"] == code
    assert repair.outcome["error"] == read_json_object(outcome_path)["error"]
    assert repair.outcome.get("warning") == read_json_object(outcome_path).get("warning")
    assert repair.chain["state"] == "not_retryable"
    assert repair.chain["attempts"][-1]["state"] == "resolved"
    assert repair.chain["policy"]["max_attempts"] == len(repair.chain["attempts"]) == 1
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}


@pytest.mark.parametrize("damage", ["receipt", "hash", "t30", "identity", "pending", "termination", "classification"])
def test_finalization_rejects_unaccredited_evidence(tmp_path, monkeypatch, damage):
    _, layout, paths, outcome_path = failed_finalization(tmp_path, monkeypatch)
    source = read_json_object(outcome_path)
    if damage == "receipt":
        Path(source["completion_path"]).unlink()
    elif damage == "hash":
        Path(source["manifest_snapshot_path"]).write_text("{}")
    elif damage == "t30":
        from test_generate_manifests import _write_valid_raven_metadata
        from render_fixtures import write_completion_for_attempt
        from types import SimpleNamespace
        _write_valid_raven_metadata(read_json_object(Path(paths["runtime_manifest_path"])), [0.5] * 9)
        write_completion_for_attempt(SimpleNamespace(record=source, expected_artifacts=source["expected_artifacts"],
                                                    completion_path=Path(source["completion_path"])))
    else:
        key, value = {"identity": ("chain_id", "foreign"), "pending": ("commit_status", "pending"),
                      "termination": ("termination_observed", False),
                      "classification": ("retry_classification", "storage_failure")}[damage]
        source[key] = value
        outcome_path.write_text(json.dumps(source))
    with pytest.raises((OSError, ValueError, RuntimeError)):
        inspect(layout, paths)


@pytest.mark.parametrize("damage", ["missing", "duplicate", "classification", "process_failed", "open"])
def test_finalization_requires_unique_accepted_circuit_resolution(tmp_path, monkeypatch, damage):
    _, layout, paths, _ = failed_finalization(tmp_path, monkeypatch)
    circuit_path = layout["render_index_path"].parent / "matlab_process_circuit.json"
    circuit = read_json_object(circuit_path)
    if damage == "missing":
        circuit["resolutions"] = []
    elif damage == "duplicate":
        circuit["resolutions"].append(circuit["resolutions"][0].copy())
    elif damage == "open":
        circuit["open"] = True
    else:
        circuit["resolutions"][0][damage] = "storage_failure" if damage == "classification" else True
    circuit_path.write_text(json.dumps(circuit))
    with pytest.raises((OSError, ValueError, RuntimeError)):
        inspect(layout, paths)


def test_circuit_capture_is_read_only_and_allows_only_append_only_history(tmp_path, monkeypatch):
    from copy import deepcopy
    from acoustic_orchestrator.pipeline.persistence_journal import verify_circuit_prefix

    _, layout, paths, _ = failed_finalization(tmp_path, monkeypatch)
    monkeypatch.setattr(process_retries.BatchCircuit, "resolve", lambda *a, **kw: pytest.fail("must not recount"))
    circuit_path = layout["render_index_path"].parent / "matlab_process_circuit.json"
    before = circuit_path.read_bytes(), circuit_path.stat().st_mtime_ns
    repair = inspect(layout, paths)
    assert repair.circuit_path == circuit_path
    assert before == (circuit_path.read_bytes(), circuit_path.stat().st_mtime_ns)
    current = deepcopy(repair.before_circuit)
    current["resolutions"].append({"attempt_id": "later", "classification": "accepted", "process_failed": False})
    assert verify_circuit_prefix(repair.before_circuit, current, repair.outcome["attempt_id"]) == 0
    current["resolutions"][0]["classification"] = "storage_failure"
    with pytest.raises(ValueError, match="prefix"):
        verify_circuit_prefix(repair.before_circuit, current, repair.outcome["attempt_id"])


@pytest.mark.parametrize("code", [0, 7])
def test_v2_journal_preserves_four_preimages_and_unknown_original_error(tmp_path, monkeypatch, code):
    from acoustic_orchestrator.pipeline.persistence_journal import read_journal

    _, layout, paths, outcome_path = failed_finalization(tmp_path, monkeypatch, code=code)
    repair = inspect(layout, paths)
    before = {p: p.read_bytes() for p in (outcome_path, repair.chain_path, repair.circuit_path)}
    journal = repair.prepare(layout["render_index_path"])
    assert journal == read_journal(outcome_path)
    assert journal["schema_version"] == 2 and journal["action"] == "finalize_chain"
    assert journal["original_error"] is None
    assert len(journal["preimages"]) == 4
    assert (outcome_path.parent / "persistence_repair/circuit.before.json").read_bytes() == before[repair.circuit_path]
    assert before == {p: p.read_bytes() for p in before}
    assert journal["targets"]["outcome"]["exit_code"] == code
    assert journal["targets"]["outcome"]["error"] == repair.before_outcome["error"]


@pytest.mark.parametrize("damage", ["version", "action", "preimage", "circuit_preimage", "process", "chain"])
def test_v2_reader_rejects_unknown_or_tampered_plans(tmp_path, monkeypatch, damage):
    from acoustic_orchestrator.pipeline.persistence_journal import read_journal, plan_digest

    _, layout, paths, outcome_path = failed_finalization(tmp_path, monkeypatch)
    inspect(layout, paths).prepare(layout["render_index_path"])
    path = outcome_path.parent / "persistence_repair/record.json"
    journal = read_json_object(path)
    if damage.endswith("preimage"):
        filename = "circuit.before.json" if damage == "circuit_preimage" else "outcome.before.json"
        (path.parent / filename).write_text("{}")
    else:
        if damage == "version":
            journal["schema_version"] = 3
        elif damage == "action":
            journal["action"] = "unknown"
        elif damage == "process":
            journal["targets"]["outcome"]["exit_code"] = 99
        else:
            journal["targets"]["chain"]["policy"]["max_attempts"] = 99
        journal["plan_sha256"] = plan_digest(journal)
        path.write_text(json.dumps(journal))
    with pytest.raises((ValueError, RuntimeError)):
        read_journal(outcome_path)


@pytest.mark.parametrize("classified", [False, True])
@pytest.mark.parametrize("boundary", ["journal", "pending", "index", "outcome", "chain", "complete"])
def test_finalization_resumes_same_journal_at_every_boundary(tmp_path, monkeypatch, classified, boundary):
    from acoustic_orchestrator.pipeline import persistence_recovery as recovery, output_index
    from acoustic_orchestrator.pipeline.persistence_journal import read_journal, verify_repaired_outcome
    from test_render_resilience import evidence

    _, layout, paths, outcome_path = failed_finalization(tmp_path, monkeypatch, classified=classified)
    repair = inspect(layout, paths)
    protected = evidence(layout)
    for root in (layout["render_attempts_dir"], layout["render_attempts_dir"].parent / "raven_resources"):
        for path in root.rglob("*"):
            if path.is_file() and (root.name == "raven_resources" or path.name in {"completion.json", "input_manifest.json"}):
                protected[path] = (path.read_bytes(), path.stat().st_mtime_ns)
    circuit_before = repair.circuit_path.read_bytes(), repair.circuit_path.stat().st_mtime_ns
    monkeypatch.setattr(process_retries.BatchCircuit, "resolve", lambda *a, **kw: pytest.fail("must not recount"))
    real_write, real_upsert = recovery.atomic_write, output_index.upsert_variant
    fired = []

    def write(path, data):
        real_write(path, data)
        record = json.loads(data)
        matches = ((boundary == "journal" and path.name == "record.json" and record["state"] == "pending")
                   or (boundary == "pending" and path == outcome_path and record["commit_status"] == "pending")
                   or (boundary == "outcome" and path == outcome_path and record["commit_status"] == "committed")
                   or (boundary == "chain" and path == repair.chain_path)
                   or (boundary == "complete" and path.name == "record.json" and record["state"] == "complete"))
        if matches and not fired:
            fired.append(True)
            raise PermissionError("interrupted finalization")

    def upsert(*args, **kwargs):
        real_upsert(*args, **kwargs)
        if boundary == "index" and not fired:
            fired.append(True)
            raise PermissionError("interrupted index")

    with monkeypatch.context() as patch:
        patch.setattr(recovery, "atomic_write", write)
        patch.setattr(output_index, "upsert_variant", upsert)
        with pytest.raises(PermissionError):
            repair.publish(layout["render_index_path"])
    assert fired
    journal = read_journal(outcome_path)
    if boundary != "complete":
        with pytest.raises(ValueError):
            verify_repaired_outcome(read_json_object(outcome_path), outcome_path)
        assert inspect(layout, paths).publish(layout["render_index_path"])
    verify_repaired_outcome(read_json_object(outcome_path), outcome_path)
    completed = read_journal(outcome_path)
    assert completed == {**journal, "state": "complete"}
    assert not repair.publish(layout["render_index_path"])
    assert circuit_before == (repair.circuit_path.read_bytes(), repair.circuit_path.stat().st_mtime_ns)
    import hashlib
    for path, (content, mtime) in protected.items():
        actual = path.read_bytes() if isinstance(content, bytes) else hashlib.sha256(path.read_bytes()).hexdigest()
        assert (actual, path.stat().st_mtime_ns) == (content, mtime)


def test_finalization_refuses_foreign_active_transition(tmp_path, monkeypatch):
    _, layout, paths, outcome_path = failed_finalization(tmp_path, monkeypatch)
    repair = inspect(layout, paths)
    repair.prepare(layout["render_index_path"])
    record = read_json_object(outcome_path)
    record["decision"] = "abort"
    outcome_path.write_text(json.dumps(record))
    with pytest.raises(RuntimeError, match="state conflict"):
        inspect(layout, paths)


@pytest.mark.parametrize("blocker", ["unobserved", "unmapped", "ancestry", "private_lock", "writer_lock", "circuit", "other_scene"])
def test_whole_batch_veto_precedes_finalization_writes(tmp_path, monkeypatch, blocker):
    from uuid import uuid4

    config, layout, paths, outcome_path = failed_finalization(tmp_path, monkeypatch, scenes=2)
    repair = inspect(layout, paths)
    if blocker in {"unobserved", "unmapped", "ancestry"}:
        record = read_json_object(repair.chain_path)
        path = repair.chain_path.parent.parent / uuid4().hex / "chain.json"
        path.parent.mkdir()
        record["chain_id"] = path.parent.name
        if blocker != "ancestry":
            record["manifest_path"] = str(path.parent / "unmapped.json")
        if blocker == "unobserved":
            record["attempts"][-1]["state"] = "reserved"
        path.write_text(json.dumps(record))
    elif blocker == "private_lock":
        runtime = read_json_object(Path(paths["runtime_manifest_path"]))
        (Path(runtime["base_rpf_file"]).parent / "writer.lock").write_text("existing owner")
    elif blocker == "writer_lock":
        (layout["render_index_path"].parent / "static_writer.lock").write_text("existing owner")
    elif blocker == "circuit":
        record = read_json_object(repair.circuit_path)
        record["open"] = True
        repair.circuit_path.write_text(json.dumps(record))
    else:
        path = sorted(layout["scene_manifest_dir"].glob("*.json"))[1]
        record = read_json_object(path)
        record["scene_id"] = "foreign"
        path.write_text(json.dumps(record))
    root = layout["render_attempts_dir"].parent
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **kw: pytest.fail("blocked preflight must not dispatch"))
    with pytest.raises((OSError, ValueError, RuntimeError)):
        render_static_scenes(config)
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}
    assert not (outcome_path.parent / "persistence_repair/record.json").exists()


def test_normal_resume_finalizes_then_repeats_preflight_without_new_process(tmp_path, monkeypatch):
    config, layout, _, outcome_path = failed_finalization(tmp_path, monkeypatch)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **kw: pytest.fail("finalization must not dispatch"))
    monkeypatch.setattr(process_retries.BatchCircuit, "resolve", lambda *a, **kw: pytest.fail("must not recount"))
    manifests, summary = render_static_scenes(config)
    assert len(manifests) == summary["resumed_variants"] == summary["persistence_repairs"] == 1
    assert summary["matlab_invocations"] == summary["process_retries"] == 0
    assert read_json_object(outcome_path)["schema_version"] == 2


@pytest.mark.parametrize("classified", [False, True])
def test_known_chain_blocks_reuse_and_clarity_before_or_during_repair(tmp_path, monkeypatch, classified):
    from acoustic_orchestrator.config.loader import load_config
    from acoustic_orchestrator.pipeline.clarity_handoff import _revalidate_render_input
    from acoustic_orchestrator.pipeline.output_index import should_resume_variant
    from acoustic_orchestrator.pipeline.persistence_journal import atomic_write, json_bytes

    config, layout, paths, outcome_path = failed_finalization(tmp_path, monkeypatch, classified=classified)
    records = load_variant_index(layout["render_index_path"])
    assert not should_resume_variant(records, paths, metadata_required=True)
    assert "render_history_not_accepted" in _revalidate_render_input(load_config(config), records[paths["variant_id"]])
    repair = inspect(layout, paths)
    repair.prepare(layout["render_index_path"])
    atomic_write(outcome_path, json_bytes(repair.outcome))
    atomic_write(repair.chain_path, json_bytes(repair.chain))
    assert not should_resume_variant(records, paths, metadata_required=True)
    assert "render_history_not_accepted" in _revalidate_render_input(load_config(config), records[paths["variant_id"]])
    assert inspect(layout, paths).publish(layout["render_index_path"])
    records = load_variant_index(layout["render_index_path"])
    assert should_resume_variant(records, paths, metadata_required=True)
    assert "render_history_not_accepted" not in _revalidate_render_input(load_config(config), records[paths["variant_id"]])
    # A rollback reader that supports v2 outcomes but only v1 journals must fail closed.
    journal = read_json_object(outcome_path.parent / "persistence_repair/record.json")
    assert journal["schema_version"] != 1


def test_chain_label_alone_cannot_authorize_reuse(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.output_index import should_resume_variant

    _, layout, paths, _ = failed_finalization(tmp_path, monkeypatch)
    repair = inspect(layout, paths)
    repair.chain_path.write_text(json.dumps(repair.chain))
    assert not should_resume_variant(load_variant_index(layout["render_index_path"]), paths, metadata_required=True)


def test_mixed_repairs_count_each_uuid_once_and_dispatch_only_pending(tmp_path, monkeypatch):
    from threading import Barrier
    from acoustic_orchestrator.pipeline import render_pipeline
    from test_parallel_canonical_render import configure as parallel_configure
    from test_render_diagnostics_integration import process_manifest

    config, layout = parallel_configure(tmp_path, scenes=4, workers=2)
    barrier = Barrier(2, timeout=20)
    initial = []

    def run(command, **kwargs):
        initial.append(process_manifest(command)["scene_id"])
        barrier.wait()
        result = successful_process(command, **kwargs)
        publish_process_receipt(command)
        return result

    real_upsert = render_pipeline.upsert_variant
    real_save = process_retries.RenderAttempt.save

    def upsert(index, records, record):
        if record["scene_id"] == "scene_static_0001" and record.get("decision") == "continue":
            raise PermissionError("acceptance denied")
        return real_upsert(index, records, record)

    def save(attempt):
        if attempt.scene_id == "scene_static_0002" and attempt.record.get("retry_classification") == "accepted":
            raise PermissionError("classification denied")
        return real_save(attempt)

    with monkeypatch.context() as patch:
        patch.setattr(render_pipeline, "upsert_variant", upsert)
        patch.setattr(process_retries.RenderAttempt, "save", save)
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
        with pytest.raises(RenderStaticRunError):
            render_static_scenes(config)
    assert set(initial) == {"scene_static_0001", "scene_static_0002"}
    pending = []

    def remaining(command, **kwargs):
        scene = process_manifest(command)["scene_id"]
        assert scene not in initial
        pending.append(scene)
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", remaining)
    _, summary = render_static_scenes(config)
    assert summary["persistence_repairs"] == summary["resumed_variants"] == 2
    assert summary["matlab_invocations"] == len(pending) == 2
    for key in ("process_retries", "failed_matlab_processes", "recovered_matlab_processes", "variants_completed_after_retry"):
        assert summary[key] == 0
    journals = [read_json_object(p) for p in layout["render_attempts_dir"].glob("*/persistence_repair/record.json")]
    assert sorted(j["schema_version"] for j in journals) == [1, 2]
    _, again = render_static_scenes(config)
    assert again["persistence_repairs"] == again["matlab_invocations"] == 0
    assert again["resumed_variants"] == 4
    assert len(pending) == 2


@pytest.mark.parametrize("scenario", ["raven_parallel", "raven_variants", "pra_parallel"])
@pytest.mark.parametrize("classified", [False, True])
def test_backend_calls_preserve_uuid_and_evidence_across_repeated_denial(tmp_path, monkeypatch, scenario, classified):
    import yaml
    from threading import Barrier, Event
    from acoustic_orchestrator.pipeline import persistence_recovery as recovery
    from acoustic_orchestrator.pipeline.persistence_journal import read_journal
    from test_parallel_canonical_render import configure as parallel_configure
    from test_render_diagnostics_integration import process_manifest
    from test_render_resilience import evidence

    optional = scenario == "raven_variants"
    if scenario == "pra_parallel":
        from acoustic_orchestrator.config import backend_validation
        from test_pra_resilience import configure as pra_configure, fake_worker, worker_arguments
        real_version = backend_validation.importlib.metadata.version
        monkeypatch.setattr(backend_validation.importlib.metadata, "version",
                            lambda name: "0.10.1" if name == "pyroomacoustics" else real_version(name))
        config, layout = pra_configure(tmp_path, attempts=1)
        raw = yaml.safe_load(config.read_text())
        raw["execution"].update(num_simulations=3, num_workers=2)
        config.write_text(yaml.safe_dump(raw))
        manifest_for = lambda command: read_json_object(worker_arguments(command).snapshot)
        worker = fake_worker
    else:
        config, layout = parallel_configure(tmp_path, scenes=1 if optional else 3,
                                             workers=1 if optional else 2, optional=optional)
        manifest_for = process_manifest
        worker = successful_process
    barrier = Barrier(2, timeout=20)
    failure_observed = Event()
    initial = []

    def run(command, **kwargs):
        manifest = manifest_for(command)
        initial.append(manifest["scene_id"] + "__" + manifest["render"]["active_output_type"])
        if not optional:
            barrier.wait()
        result = worker(command, **kwargs)
        if scenario != "pra_parallel":
            publish_process_receipt(command)
        if not optional and manifest["scene_id"] != "scene_static_0001":
            assert failure_observed.wait(20)
        return result

    def selected(record):
        return record["output_type"] != "binaural_hrtf" if optional else record["scene_id"] == "scene_static_0001"

    real_save = process_retries.RenderAttempt.save
    real_chain_save = process_retries.RetryChain.save

    def save(attempt):
        if not classified and selected(attempt.record) and attempt.record.get("retry_classification") == "accepted":
            failure_observed.set()
            raise PermissionError("classification denied")
        return real_save(attempt)

    def chain_save(chain):
        if classified and chain.record["state"] == "not_retryable":
            outcome = read_json_object(Path(chain.record["attempts"][-1]["outcome_path"]))
            if selected(outcome):
                failure_observed.set()
                raise PermissionError("chain denied")
        return real_chain_save(chain)

    with monkeypatch.context() as patch:
        patch.setattr(process_retries.RenderAttempt, "save", save)
        patch.setattr(process_retries.RetryChain, "save", chain_save)
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
        with pytest.raises(RenderStaticRunError):
            render_static_scenes(config)
    assert len(initial) == 2
    chain_path = next(p for p in (layout["render_attempts_dir"].parent / "process_chains").glob("*/chain.json")
                      if read_json_object(p)["state"] == "running")
    outcome_path = Path(read_json_object(chain_path)["attempts"][-1]["outcome_path"])
    original = read_json_object(outcome_path)
    protected = evidence(layout)
    for path in layout["run_root"].rglob("*"):
        if path.is_file() and ("raven_resources" in path.parts or path.name in {"completion.json", "input_manifest.json"}):
            protected[path] = (path.read_bytes(), path.stat().st_mtime_ns)
    pending = []

    def remaining(command, **kwargs):
        manifest = manifest_for(command)
        identity = manifest["scene_id"] + "__" + manifest["render"]["active_output_type"]
        assert identity not in initial
        pending.append(identity)
        return worker(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", remaining)
    real_write = recovery.atomic_write

    def denied(path, data):
        if path == chain_path:
            raise PermissionError("finalization still denied")
        return real_write(path, data)

    circuit_path = layout["render_index_path"].parent / "matlab_process_circuit.json"
    circuit_before = circuit_path.read_bytes(), circuit_path.stat().st_mtime_ns
    with monkeypatch.context() as patch:
        patch.setattr(recovery, "atomic_write", denied)
        with pytest.raises(PermissionError, match="still denied"):
            render_static_scenes(config)
    assert not pending
    assert circuit_before == (circuit_path.read_bytes(), circuit_path.stat().st_mtime_ns)
    journal = read_journal(outcome_path)
    assert journal["state"] == "pending"
    _, summary = render_static_scenes(config)
    assert summary["persistence_repairs"] == 1
    assert summary["resumed_variants"] == 2
    assert summary["matlab_invocations"] == len(pending) == 1
    assert summary["process_retries"] == summary["failed_matlab_processes"] == 0
    assert read_journal(outcome_path) == {**journal, "state": "complete"}
    final = read_json_object(outcome_path)
    for key in ("attempt_id", "chain_id", "attempt_ordinal", "manifest_sha256", "input_sha256"):
        assert final[key] == original[key]
    resolutions = read_json_object(circuit_path)["resolutions"]
    assert sum(item["attempt_id"] == original["attempt_id"] for item in resolutions) == 1
    import hashlib
    for path, (content, mtime) in protected.items():
        actual = path.read_bytes() if isinstance(content, bytes) else hashlib.sha256(path.read_bytes()).hexdigest()
        assert (actual, path.stat().st_mtime_ns) == (content, mtime)


def test_cli_finalization_denial_explains_exhaustion_and_can_resume(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from acoustic_orchestrator.cli import app
    from acoustic_orchestrator.pipeline import atomic_replace as publication

    config, layout, paths, outcome_path = failed_finalization(tmp_path, monkeypatch)
    repair = inspect(layout, paths)
    original_replace = Path.replace
    calls, sleeps = [], []

    def replace(path, target):
        if Path(target) == repair.chain_path:
            calls.append(path)
            error = PermissionError(13, "denied", str(path), 5, str(target))
            error.winerror = 5
            raise error
        return original_replace(path, target)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **kw: pytest.fail("no process dispatch"))
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", replace)
        patch.setattr(publication.time, "sleep", sleeps.append)
        result = CliRunner().invoke(app, ["render-static", str(config), "--no-progress"])
    assert result.exit_code == 1
    assert len(calls) == 5 and sleeps == [0.05, 0.10, 0.20, 0.40]
    assert "PermissionError" in result.stderr and "5 attempts (0.75 s scheduled wait)" in result.stderr
    assert "chain.json.tmp" in result.stderr and "chain.json" in result.stderr
    assert f"attempt_id={outcome_path.parent.name}" in result.stderr
    assert "antivirus" not in result.output
    result = CliRunner().invoke(app, ["render-static", str(config), "--no-progress"])
    assert result.exit_code == 0, result.output
    assert "persistencias_reparadas=1" in result.stdout


def test_completed_finalization_allows_later_circuit_progress_but_not_prefix_edits(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.persistence_journal import verify_repaired_outcome

    _, layout, paths, outcome_path = failed_finalization(tmp_path, monkeypatch)
    repair = inspect(layout, paths)
    assert repair.publish(layout["render_index_path"])
    circuit = process_retries.BatchCircuit(repair.circuit_path, repair.before_circuit["limit"])
    circuit.resolve("later_attempt", "accepted", process_failed=False)
    verify_repaired_outcome(read_json_object(outcome_path), outcome_path)
    circuit.record["resolutions"][0]["process_failed"] = True
    repair.circuit_path.write_text(json.dumps(circuit.record))
    with pytest.raises(ValueError, match="prefix"):
        verify_repaired_outcome(read_json_object(outcome_path), outcome_path)


@pytest.mark.parametrize("field", ["attempt_id", "attempt_outcome_path", "completion_path", "process_status", "decision"])
def test_closed_chain_requires_coherent_index_references(tmp_path, monkeypatch, field):
    from acoustic_orchestrator.pipeline.output_index import should_resume_variant

    _, layout, paths, _ = failed_finalization(tmp_path, monkeypatch)
    assert inspect(layout, paths).publish(layout["render_index_path"])
    records = load_variant_index(layout["render_index_path"])
    assert should_resume_variant(records, paths, metadata_required=True)
    records[paths["variant_id"]][field] = "foreign"
    assert not should_resume_variant(records, paths, metadata_required=True)
