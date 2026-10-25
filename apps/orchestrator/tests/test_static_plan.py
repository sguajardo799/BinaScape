"""All fixtures live under pytest's temporary workspace; no real run is used."""

import json
from pathlib import Path

import pytest

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.experiment import sampler
from acoustic_orchestrator.experiment.scene_builder import build_static_manifest
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.static_plan import (
    StaticPlanConflict,
    apply_static_plan,
    inspect_static_plan,
    normalized_static_config,
    record_static_manifest_revision,
    static_writer_lock,
)
from test_generate_manifests import _build_workspace, _write_config


def fixture_config(tmp_path, count=3):
    workspace = _build_workspace(tmp_path)
    path = _write_config(workspace, "preflight", num_simulations=count,
                         resume_if_possible=True, overwrite_existing=False)
    config = load_config(path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    return config, layout


def publish(config, layout):
    plan = inspect_static_plan(config, layout)
    with static_writer_lock(layout) as lock:
        apply_static_plan(plan, lock=lock)
    return plan


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
             for p in root.rglob("*") if p.is_file()}


def test_plan_progress_new_reused_and_conflict_is_read_only(tmp_path):
    from acoustic_orchestrator.pipeline.progress import ProgressReporter

    config, layout = fixture_config(tmp_path)
    events = []
    inspect_static_plan(config, layout, progress=ProgressReporter(events.append))
    resolved = [e for e in events if e.kind == "resolved"]
    assert [e.outcome for e in resolved] == ["nuevas"] * 3
    assert [e.completed for e in resolved] == [1, 2, 3]
    assert not layout["run_root"].exists()
    plan = publish(config, layout)
    events.clear()
    inspect_static_plan(config, layout, progress=ProgressReporter(events.append))
    assert [e.outcome for e in events if e.kind == "resolved"] == ["conservadas"] * 3
    plan.manifests[-1].path.write_text("{")
    before = snapshot(layout["run_root"])
    events.clear()
    with pytest.raises(StaticPlanConflict):
        inspect_static_plan(config, layout, progress=ProgressReporter(events.append))
    assert events[-1].completed == 3
    assert "no publicado" in events[-1].outcome
    assert events[-1].counters["conflictos"] == 1
    assert snapshot(layout["run_root"]) == before


def test_publication_announced_only_after_revalidation(tmp_path):
    from acoustic_orchestrator.pipeline.progress import ProgressReporter

    config, layout = fixture_config(tmp_path)
    plan = inspect_static_plan(config, layout)
    events = []
    with static_writer_lock(layout) as lock:
        apply_static_plan(plan, lock=lock, progress=ProgressReporter(events.append))
    assert [e.stage for e in events if e.kind == "begin"] == [
        "Verificando plan antes de publicar", "Publicando plan"]
    events.clear()
    stale = inspect_static_plan(config, layout)
    stale.manifests[0].path.write_text("{")
    with static_writer_lock(layout) as lock:
        with pytest.raises(StaticPlanConflict):
            apply_static_plan(stale, lock=lock, progress=ProgressReporter(events.append))
    assert not any(e.stage == "Publicando plan" for e in events)


@pytest.mark.parametrize("interrupted_index", [0, 1])
def test_interrupted_sampling_leaves_scene_pending(tmp_path, interrupted_index):
    from acoustic_orchestrator.pipeline.progress import ProgressReporter

    config, layout = fixture_config(tmp_path)
    events = []
    report = ProgressReporter(events.append)

    def interrupt_scene(config, rng, scene_index):
        if scene_index == interrupted_index:
            raise KeyboardInterrupt
        return sampler.sample_static_scene(config, rng, scene_index)

    with pytest.raises(KeyboardInterrupt):
        inspect_static_plan(config, layout, sample_scene=interrupt_scene, progress=report)

    resolved = [event for event in events if event.kind == "resolved"]
    assert len(resolved) == interrupted_index
    assert all(event.outcome == "nuevas" for event in resolved)
    assert events[-1].completed == interrupted_index
    assert events[-1].total == config.execution.num_simulations
    assert report.counters["conflictos"] == 0
    assert len(report.starts) == 1  # The interrupted scene remains in progress.
    assert not any(event.kind == "finish" and event.stage == "Planificacion" for event in events)
    assert not layout["run_root"].exists()


def test_read_only_inspection_and_no_resampling_on_recorded_reuse(tmp_path, monkeypatch):
    config, layout = fixture_config(tmp_path)
    plan = inspect_static_plan(config, layout)
    assert not layout["run_root"].exists()
    assert len(plan.manifests) == 3
    with static_writer_lock(layout) as lock:
        apply_static_plan(plan, lock=lock)
    before = snapshot(layout["run_root"])
    monkeypatch.setattr(sampler, "sample_static_scene", lambda *a, **kw: pytest.fail("Reused scene resampled"))
    reused = inspect_static_plan(config, layout)
    assert not any(m.needs_write for m in reused.manifests)
    assert not reused.needs_plan_write
    with static_writer_lock(layout) as lock:
        apply_static_plan(reused, lock=lock)
    assert snapshot(layout["run_root"]) == before


@pytest.mark.parametrize("corruption", ["broken", "duplicate", "nan", "infinity", "pose", "seed_float", "surface", "identity", "clockwise", "shape", "treatment"])
def test_late_scene_corruption_does_not_write_anything(tmp_path, corruption):
    config, layout = fixture_config(tmp_path)
    plan = publish(config, layout)
    path = plan.manifests[-1].path
    data = json.loads(path.read_text())
    if corruption == "broken":
        text = "{"
    elif corruption == "duplicate":
        text = path.read_text().replace('"scene_type": "static"', '"scene_type": "static", "scene_type": "static"')
    else:
        if corruption == "nan":
            data["receiver"]["position_m"][0] = float("nan")
        elif corruption == "infinity":
            data["receiver"]["position_m"][0] = float("inf")
        elif corruption == "pose":
            data["receiver"]["position_m"] = [999.0, 0, 999.0]
        elif corruption == "seed_float":
            data["sampling"]["effective_seed"] = float(data["sampling"]["effective_seed"])
        elif corruption == "surface":
            data["room"]["acoustic_surfaces"]["floor"]["effective_absorption"] = [2.0] * 31
        elif corruption == "clockwise":
            data["room"]["geometry"]["footprint_vertices_m"].reverse()
        elif corruption == "shape":
            data["room"]["geometry"]["type"] = "unsupported"
        elif corruption == "treatment":
            data["room"]["acoustic_surfaces"]["floor"]["treatment"]["preset_id"] = "unsupported"
        else:
            data["scene_id"] = "another_scene"
        text = json.dumps(data)
    path.write_text(text)
    plan.manifests[0].path.unlink()  # A hole before the conflict must not be filled.
    before = snapshot(layout["run_root"])
    with pytest.raises(StaticPlanConflict, match=path.name):
        inspect_static_plan(config, layout)
    assert snapshot(layout["run_root"]) == before


def test_missing_intermediate_scene_is_only_write(tmp_path):
    config, layout = fixture_config(tmp_path)
    plan = publish(config, layout)
    missing = plan.manifests[1]
    original = missing.path.read_bytes()
    missing.path.unlink()
    before = {m.path: (m.path.read_bytes(), m.path.stat().st_mtime_ns)
              for m in plan.manifests if m.path.exists()}
    new = publish(config, layout)
    assert [m.scene_index for m in new.manifests if m.needs_write] == [1]
    assert missing.path.read_bytes() == original
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == value for p, value in before.items())


def test_operational_changes_allowed_acoustic_count_catalog_and_versions_blocked(tmp_path, monkeypatch):
    config, layout = fixture_config(tmp_path)
    publish(config, layout)
    original_hash = normalized_static_config(config)
    config.execution.num_workers = 7
    config.execution.overwrite_existing = True
    config.execution.save_scene_manifest = False
    if hasattr(config.execution, "matlab_process_retry"):
        config.execution.matlab_process_retry.max_attempts = 1
    assert normalized_static_config(config) == original_hash
    assert not inspect_static_plan(config, layout).needs_plan_write
    config.execution.overwrite_existing = False
    for change in ("acoustic", "count", "catalog", "version"):
        changed = config.model_copy(deep=True)
        if change == "acoustic":
            changed.render.num_particles += 1
        elif change == "count":
            changed.execution.num_simulations += 1
        elif change == "catalog":
            (changed.receiver_outputs.binaural_hrtf.ir_catalog_path / "new.daff").write_text("new")
        else:
            monkeypatch.setattr(sampler, "PROPOSAL_STRATEGY_VERSION", 999)
        with pytest.raises(StaticPlanConflict, match="contract changed"):
            inspect_static_plan(changed, layout)
        if change == "catalog":
            (changed.receiver_outputs.binaural_hrtf.ir_catalog_path / "new.daff").unlink()


def test_selected_file_replaced_at_same_path_blocks(tmp_path):
    config, layout = fixture_config(tmp_path)
    plan = publish(config, layout)
    dependency = Path(plan.manifests[0].manifest["sources"][0]["audio_path"])
    dependency.write_bytes(dependency.read_bytes() + b"different")
    before = snapshot(layout["run_root"])
    with pytest.raises(StaticPlanConflict, match="Selected dependency changed"):
        inspect_static_plan(config, layout)
    assert snapshot(layout["run_root"]) == before


def test_legacy_exact_late_attempt_replay_preserves_big_seed_t30_history(tmp_path):
    config, layout = fixture_config(tmp_path, count=1)
    # Sampling effective seeds are 64-bit integers, unrelated to render.seed.
    config.experiment.random_seed = 42
    sampled = sampler.sample_static_scene(config, None, 0, start_scene_attempt=8)
    sampled["background_noise"] = sampler.build_background_noise_plan(config, 1)[0]
    path = layout["scene_manifest_dir"] / "scene_static_0001.json"
    manifest = build_static_manifest(config, sampled, 0, path)
    assert manifest["sampling"]["effective_seed"] > 2**53
    manifest["reverberation_sampling"]["raven_t30_rejections"] = [{"sampling_attempt": 2, "reason": "T30"}]
    manifest["reverberation_sampling"]["raven_retry_count"] = 1
    manifest["reverberation_batch"] = {"accepted_realizations": 0, "old": True}
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest))
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    plan = publish(config, layout)
    assert not plan.manifests[0].needs_write
    assert plan.metadata["adoption"] == "legacy_replay"
    assert all(d["provenance"] == "observed_at_adoption" for d in plan.metadata["selected_dependencies"].values())
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert plan.manifests[0].manifest["reverberation_sampling"]["raven_retry_count"] == 1


def test_legacy_functional_mismatch_cannot_be_adopted(tmp_path):
    config, layout = fixture_config(tmp_path, count=1)
    plan = publish(config, layout)
    plan.plan_path.unlink()
    path = plan.manifests[0].path
    manifest = json.loads(path.read_text())
    manifest["sources"][0]["gain_db"] += 0.1
    path.write_text(json.dumps(manifest))
    with pytest.raises(StaticPlanConflict, match="cannot be replayed"):
        inspect_static_plan(config, layout)
    assert not plan.plan_path.exists()


def test_failure_history_preserved_when_hole_later_succeeds(tmp_path, monkeypatch):
    config, layout = fixture_config(tmp_path)
    original = sampler.sample_static_scene

    def sample(config, rng, index, **kwargs):
        if index == 1:
            raise sampler.SceneSamplingFailure({"scene_index": 1, "stage": "sources", "scene_attempts": 50,
                                               "rejections_by_reason": {"sources": 50}})
        return original(config, rng, index, **kwargs)

    monkeypatch.setattr(sampler, "sample_static_scene", sample)
    first = publish(config, layout)
    assert len(first.failures) == 1
    history = first.failures_path.read_bytes()
    monkeypatch.setattr(sampler, "sample_static_scene", original)
    second = publish(config, layout)
    assert not second.failures
    assert len(second.failure_history) == 1
    assert second.failures_path.read_bytes() == history
    assert [m.scene_index for m in second.manifests if m.needs_write] == [1]


def test_changed_preconditions_block_all_publication(tmp_path):
    config, layout = fixture_config(tmp_path)
    plan = inspect_static_plan(config, layout)
    plan.manifests[-1].path.parent.mkdir(parents=True)
    plan.manifests[-1].path.write_text("changed concurrently")
    before = snapshot(layout["run_root"])
    with static_writer_lock(layout) as lock:
        with pytest.raises(StaticPlanConflict, match="changed after preflight"):
            apply_static_plan(plan, lock=lock)
    assert snapshot(layout["run_root"]) == before


def test_writer_lock_contention_and_uncertain_lock_never_broken(tmp_path):
    _, layout = fixture_config(tmp_path)
    with static_writer_lock(layout) as lock:
        contents = lock.path.read_bytes()
        with pytest.raises(StaticPlanConflict, match="Writer lock exists"):
            with static_writer_lock(layout):
                pytest.fail("Second writer entered")
        assert lock.path.read_bytes() == contents
    assert not lock.path.exists()
    lock.path.write_text("uncertain owner, malformed lock")
    with pytest.raises(StaticPlanConflict, match="Manual intervention"):
        with static_writer_lock(layout):
            pytest.fail("Uncertain lock was broken")
    assert lock.path.read_text() == "uncertain owner, malformed lock"


def test_acoustic_revision_preserves_previous_realization_history(tmp_path):
    config, layout = fixture_config(tmp_path, count=1)
    first = publish(config, layout)
    path = first.manifests[0].path
    old_attempt = first.manifests[0].manifest["sampling"]["scene_attempt"]
    assert old_attempt < config.scene_validation.max_scene_attempts
    sampled = sampler.sample_static_scene(config, None, 0, start_scene_attempt=old_attempt + 1)
    sampled["background_noise"] = sampler.build_background_noise_plan(config, 1)[0]
    manifest = build_static_manifest(config, sampled, 0, path)
    manifest["reverberation_sampling"]["raven_t30_rejections"] = [{"sampling_attempt": old_attempt}]
    with static_writer_lock(layout) as lock:
        path.write_text(json.dumps(manifest))
        record_static_manifest_revision(config, manifest, path, lock=lock)
    revised = inspect_static_plan(config, layout)
    assert not revised.needs_plan_write
    revisions = revised.metadata["scene_revision_history"]
    assert len(revisions) == 1
    assert revisions[0]["previous"]["sampling"]["scene_attempt"] == old_attempt
    assert revisions[0]["replacement"]["sampling"]["scene_attempt"] == manifest["sampling"]["scene_attempt"]
    path.unlink()
    restored = publish(config, layout)
    assert restored.manifests[0].manifest == manifest
    assert restored.manifests[0].needs_write


def test_catalog_changed_after_inspection_blocks_publication(tmp_path):
    config, layout = fixture_config(tmp_path, count=1)
    plan = inspect_static_plan(config, layout)
    (config.receiver_outputs.binaural_hrtf.ir_catalog_path / "late.daff").write_text("changed catalog")
    with static_writer_lock(layout) as lock:
        with pytest.raises(StaticPlanConflict, match="catalog changed after preflight"):
            apply_static_plan(plan, lock=lock)
    assert not plan.plan_path.exists()
    assert not any(m.path.exists() for m in plan.manifests)


def test_acoustic_contract_change_requires_overwrite_and_preserves_previous_plan(tmp_path):
    config, layout = fixture_config(tmp_path, count=2)
    original = publish(config, layout)
    original_metadata = json.loads(original.plan_path.read_text())
    config.render.sample_rate_hz = 48000
    before = snapshot(layout["run_root"])
    with pytest.raises(StaticPlanConflict, match="contract changed"):
        inspect_static_plan(config, layout)
    assert snapshot(layout["run_root"]) == before
    config.execution.overwrite_existing = True
    replacement = inspect_static_plan(config, layout)
    assert all(item.needs_write for item in replacement.manifests)
    assert all(item.manifest["render"]["sample_rate_hz"] == 48000 for item in replacement.manifests)
    assert replacement.metadata["plan_revision_history"] == [
        {"reason": "functional_contract_changed", "previous_plan": original_metadata}]
    assert snapshot(layout["run_root"]) == before
    with static_writer_lock(layout) as lock:
        apply_static_plan(replacement, lock=lock)
    reused = inspect_static_plan(config, layout)
    assert not reused.needs_plan_write
    assert not any(item.needs_write for item in reused.manifests)
    assert reused.metadata["plan_revision_history"] == replacement.metadata["plan_revision_history"]


def test_generation_version_change_requires_explicit_fresh_run(tmp_path, monkeypatch):
    config, layout = fixture_config(tmp_path, count=1)
    original = publish(config, layout)
    monkeypatch.setattr(sampler, "PROPOSAL_STRATEGY_VERSION", 2)
    config.execution.overwrite_existing = True
    before = snapshot(layout["run_root"])
    with pytest.raises(StaticPlanConflict, match="requires resume=false and overwrite=true"):
        inspect_static_plan(config, layout)
    assert snapshot(layout["run_root"]) == before
    config.execution.resume_if_possible = False
    replacement = publish(config, layout)
    assert replacement.manifests[0].needs_write
    assert replacement.metadata["plan_revision_history"][0]["previous_plan"] == original.metadata
    assert replacement.metadata["plan_revision_history"][0]["reason"] == "generation_contract_changed"


def test_custom_sampler_used_for_generation_and_legacy_and_missing_replays(tmp_path):
    config, layout = fixture_config(tmp_path, count=1)
    calls = []

    def sample(config, rng, index, **kwargs):
        calls.append((index, kwargs))
        return sampler.sample_static_scene(config, rng, index, **kwargs)

    generated = inspect_static_plan(config, layout, sample_scene=sample)
    assert calls == [(0, {})]
    with static_writer_lock(layout) as lock:
        apply_static_plan(generated, lock=lock)
    generated.plan_path.unlink()
    calls.clear()
    legacy = inspect_static_plan(config, layout, sample_scene=sample)
    assert calls == [(0, {"start_scene_attempt": generated.manifests[0].manifest["sampling"]["scene_attempt"]})]
    with static_writer_lock(layout) as lock:
        apply_static_plan(legacy, lock=lock)
    legacy.manifests[0].path.unlink()
    calls.clear()
    restored = inspect_static_plan(config, layout, sample_scene=sample)
    assert calls == [(0, {"start_scene_attempt": generated.manifests[0].manifest["sampling"]["scene_attempt"]})]
    assert restored.manifests[0].needs_write


def test_empty_failure_journal_published_once(tmp_path):
    config, layout = fixture_config(tmp_path, count=1)
    first = inspect_static_plan(config, layout)
    assert first.needs_failures_write
    assert not first.failures_path.exists()
    with static_writer_lock(layout) as lock:
        apply_static_plan(first, lock=lock)
    before = (first.failures_path.read_bytes(), first.failures_path.stat().st_mtime_ns)
    assert before[0] == b""
    second = publish(config, layout)
    assert not second.needs_failures_write
    assert (first.failures_path.read_bytes(), first.failures_path.stat().st_mtime_ns) == before


def test_corrupt_manifest_requires_explicit_fresh_run_even_when_overwrite_allowed(tmp_path):
    config, layout = fixture_config(tmp_path, count=1)
    original = publish(config, layout)
    original.manifests[0].path.write_text("{")
    config.execution.overwrite_existing = True
    with pytest.raises(StaticPlanConflict):
        inspect_static_plan(config, layout)
    config.execution.resume_if_possible = False
    replacement = publish(config, layout)
    assert replacement.manifests[0].needs_write
    assert replacement.metadata["plan_revision_history"][0]["reason"] == "explicit_fresh_run"


def test_smaller_authorized_batch_retains_old_scene_without_treating_it_as_active(tmp_path):
    config, layout = fixture_config(tmp_path, count=2)
    original = publish(config, layout)
    retired_path = original.manifests[1].path
    before = (retired_path.read_bytes(), retired_path.stat().st_mtime_ns)
    config.execution.num_simulations = 1
    config.execution.overwrite_existing = True
    replacement = publish(config, layout)
    assert len(replacement.manifests) == 1
    assert replacement.metadata["retired_scene_paths"] == [retired_path.as_posix()]
    assert (retired_path.read_bytes(), retired_path.stat().st_mtime_ns) == before
    assert not inspect_static_plan(config, layout).needs_plan_write


@pytest.mark.parametrize("resume,overwrite", [(False, False), (False, True), (True, False), (True, True)])
def test_options_matrix(tmp_path, resume, overwrite):
    config, layout = fixture_config(tmp_path, count=1)
    publish(config, layout)
    config.execution.resume_if_possible = resume
    config.execution.overwrite_existing = overwrite
    if not resume and not overwrite:
        with pytest.raises(StaticPlanConflict, match="resume=false"):
            inspect_static_plan(config, layout)
    else:
        plan = inspect_static_plan(config, layout)
        assert plan.manifests[0].needs_write is (not resume)
