from pathlib import Path

import pytest

from acoustic_orchestrator.pipeline.output_index import inspect_variant, should_resume_variant
from acoustic_orchestrator.pipeline.render_history import load_render_history
from render_fixtures import write_variant_fixture


def legacy_paths(tmp_path):
    return {"variant_id": "scene__binaural_hrtf", "scene_id": "scene", "output_type": "binaural_hrtf",
            "output_subdir": "binaural_hrtf", "runtime_manifest_path": str(tmp_path / "scene__binaural_hrtf.json"),
            "rendered_wav_path": str(tmp_path / "out.wav"), "render_metadata_path": str(tmp_path / "out.json"),
            "receiver_ir_path": str(tmp_path / "receiver.daff")}


def test_strict_legacy_reuse_does_not_invent_attempt_or_process_success(tmp_path):
    paths = legacy_paths(tmp_path)
    write_variant_fixture(paths, metadata=True)
    assert should_resume_variant({}, paths, metadata_required=True)
    resumed = inspect_variant(paths, metadata_required=True, attempted=False, resumed=True)
    assert resumed["status"] == "completed"
    assert resumed["resumed"] and not resumed["attempted"]
    assert "attempt_id" not in resumed and "process_status" not in resumed
    assert not (tmp_path / "render_attempts").exists()


@pytest.mark.parametrize("field,value", [("process_status", "failed"), ("process_status", "running"),
    ("process_status", "launch_failed"), ("exit_code", 1), ("decision", "abort"),
    ("decision", "continue_with_warning"), ("attempt_id", "missing-attempt"), ("recovery_reason", "post_completion"),
    ("attempt_outcome_path", "missing/outcome.json"), ("completion_path", "missing/completion.json")])
def test_known_failure_or_missing_evidence_cannot_be_downgraded_to_legacy(tmp_path, field, value):
    paths = legacy_paths(tmp_path)
    write_variant_fixture(paths, metadata=True)
    record = {"status": "completed", "validation": {"ok": True}, field: value}
    assert not should_resume_variant({paths["variant_id"]: record}, paths, metadata_required=True)


@pytest.mark.parametrize("target", ["wav", "metadata", "runtime"])
def test_weak_legacy_files_need_rerender(tmp_path, target):
    paths = legacy_paths(tmp_path)
    write_variant_fixture(paths, metadata=True)
    field = {"wav": "rendered_wav_path", "metadata": "render_metadata_path", "runtime": "runtime_manifest_path"}[target]
    Path(paths[field]).write_text("not audio" if target == "wav" else "{}", encoding="utf-8")
    assert not should_resume_variant({}, paths, metadata_required=True)


def test_unreadable_unassigned_history_does_not_allow_optimistic_legacy_reuse(tmp_path):
    paths = legacy_paths(tmp_path)
    write_variant_fixture(paths, metadata=True)
    unknown = tmp_path / "render_attempts" / ("b" * 32)
    unknown.mkdir(parents=True)
    (unknown / "outcome.json").write_text('{"truncated":', encoding="utf-8")
    history = load_render_history(unknown.parent)
    assert history.issues
    assert not should_resume_variant({}, paths, metadata_required=True, history=history)
