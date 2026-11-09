import json
from pathlib import Path
import subprocess

import pytest

from acoustic_orchestrator.pipeline import render_pipeline
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.persistence_recovery import inspect_persistence_repair
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes
from test_render_resilience import configure
from test_render_acceptance import publish_process_receipt
from test_render_diagnostics_integration import successful_process


def failed_publication(tmp_path, monkeypatch, *, code=0, scenes=1):
    path, layout = configure(tmp_path, scenes=scenes, attempts=1)
    upsert = render_pipeline.upsert_variant
    def deny(index, records, record):
        if record.get("decision") in {"continue", "continue_with_warning"}:
            raise PermissionError("index denied")
        return upsert(index, records, record)
    def run(command, **kwargs):
        result = successful_process(command, **kwargs)
        publish_process_receipt(command)
        if code:
            raise subprocess.CalledProcessError(code, command)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(render_pipeline, "upsert_variant", deny)
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
        with pytest.raises(RenderStaticRunError):
            render_static_scenes(path)
    outcome_path = next(layout["render_attempts_dir"].glob("*/outcome.json"))
    outcome = read_json_object(outcome_path)
    runtime = Path(outcome["manifest_path"])
    manifest = read_json_object(runtime)
    paths = {"scene_id": outcome["scene_id"], "variant_id": outcome["variant_id"], "output_type": outcome["output_type"],
             "runtime_manifest_path": runtime.as_posix(), "rendered_wav_path": manifest["render"]["output_wav_path"],
             "render_metadata_path": manifest["render"]["output_metadata_path"],
             "receiver_ir_path": load_variant_index(layout["render_index_path"])[outcome["variant_id"]]["receiver_ir_path"]}
    return path, layout, paths, outcome_path


def inspect(layout, paths):
    return inspect_persistence_repair(paths, read_json_object(Path(paths["runtime_manifest_path"])),
                                      load_variant_index(layout["render_index_path"]),
                                      layout["render_attempts_dir"].parent / "process_chains",
                                      metadata_required=True, canonical=True)


@pytest.mark.parametrize("code", [0, 7])
def test_inspection_is_read_only_and_preserves_process(tmp_path, monkeypatch, code):
    _, layout, paths, outcome_path = failed_publication(tmp_path, monkeypatch, code=code)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in layout["render_attempts_dir"].parent.rglob("*") if p.is_file()}
    candidate = inspect(layout, paths)
    assert candidate.outcome["exit_code"] == code
    assert candidate.outcome["decision"] == ("continue_with_warning" if code else "continue")
    assert candidate.outcome["error"] == read_json_object(outcome_path)["error"]
    assert candidate.outcome["attempt_id"] == candidate.before_outcome["attempt_id"]
    assert candidate.chain["attempts"][-1]["ordinal"] == 1
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}


@pytest.mark.parametrize("damage", ["missing_receipt", "foreign_receipt", "snapshot", "process", "index", "index_path", "t30"])
def test_inspection_rejects_incomplete_or_foreign_evidence(tmp_path, monkeypatch, damage):
    _, layout, paths, outcome_path = failed_publication(tmp_path, monkeypatch)
    outcome = read_json_object(outcome_path)
    if damage == "missing_receipt":
        Path(outcome["completion_path"]).unlink()
    elif damage == "foreign_receipt":
        receipt = read_json_object(Path(outcome["completion_path"]))
        receipt["attempt_id"] = "other"
        Path(outcome["completion_path"]).write_text(json.dumps(receipt))
    elif damage == "snapshot":
        Path(outcome["manifest_snapshot_path"]).write_text("{}")
    elif damage == "process":
        outcome["termination_observed"] = False
        outcome_path.write_text(json.dumps(outcome))
    elif damage in {"index", "index_path"}:
        records = load_variant_index(layout["render_index_path"])
        records[paths["variant_id"]]["attempt_id" if damage == "index" else "rendered_wav_path"] = "other"
        layout["render_index_path"].write_text("\n".join(json.dumps(r) for r in records.values()))
    else:
        from test_generate_manifests import _write_valid_raven_metadata
        from render_fixtures import write_completion_for_attempt
        from types import SimpleNamespace
        _write_valid_raven_metadata(read_json_object(Path(paths["runtime_manifest_path"])), [0.5] * 9)
        write_completion_for_attempt(SimpleNamespace(record=outcome, expected_artifacts=outcome["expected_artifacts"],
                                                    completion_path=Path(outcome["completion_path"])))
    with pytest.raises((ValueError, RuntimeError, OSError)):
        inspect(layout, paths)


def test_journal_precedes_active_writes_and_versions_only_repaired_outcome(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.persistence_journal import read_journal
    _, layout, paths, outcome_path = failed_publication(tmp_path, monkeypatch)
    repair = inspect(layout, paths)
    before = {p: p.read_bytes() for p in (outcome_path, repair.chain_path, layout["render_index_path"])}
    journal = repair.prepare(layout["render_index_path"])
    assert journal == read_journal(outcome_path)
    assert journal["state"] == "pending"
    assert journal["original_error"]["type"] == "PermissionError"
    assert journal["targets"]["outcome"]["schema_version"] == 2
    assert read_json_object(outcome_path)["schema_version"] == 1
    assert read_json_object(Path(repair.outcome["completion_path"]))["schema_version"] == 1
    assert before == {p: p.read_bytes() for p in before}
    assert (outcome_path.parent / "persistence_repair/outcome.before.json").read_bytes() == before[outcome_path]
    # Both previous reader gates required schema_version == 1, irrespective of
    # committed status or a completed index. V2 therefore fails closed on rollback.
    for status in ("pending", "committed"):
        previous_reader_input = {**journal["targets"]["outcome"], "commit_status": status}
        assert not (type(previous_reader_input["schema_version"]) is int
                    and previous_reader_input["schema_version"] == 1)
    assert repair.prepare(layout["render_index_path"]) == journal


@pytest.mark.parametrize("boundary", ["journal", "pending", "index", "outcome", "chain", "complete"])
def test_repair_can_resume_after_every_publication_boundary(tmp_path, monkeypatch, boundary):
    from acoustic_orchestrator.pipeline import persistence_recovery as recovery, output_index
    from acoustic_orchestrator.pipeline.persistence_journal import verify_repaired_outcome
    _, layout, paths, outcome_path = failed_publication(tmp_path, monkeypatch)
    repair = inspect(layout, paths)
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
            raise PermissionError("simulated interruption after write")
    def upsert(*args, **kwargs):
        real_upsert(*args, **kwargs)
        if boundary == "index" and not fired:
            fired.append(True)
            raise PermissionError("simulated interruption after index")
    with monkeypatch.context() as patch:
        patch.setattr(recovery, "atomic_write", write)
        patch.setattr(output_index, "upsert_variant", upsert)
        with pytest.raises(PermissionError):
            repair.publish(layout["render_index_path"])
    assert fired
    next_repair = inspect(layout, paths)
    if next_repair is not None:
        assert next_repair.publish(layout["render_index_path"])
    verify_repaired_outcome(read_json_object(outcome_path), outcome_path)
    final = {p: p.read_bytes() for p in outcome_path.parent.rglob("*") if p.is_file()}
    assert not repair.publish(layout["render_index_path"])
    assert final == {p: p.read_bytes() for p in final}


@pytest.mark.parametrize("damage", ["journal", "preimage", "state", "shape"])
def test_corrupt_journal_preimage_or_unknown_transition_blocks_repair(tmp_path, monkeypatch, damage):
    _, layout, paths, outcome_path = failed_publication(tmp_path, monkeypatch)
    repair = inspect(layout, paths)
    repair.prepare(layout["render_index_path"])
    if damage == "journal":
        path = outcome_path.parent / "persistence_repair/record.json"
        record = read_json_object(path)
        record["targets"]["outcome"]["exit_code"] = 19
        path.write_text(json.dumps(record))
    elif damage == "preimage":
        (outcome_path.parent / "persistence_repair/outcome.before.json").write_text("{}")
    elif damage == "state":
        record = read_json_object(outcome_path)
        record["decision"] = "continue"
        outcome_path.write_text(json.dumps(record))
    else:
        from acoustic_orchestrator.pipeline.persistence_journal import plan_digest
        path = outcome_path.parent / "persistence_repair/record.json"
        record = read_json_object(path)
        record["preimages"] = {}
        record["plan_sha256"] = plan_digest(record)
        path.write_text(json.dumps(record))
    before = outcome_path.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        inspect(layout, paths)
    assert outcome_path.read_bytes() == before


@pytest.mark.parametrize("missing_index", [False, True])
def test_repair_rebuilds_missing_or_stale_index_and_preserves_other_records(tmp_path, monkeypatch, missing_index):
    from acoustic_orchestrator.pipeline.output_index import upsert_variant
    _, layout, paths, _ = failed_publication(tmp_path, monkeypatch)
    if missing_index:
        layout["render_index_path"].unlink()
    repair = inspect(layout, paths)
    assert repair.publish(layout["render_index_path"])
    records = load_variant_index(layout["render_index_path"])
    other = {**records[paths["variant_id"]], "variant_id": "unrelated", "scene_id": "unrelated"}
    upsert_variant(layout["render_index_path"], records, other)
    assert not repair.publish(layout["render_index_path"])
    assert load_variant_index(layout["render_index_path"])["unrelated"] == other


@pytest.mark.parametrize("code", [0, 7])
def test_unconfirmed_repair_blocks_reuse_chain_and_clarity_until_complete(tmp_path, monkeypatch, code):
    from acoustic_orchestrator.config.loader import load_config
    from acoustic_orchestrator.pipeline.clarity_handoff import _revalidate_render_input
    from acoustic_orchestrator.pipeline.output_index import should_resume_variant
    from acoustic_orchestrator.pipeline.process_retries import RetryChain
    from acoustic_orchestrator.pipeline.persistence_journal import atomic_write, json_bytes
    config_path, layout, paths, outcome_path = failed_publication(tmp_path, monkeypatch, code=code)
    repair = inspect(layout, paths)
    repair.prepare(layout["render_index_path"])
    # The dangerous crash boundary: index, outcome and chain look accepted,
    # but the journal's final completion marker has not been committed.
    from acoustic_orchestrator.pipeline.output_index import upsert_variant
    records = load_variant_index(layout["render_index_path"])
    upsert_variant(layout["render_index_path"], records, repair.index)
    atomic_write(outcome_path, json_bytes(repair.outcome))
    atomic_write(repair.chain_path, json_bytes(repair.chain))
    assert not should_resume_variant(records, paths, metadata_required=True)
    assert "render_history_not_accepted" in _revalidate_render_input(load_config(config_path), records[paths["variant_id"]])
    with pytest.raises((ValueError, RuntimeError)):
        RetryChain(repair.chain_path, repair.chain).verify_resolved()
    assert inspect(layout, paths).publish(layout["render_index_path"])
    records = load_variant_index(layout["render_index_path"])
    assert should_resume_variant(records, paths, metadata_required=True)
    assert "render_history_not_accepted" not in _revalidate_render_input(load_config(config_path), records[paths["variant_id"]])
    assert RetryChain(repair.chain_path, repair.chain).verify_resolved()["exit_code"] == code
    outcome = read_json_object(outcome_path)
    assert outcome["error"] == repair.before_outcome["error"]
    if code:
        assert outcome["warning"]["code"] == "matlab_process_failed_after_completion"
        assert outcome["recovery_reason"] == "post_completion"


def test_v2_without_journal_cannot_become_legacy_reuse(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.output_index import should_resume_variant
    _, layout, paths, outcome_path = failed_publication(tmp_path, monkeypatch)
    repair = inspect(layout, paths)
    outcome_path.write_text(json.dumps(repair.outcome))
    assert not should_resume_variant({}, paths, metadata_required=True)


@pytest.mark.parametrize("blocker", ["other_scene", "running", "private_lock", "writer_lock", "circuit"])
def test_global_preflight_conflict_prevents_all_repair_writes(tmp_path, monkeypatch, blocker):
    from uuid import uuid4
    config_path, layout, paths, outcome_path = failed_publication(tmp_path, monkeypatch, scenes=2)
    if blocker == "other_scene":
        path = sorted(layout["scene_manifest_dir"].glob("*.json"))[1]
        record = read_json_object(path)
        record["scene_id"] = "foreign_scene"
        path.write_text(json.dumps(record))
    elif blocker == "running":
        path = next((layout["render_attempts_dir"].parent / "process_chains").glob("*/chain.json"))
        record = read_json_object(path)
        new_path = path.parent.parent / uuid4().hex / "chain.json"
        new_path.parent.mkdir()
        record["chain_id"] = new_path.parent.name
        record["state"] = "running"
        record["attempts"][-1]["state"] = "running"
        new_path.write_text(json.dumps(record))
    elif blocker == "private_lock":
        runtime = read_json_object(Path(paths["runtime_manifest_path"]))
        (Path(runtime["base_rpf_file"]).parent / "writer.lock").write_text("existing writer")
    elif blocker == "writer_lock":
        (layout["render_index_path"].parent / "static_writer.lock").write_text("existing writer")
    else:
        (layout["render_index_path"].parent / "matlab_process_circuit.json").write_text('{"open":true}')
    root = layout["render_attempts_dir"].parent
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **kw: pytest.fail("blocked preflight must not launch"))
    with pytest.raises((ValueError, RuntimeError, OSError)):
        render_static_scenes(config_path)
    assert not (outcome_path.parent / "persistence_repair/record.json").exists()
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("resume", [True, False])
def test_next_call_repairs_without_overwrite_or_new_process_budget(tmp_path, monkeypatch, resume):
    import yaml
    config_path, layout, paths, outcome_path = failed_publication(tmp_path, monkeypatch)
    raw = yaml.safe_load(config_path.read_text())
    raw["execution"].update(resume_if_possible=resume, overwrite_existing=False)
    config_path.write_text(yaml.safe_dump(raw))
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **kw: pytest.fail("no new process is permitted"))
    if resume:
        manifests, summary = render_static_scenes(config_path)
        assert len(manifests) == summary["resumed_variants"] == 1
        assert summary["matlab_invocations"] == summary["process_retries"] == 0
        assert read_json_object(outcome_path)["attempt_ordinal"] == 1
        assert read_json_object(outcome_path)["retry_policy"]["max_attempts"] == 1
        assert read_json_object(outcome_path)["schema_version"] == 2
    else:
        with pytest.raises((ValueError, RuntimeError, OSError)):
            render_static_scenes(config_path)
        assert read_json_object(outcome_path)["schema_version"] == 1
        assert not (outcome_path.parent / "persistence_repair/record.json").exists()


@pytest.mark.parametrize("code", [0, 7])
def test_summary_counts_only_repairs_confirmed_in_this_call(tmp_path, monkeypatch, code):
    from typer.testing import CliRunner
    from acoustic_orchestrator.cli import app
    config_path, layout, _, _ = failed_publication(tmp_path, monkeypatch, code=code, scenes=2)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return successful_process(command, **kwargs)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    result = CliRunner().invoke(app, ["render-static", str(config_path), "--no-progress"])
    assert result.exit_code == 0, result.output
    assert "persistencias_reparadas=1" in result.stdout
    summary = read_json_object(layout["render_index_path"].parent / "static_run_summary.json")["render"]
    assert summary["persistence_repairs"] == summary["resumed_variants"] == len(calls) == 1
    assert summary["matlab_invocations"] == 1
    assert summary["process_retries"] == summary["variants_completed_after_retry"] == 0
    assert summary["recovered_matlab_processes"] == summary["failed_matlab_processes"] == 0
    assert summary["resumed_recovered_variants"] == bool(code)
    result = CliRunner().invoke(app, ["render-static", str(config_path), "--no-progress"])
    assert result.exit_code == 0, result.output
    assert "persistencias_reparadas=0" in result.stdout
    summary = read_json_object(layout["render_index_path"].parent / "static_run_summary.json")["render"]
    assert summary["persistence_repairs"] == summary["matlab_invocations"] == 0
    assert summary["resumed_variants"] == 2
    assert len(calls) == 1


@pytest.mark.parametrize("stage", ["parallel_canonical", "other_variants"])
def test_next_call_after_outcome_replace_denied_preserves_protected_files(tmp_path, monkeypatch, stage):
    from threading import Barrier
    import time
    from test_parallel_canonical_render import configure as configure_parallel
    from test_render_diagnostics_integration import process_manifest
    from test_render_resilience import evidence
    optional = stage == "other_variants"
    config_path, layout = configure_parallel(tmp_path, scenes=1 if optional else 5,
                                             workers=1 if optional else 3, optional=optional)
    started = Barrier(3, timeout=20)
    calls = []
    def run(command, **kwargs):
        manifest = process_manifest(command)
        calls.append(manifest["scene_id"] + "__" + manifest["render"]["active_output_type"])
        if not optional:
            started.wait()
            if manifest["scene_id"] != "scene_static_0001":
                time.sleep(0.1)  # Leave other attempts in flight when scene 1 fails to commit.
        result = successful_process(command, **kwargs)
        publish_process_receipt(command)
        return result
    original_replace = Path.replace
    denied = []
    def replace(path, target):
        if path.name == "outcome.json.tmp" and not denied:
            candidate = read_json_object(path)
            selected = (candidate["output_type"] != "binaural_hrtf" if optional
                        else candidate["scene_id"] == "scene_static_0001")
            if selected and candidate.get("commit_status") == "committed":
                denied.append(Path(target))
                raise PermissionError(f"[WinError 5] Acceso denegado: {path} -> {target}")
        return original_replace(path, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", replace)
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
        with pytest.raises(RenderStaticRunError):
            render_static_scenes(config_path)
    assert len(calls) == (2 if optional else 3)
    assert len(denied) == 1
    failed = read_json_object(denied[0])
    assert "WinError 5" in failed["persistence_error"]["message"]
    records = load_variant_index(layout["render_index_path"])
    assert records[failed["variant_id"]]["status"] == "completed"
    assert failed["commit_status"] == "failed"
    before = evidence(layout)
    for root in (layout["render_attempts_dir"], layout["render_attempts_dir"].parent / "raven_resources"):
        for file in root.rglob("*"):
            if file.is_file() and (root.name == "raven_resources" or file.name in {"completion.json", "input_manifest.json"}):
                before[file] = (file.read_bytes(), file.stat().st_mtime_ns)
    pending_calls = []
    def remaining(command, **kwargs):
        manifest = process_manifest(command)
        pending_calls.append(manifest["scene_id"] + "__" + manifest["render"]["active_output_type"])
        assert pending_calls[-1] not in calls
        return successful_process(command, **kwargs)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", remaining)
    _, summary = render_static_scenes(config_path)
    assert summary["persistence_repairs"] == 1
    assert summary["resumed_variants"] == len(calls)
    assert len(pending_calls) == summary["matlab_invocations"] == (1 if optional else 2)
    for file, snapshot in before.items():
        if isinstance(snapshot[0], bytes):
            assert (file.read_bytes(), file.stat().st_mtime_ns) == snapshot
        else:
            import hashlib
            assert (hashlib.sha256(file.read_bytes()).hexdigest(), file.stat().st_mtime_ns) == snapshot
    assert read_json_object(denied[0])["schema_version"] == 2
    chains = (layout["render_attempts_dir"].parent / "process_chains").glob("*/chain.json")
    assert all(read_json_object(path)["state"] != "running" for path in chains)


def test_persistent_storage_denial_is_visible_and_third_call_completes_same_journal(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from acoustic_orchestrator.cli import app
    from acoustic_orchestrator.pipeline import persistence_recovery as recovery
    from acoustic_orchestrator.pipeline.persistence_journal import read_journal
    config_path, layout, paths, outcome_path = failed_publication(tmp_path, monkeypatch)
    repair = inspect(layout, paths)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **kw: pytest.fail("repair must not launch another process"))
    real_write = recovery.atomic_write
    def denied(path, data):
        if path == repair.chain_path:
            raise PermissionError("[WinError 5] repair chain still denied")
        return real_write(path, data)
    with monkeypatch.context() as patch:
        patch.setattr(recovery, "atomic_write", denied)
        result = CliRunner().invoke(app, ["render-static", str(config_path), "--no-progress"])
    assert result.exit_code == 1
    assert "PermissionError: [WinError 5] repair chain still denied" in result.stderr
    assert f"attempt_id={repair.outcome['attempt_id']}" in result.stderr
    journal = read_journal(outcome_path)
    assert journal["state"] == "pending"
    assert journal["original_error"]["message"] == "index denied"
    preimages = {p: p.read_bytes() for p in (outcome_path.parent / "persistence_repair").glob("*.before.json")}
    result = CliRunner().invoke(app, ["render-static", str(config_path), "--no-progress"])
    assert result.exit_code == 0, result.output
    assert "persistencias_reparadas=1" in result.stdout
    completed = read_journal(outcome_path)
    assert {key: value for key, value in completed.items() if key != "state"} == {
        key: value for key, value in journal.items() if key != "state"}
    assert completed["state"] == "complete"
    assert all(path.read_bytes() == data for path, data in preimages.items())
    assert len(list(layout["render_attempts_dir"].glob("*/outcome.json"))) == 1


def test_incomplete_temporaries_do_not_accredit_a_repair(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.output_index import should_resume_variant
    _, layout, paths, outcome_path = failed_publication(tmp_path, monkeypatch)
    repair = inspect(layout, paths)
    directory = outcome_path.parent / "persistence_repair"
    directory.mkdir()
    (directory / "record.json.tmp").write_text('{"state":"complete"')
    assert not should_resume_variant(load_variant_index(layout["render_index_path"]), paths, metadata_required=True)
    assert inspect(layout, paths).publish(layout["render_index_path"])


@pytest.mark.parametrize("code,damage", [(0, None), (7, None), (0, "receipt"), (0, "process")])
def test_pyroomacoustics_uses_same_repair_and_fail_closed_guards(tmp_path, monkeypatch, code, damage):
    from acoustic_orchestrator.config import backend_validation
    from test_pra_resilience import configure as configure_pra, fake_worker
    from test_render_resilience import evidence
    real_version = backend_validation.importlib.metadata.version
    monkeypatch.setattr(backend_validation.importlib.metadata, "version",
                        lambda name: "0.10.1" if name == "pyroomacoustics" else real_version(name))
    config_path, layout = configure_pra(tmp_path, attempts=1)
    real_upsert = render_pipeline.upsert_variant
    def deny(index, records, record):
        if record.get("decision") in {"continue", "continue_with_warning"}:
            raise PermissionError("PRA index denied")
        return real_upsert(index, records, record)
    def run(command, **kwargs):
        result = fake_worker(command, **kwargs)
        if code:
            raise subprocess.CalledProcessError(code, command)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(render_pipeline, "upsert_variant", deny)
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
        with pytest.raises(RenderStaticRunError):
            render_static_scenes(config_path)
    outcome_path = next(layout["render_attempts_dir"].glob("*/outcome.json"))
    outcome = read_json_object(outcome_path)
    if damage == "receipt":
        Path(outcome["completion_path"]).unlink()
    elif damage == "process":
        outcome["termination_observed"] = False
        outcome_path.write_text(json.dumps(outcome))
    protected = evidence(layout)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **kw: pytest.fail("repair/rejection must not launch PRA or MATLAB"))
    if damage:
        with pytest.raises((ValueError, RuntimeError, OSError)):
            render_static_scenes(config_path)
        assert read_json_object(outcome_path)["commit_status"] == "failed"
        assert not (outcome_path.parent / "persistence_repair/record.json").exists()
    else:
        _, summary = render_static_scenes(config_path)
        assert summary["persistence_repairs"] == summary["resumed_variants"] == 1
        assert summary["matlab_invocations"] == summary["process_retries"] == 0
        assert summary["resumed_recovered_variants"] == bool(code)
        assert read_json_object(outcome_path)["exit_code"] == code
        assert read_json_object(outcome_path)["schema_version"] == 2
    assert protected == evidence(layout)


def test_cli_reports_corrupt_outcome_with_known_attempt_path(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from acoustic_orchestrator.cli import app
    config_path, _, _, outcome_path = failed_publication(tmp_path, monkeypatch)
    outcome_path.write_text("{broken json")
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **kw: pytest.fail("corrupt evidence must block launch"))
    result = CliRunner().invoke(app, ["render-static", str(config_path), "--no-progress"])
    assert result.exit_code == 1
    assert "JSONDecodeError" in result.stderr
    assert f"evidence={outcome_path.as_posix()}" in result.stderr
    assert f"attempt_id={outcome_path.parent.name}" in result.stderr
