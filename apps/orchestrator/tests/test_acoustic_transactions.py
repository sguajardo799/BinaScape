from pathlib import Path
from threading import Barrier

import pytest

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline import acoustic_transactions as acoustic, render_pipeline
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.stop_control import RenderStopped, StopControl
from test_generate_manifests import _write_valid_raven_metadata
from test_render_acceptance import publish_process_receipt
from test_render_diagnostics_integration import process_manifest, successful_process
from test_render_resilience import configure


def snapshot(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def deferred(tmp_path, monkeypatch, *, scenes=2, reject_count=None, optional=False, workers=None):
    from uuid import uuid4
    # Keep private RAVEN resource paths below Windows' legacy path threshold.
    workspace = tmp_path.parent / ("act_" + uuid4().hex[:8])
    workers = workers or scenes
    if optional:
        from test_render_retry_dispatch import configure as optional_configure
        config, layout = optional_configure(workspace, scenes=scenes, workers=workers, optional=True)
    else:
        config, layout = configure(workspace, scenes=scenes, workers=workers)
    render_pipeline.generate_static_manifests(config)
    # Match a resumed run: the output directory already exists before workers
    # build their paths, rather than racing Windows path canonicalization with
    # another fixture worker's first directory creation.
    (layout["render_outputs_root"] / "binaural_hrtf").mkdir(parents=True, exist_ok=True)
    control = StopControl()
    barrier = Barrier(workers, timeout=20)

    def run(command, **kwargs):
        from types import SimpleNamespace
        manifest = process_manifest(command)
        rejected = reject_count is None or int(manifest["scene_id"].rsplit("_", 1)[1]) <= reject_count
        _write_valid_raven_metadata(manifest, [0.5] * 9 if rejected else None)
        publish_process_receipt(command)
        barrier.wait()
        control.request()
        return SimpleNamespace(returncode=0)

    with monkeypatch.context() as patch:
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
        patch.setattr(render_pipeline, "sample_static_scene", lambda *a, **kw: pytest.fail("no resampling during stop"))
        patch.setattr(render_pipeline._RenderCoordinator, "_archive_replaced_outputs", lambda *a, **kw: pytest.fail("no moves during stop"))
        with pytest.raises(RenderStopped):
            render_pipeline.render_static_scenes(config, stop=control)
    return config, layout


def test_two_drained_rejections_record_intents_and_plan_one_common_target_read_only(tmp_path, monkeypatch):
    config, layout = deferred(tmp_path, monkeypatch)
    intents = list(layout["render_attempts_dir"].glob("*/acoustic_continuation/record.json"))
    assert len(intents) == 2
    before = snapshot(layout["run_root"])
    transaction = acoustic.inspect_transaction(load_config(config), layout)
    assert before == snapshot(layout["run_root"])
    assert not transaction.path.exists()
    assert [p["intent"]["scene_index"] for p in transaction.participants] == [0, 1]
    assert all((p["replacement"] is None) != (p["failure"] is None) for p in transaction.participants)
    plan_writes = [op for op in transaction.plan["operations"] if Path(op["path"]).name == "static_plan.json"]
    assert len(plan_writes) == 1
    target = __import__("json").loads(acoustic._decoded(plan_writes[0]["after"]))
    assert len(target["scene_revision_history"]) == 2
    for participant in transaction.participants:
        intent = participant["intent"]
        if participant["replacement"] is not None:
            assert target["scenes"][intent["scene_id"]]["sampling"] == participant["replacement"]["sampling"]
            assert len(participant["replacement"]["reverberation_sampling"]["raven_t30_rejections"]) == 1
        else:
            assert target["failed_scenes"][intent["scene_id"]] == participant["failure"]
        assert intent["manifest_before"] == acoustic._encoded(Path(intent["manifest_path"]).read_bytes())
    assert all(op["destination"].startswith(transaction.path.parent.as_posix()) for op in transaction.plan["operations"] if op["kind"] == "move")


def test_terminal_sampling_failure_is_in_shared_plan_and_failure_journal(tmp_path, monkeypatch):
    config, layout = deferred(tmp_path, monkeypatch)
    from acoustic_orchestrator.experiment.sampler import SceneSamplingFailure

    def exhausted(config, unused, index, **kwargs):
        assert kwargs["start_scene_attempt"] >= 2
        raise SceneSamplingFailure({"scene_index": index, "stage": "sampling", "scene_attempts": config.scene_validation.max_scene_attempts,
                                    "rejections_by_reason": {"raven_t30_invalid": 1}})

    before = snapshot(layout["run_root"])
    transaction = acoustic.inspect_transaction(load_config(config), layout, sample_scene=exhausted)
    assert before == snapshot(layout["run_root"])
    assert all(p["replacement"] is None and p["failure"] for p in transaction.participants)
    plan_op = transaction.plan["operations"][-1]
    target = __import__("json").loads(acoustic._decoded(plan_op["after"]))
    assert len(target["failed_scenes"]) == 2 and not target["scenes"]
    failure_op = transaction.plan["operations"][-2]
    assert len(acoustic._decoded(failure_op["after"]).splitlines()) == 2


def test_existing_transaction_is_loaded_without_resampling_and_duplicate_active_plans_block(tmp_path, monkeypatch):
    config, layout = deferred(tmp_path, monkeypatch)
    transaction = acoustic.inspect_transaction(load_config(config), layout)
    transaction.path.parent.mkdir(parents=True)
    transaction.path.write_bytes(acoustic.json_bytes(transaction.plan))
    before = snapshot(layout["run_root"])
    loaded = acoustic.inspect_transaction(load_config(config), layout,
                                          sample_scene=lambda *a, **kw: pytest.fail("must not sample twice"))
    assert loaded.plan == transaction.plan
    assert before == snapshot(layout["run_root"])
    duplicate = transaction.path.parent.parent / ("f" * 32) / "plan.json"
    duplicate.parent.mkdir()
    duplicate.write_bytes(transaction.path.read_bytes())
    with pytest.raises(RuntimeError, match="Multiple active"):
        acoustic.inspect_transaction(load_config(config), layout)


def test_changed_evidence_blocks_planning_without_writes(tmp_path, monkeypatch):
    config, layout = deferred(tmp_path, monkeypatch, scenes=1)
    intent = read_json_object(next(layout["render_attempts_dir"].glob("*/acoustic_continuation/record.json")))
    Path(intent["files"][0]["path"]).write_bytes(b"foreign")
    before = snapshot(layout["run_root"])
    with pytest.raises(RuntimeError, match="changed"):
        acoustic.inspect_transaction(load_config(config), layout)
    assert before == snapshot(layout["run_root"])


def test_projection_is_read_only_and_cannot_be_applied_as_an_ordinary_plan(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.static_plan import static_writer_lock, apply_static_plan

    config_path, layout = deferred(tmp_path, monkeypatch)
    config = load_config(config_path)
    transaction = acoustic.inspect_transaction(config, layout)
    with static_writer_lock(layout) as lock:
        before = snapshot(layout["run_root"])
        plan, repairs = transaction.preflight(config, layout, lock=lock)
        assert plan.projected and len(plan.manifests) == 2 and not repairs
        with pytest.raises(ValueError, match="inspection-only"):
            apply_static_plan(plan, lock=lock)
        assert before == snapshot(layout["run_root"])


@pytest.mark.parametrize("damage", ["other_scene", "private_lock", "unknown_process", "circuit", "artifact", "unmapped_artifact", "writer_lock"])
def test_projection_conflict_does_not_write_or_dispatch(tmp_path, monkeypatch, damage):
    import json
    from uuid import uuid4
    from acoustic_orchestrator.pipeline.static_plan import static_writer_lock

    config_path, layout = deferred(tmp_path, monkeypatch, scenes=3, reject_count=2, optional=damage == "unmapped_artifact")
    config = load_config(config_path)
    transaction = acoustic.inspect_transaction(config, layout)
    intent = transaction.participants[0]["intent"]
    if damage == "other_scene":
        path = sorted(layout["scene_manifest_dir"].glob("*.json"))[2]
        record = read_json_object(path)
        record["scene_id"] = "foreign"
        path.write_text(json.dumps(record))
    elif damage == "private_lock":
        runtime = read_json_object(Path(intent["variants"][0]["runtime_manifest_path"]))
        (Path(runtime["base_rpf_file"]).parent / "writer.lock").write_text("unknown writer")
    elif damage == "unknown_process":
        existing = next((layout["render_attempts_dir"].parent / "process_chains").glob("*/chain.json"))
        record = read_json_object(existing)
        path = existing.parent.parent / uuid4().hex / "chain.json"
        path.parent.mkdir()
        record.update(chain_id=path.parent.name, manifest_path=str(path.parent / "unknown.json"), state="running")
        record["attempts"][-1]["state"] = "running"
        path.write_text(json.dumps(record))
    elif damage == "circuit":
        path = layout["render_index_path"].parent / "matlab_process_circuit.json"
        record = read_json_object(path)
        record["open"] = True
        path.write_text(json.dumps(record))
    elif damage == "writer_lock":
        (layout["render_index_path"].parent / "static_writer.lock").write_text("other writer")
    else:
        # A change in inventory or a file that unexpectedly appeared both veto.
        if damage == "artifact":
            path = Path(intent["files"][0]["path"])
        else:
            path = Path(intent["absent"][0])
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"foreign")
    before = snapshot(layout["run_root"])
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **kw: pytest.fail("preflight must not dispatch"))
    with pytest.raises((RuntimeError, ValueError, OSError)):
        with static_writer_lock(layout) as lock:
            transaction.preflight(config, layout, lock=lock)
    assert before == snapshot(layout["run_root"])


def apply_operation_for_fixture(operation):
    path = Path(operation["path"])
    if operation["kind"] == "move":
        destination = Path(operation["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.rename(destination)
    else:
        after = acoustic._decoded(operation["after"])
        if after is None:
            path.unlink()
        else:
            path.write_bytes(after)


def test_partial_prefix_projection_validates_both_participants_and_other_scene(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.static_plan import static_writer_lock

    config_path, layout = deferred(tmp_path, monkeypatch, scenes=3, reject_count=2)
    config = load_config(config_path)
    transaction = acoustic.inspect_transaction(config, layout)
    # Finish the first scene's moves+manifest but leave the shared plan old.
    boundary = next(i for i, operation in enumerate(transaction.plan["operations"], 1)
                    if operation["kind"] == "write")
    for operation in transaction.plan["operations"][:boundary]:
        apply_operation_for_fixture(operation)
    with static_writer_lock(layout) as lock:
        before = snapshot(layout["run_root"])
        plan, _ = transaction.preflight(config, layout, lock=lock)
        assert len(plan.manifests) == 3
        assert transaction.position(config, layout) == boundary
        assert before == snapshot(layout["run_root"])
    # A later operation alone is not an allowed prefix, even if each file is
    # individually a known original or target state.
    apply_operation_for_fixture(transaction.plan["operations"][-1])
    with pytest.raises(RuntimeError, match="prefix"):
        transaction.view(config, layout)


def test_projection_rejects_ambiguous_participants(tmp_path, monkeypatch):
    config_path, layout = deferred(tmp_path, monkeypatch)
    config = load_config(config_path)
    transaction = acoustic.inspect_transaction(config, layout)
    transaction.plan["participants"].append(transaction.participants[0])
    transaction.plan["plan_sha256"] = acoustic._hash({k: v for k, v in transaction.plan.items() if k != "plan_sha256"})
    with pytest.raises(RuntimeError, match="Ambiguous"):
        transaction.view(config, layout)


def test_publication_combines_revisions_and_preserves_every_moved_byte(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.static_plan import static_writer_lock, inspect_static_plan

    config_path, layout = deferred(tmp_path, monkeypatch)
    config = load_config(config_path)
    transaction = acoustic.inspect_transaction(config, layout)
    originals = {op["path"]: (Path(op["path"]).read_bytes(), Path(op["path"]).stat().st_mtime_ns)
                 for op in transaction.plan["operations"] if op["kind"] == "move"}
    with static_writer_lock(layout) as lock:
        assert transaction.publish(config, layout, lock=lock)
        after = snapshot(layout["run_root"])
        assert not transaction.publish(config, layout, lock=lock)
        assert after == snapshot(layout["run_root"])
    for operation in transaction.plan["operations"]:
        if operation["kind"] == "move":
            destination = Path(operation["destination"])
            assert not Path(operation["path"]).exists()
            assert (destination.read_bytes(), destination.stat().st_mtime_ns) == originals[operation["path"]]
    assert acoustic.inspect_transaction(config, layout, sample_scene=lambda *a, **kw: pytest.fail("no resampling")) is None
    plan = inspect_static_plan(config, layout)
    assert len(plan.manifests) + len(plan.failures) == 2
    assert len(plan.metadata["scene_revision_history"]) == 2


@pytest.mark.parametrize("when", ["before", "after"])
def test_resume_at_every_move_and_shared_write_boundary(tmp_path, monkeypatch, when):
    from acoustic_orchestrator.pipeline.static_plan import static_writer_lock

    # Exercise every operation, not merely the first move or the final plan.
    first_config, first_layout = deferred(tmp_path, monkeypatch, scenes=1)
    first = acoustic.inspect_transaction(load_config(first_config), first_layout)
    count = len(first.plan["operations"])
    for boundary in range(count):
        if boundary == 0:
            config_path, layout, transaction = first_config, first_layout, first
        else:
            config_path, layout = deferred(tmp_path, monkeypatch, scenes=1)
            transaction = acoustic.inspect_transaction(load_config(config_path), layout)
        config = load_config(config_path)
        operation = transaction.plan["operations"][boundary]
        original = acoustic._apply_operation
        fired = []

        def interrupted(actual):
            if actual == operation and when == "before":
                fired.append(True)
                raise PermissionError("before acoustic operation")
            original(actual)
            if actual == operation and when == "after":
                fired.append(True)
                raise PermissionError("after acoustic operation")

        with static_writer_lock(layout) as lock:
            with monkeypatch.context() as patch:
                patch.setattr(acoustic, "_apply_operation", interrupted)
                with pytest.raises(PermissionError):
                    transaction.publish(config, layout, lock=lock)
            assert fired
            resumed = acoustic.inspect_transaction(config, layout, sample_scene=lambda *a, **kw: pytest.fail("must reuse target"))
            assert resumed.plan == transaction.plan and resumed.path == transaction.path
            assert resumed.publish(config, layout, lock=lock)
            assert not resumed.publish(config, layout, lock=lock)
        assert acoustic.inspect_transaction(config, layout) is None


@pytest.mark.parametrize("boundary", ["plan", "pending_state", "link", "participant_complete", "complete"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_resume_journal_and_link_publication_boundaries(tmp_path, monkeypatch, boundary, when):
    from acoustic_orchestrator.pipeline.static_plan import static_writer_lock

    config_path, layout = deferred(tmp_path, monkeypatch, scenes=1)
    config = load_config(config_path)
    transaction = acoustic.inspect_transaction(config, layout)
    original = acoustic.atomic_write
    fired = []

    def interrupted(path, data):
        record = __import__("json").loads(data) if path.name in {"plan.json", "state.json", "transaction.json"} else {}
        matches = ((boundary == "plan" and path == transaction.path)
                   or (boundary == "pending_state" and path.name == "state.json" and record["state"] == "pending")
                   or (boundary == "link" and path.name == "transaction.json" and record["state"] == "pending")
                   or (boundary == "participant_complete" and path.name == "transaction.json" and record["state"] == "complete")
                   or (boundary == "complete" and path.name == "state.json" and record["state"] == "complete"))
        if matches and when == "before" and not fired:
            fired.append(True)
            raise PermissionError("before journal boundary")
        original(path, data)
        if matches and when == "after" and not fired:
            fired.append(True)
            raise PermissionError("after journal boundary")

    with static_writer_lock(layout) as lock:
        with monkeypatch.context() as patch:
            patch.setattr(acoustic, "atomic_write", interrupted)
            with pytest.raises(PermissionError):
                transaction.publish(config, layout, lock=lock)
        assert fired
        if boundary == "plan" and when == "before":
            assert transaction.position(config, layout) == 0
            assert transaction.publish(config, layout, lock=lock)
        elif boundary == "complete" and when == "after":
            assert not transaction.publish(config, layout, lock=lock)
        else:
            resumed = acoustic.inspect_transaction(config, layout, sample_scene=lambda *a, **kw: pytest.fail("must reuse target"))
            assert resumed.path == transaction.path and resumed.plan == transaction.plan
            assert resumed.publish(config, layout, lock=lock)


def test_stop_during_transaction_leaves_a_known_prefix_then_resumes(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.static_plan import static_writer_lock

    config_path, layout = deferred(tmp_path, monkeypatch, scenes=1)
    config = load_config(config_path)
    transaction = acoustic.inspect_transaction(config, layout)
    control = StopControl()
    original = acoustic._apply_operation

    def stop_after_operation(operation):
        original(operation)
        control.request()

    with static_writer_lock(layout) as lock:
        with monkeypatch.context() as patch:
            patch.setattr(acoustic, "_apply_operation", stop_after_operation)
            with pytest.raises(RenderStopped):
                transaction.publish(config, layout, lock=lock, stop=control)
        assert transaction.position(config, layout) >= 1
        assert transaction.publish(config, layout, lock=lock)


def test_normal_third_call_resumes_two_t30_participants_and_preserves_accepted_scene(tmp_path, monkeypatch):
    from test_render_resilience import evidence
    from types import SimpleNamespace

    config_path, layout = deferred(tmp_path, monkeypatch, scenes=4, workers=3, reject_count=2)
    before_evidence = evidence(layout)
    protected = {p: value for p, value in before_evidence.items() if "scene_static_0003" in str(p)}
    protected.update(snapshot(layout["run_root"] / "metadata" / "raven_resources"))
    original = acoustic._apply_operation
    denied = []

    def interrupt(operation):
        original(operation)
        if operation["kind"] == "write" and "scene_static_0001.json" in operation["path"] and not denied:
            denied.append(True)
            raise PermissionError("second call acoustic scene denied after publication")

    with monkeypatch.context() as patch:
        patch.setattr(acoustic, "_apply_operation", interrupt)
        with pytest.raises(PermissionError, match="second call"):
            render_pipeline.render_static_scenes(config_path)
    assert denied
    active = acoustic.inspect_transaction(load_config(config_path), layout,
                                          sample_scene=lambda *a, **kw: pytest.fail("target is already durable"))
    expected_new = {p["intent"]["scene_id"] for p in active.participants if p["replacement"] is not None} | {"scene_static_0004"}
    calls = []

    def run(command, **kwargs):
        manifest = process_manifest(command)
        assert manifest["scene_id"] in expected_new
        calls.append(manifest["scene_id"])
        _write_valid_raven_metadata(manifest)
        publish_process_receipt(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    _, summary = render_pipeline.render_static_scenes(config_path)
    assert set(calls) == expected_new and len(calls) == summary["matlab_invocations"]
    assert summary["process_retries"] == 0
    assert summary["resumed_variants"] == 1
    assert acoustic.inspect_transaction(load_config(config_path), layout) is None
    import hashlib
    for path, (content, mtime) in protected.items():
        actual = path.read_bytes() if isinstance(content, bytes) else hashlib.sha256(path.read_bytes()).hexdigest()
        assert (actual, path.stat().st_mtime_ns) == (content, mtime)
    plan = read_json_object(layout["render_index_path"].parent / "static_plan.json")
    assert len(plan["scene_revision_history"]) == 2
    for participant in active.participants:
        if participant["replacement"] is not None:
            actual = read_json_object(Path(participant["intent"]["manifest_path"]))
            assert actual["reverberation_sampling"]["raven_retry_count"] == 1
            assert len(actual["reverberation_sampling"]["raven_t30_rejections"]) == 1
