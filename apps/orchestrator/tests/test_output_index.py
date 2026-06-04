import json
from pathlib import Path

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline.matlab_runner import RenderVariantPaths, build_render_variant_paths
from acoustic_orchestrator.pipeline.output_index import build_planned_variant_record
from acoustic_orchestrator.pipeline.output_index import build_clarity_record
from acoustic_orchestrator.pipeline.output_index import ClarityJobManifestRecord
from acoustic_orchestrator.pipeline.output_index import inspect_variant
from acoustic_orchestrator.pipeline.output_index import inspect_clarity_record
from acoustic_orchestrator.pipeline.output_index import load_clarity_index
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.output_index import should_resume_clarity_job
from acoustic_orchestrator.pipeline.output_index import should_resume_variant
from acoustic_orchestrator.pipeline.output_index import summarize_clarity_index
from acoustic_orchestrator.pipeline.output_index import summarize_variant_index
from acoustic_orchestrator.pipeline.output_index import upsert_clarity_record
from acoustic_orchestrator.pipeline.output_index import upsert_variant
from acoustic_orchestrator.pipeline.output_index import write_clarity_job_manifest
from acoustic_orchestrator.pipeline.output_index import VariantRecord
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout


def test_build_render_variant_paths_returns_deterministic_identity(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    hrtf = {"hrtf_id": "binaural_hrtf", "hrtf_path": str(tmp_path / "catalog.daff")}
    scene_manifest = {
        "project_name": "sim_test",
        "scene_id": "scene_static_0001",
        "receiver": {"hrtfs": [hrtf]},
    }

    variant_paths = build_render_variant_paths(
        scene_manifest=scene_manifest,
        hrtf=hrtf,
        runtime_manifest_dir=resolve_artifact_layout(config.outputs, config.experiment.experiment_id)["runtime_manifest_dir"],
        outputs=config.outputs,
        receiver_output=config.receiver_outputs.binaural_hrtf,
    )

    assert variant_paths["variant_id"] == "scene_static_0001__binaural_hrtf"
    assert Path(variant_paths["runtime_manifest_path"]).parts[-4:] == (
        "manifests",
        "runtime",
        "render",
        "scene_static_0001__binaural_hrtf.json",
    )
    assert Path(variant_paths["rendered_wav_path"]).parts[-2:] == ("binaural_hrtf", "scene_static_0001__binaural_hrtf.wav")
    assert Path(variant_paths["render_metadata_path"]).parts[-2:] == (
        "binaural_hrtf",
        "scene_static_0001__binaural_hrtf__render.json",
    )
    assert variant_paths["scene_id"] == "scene_static_0001"
    assert variant_paths["output_type"] == "binaural_hrtf"
    assert variant_paths["output_subdir"] == "binaural_hrtf"
    assert Path(variant_paths["receiver_ir_path"]).name == "catalog.daff"


def test_inspect_variant_returns_normalized_record_payload(tmp_path: Path) -> None:
    variant_paths = _variant_paths(tmp_path)
    Path(variant_paths["runtime_manifest_path"]).parent.mkdir(parents=True)
    Path(variant_paths["runtime_manifest_path"]).write_text("{}", encoding="utf-8")
    Path(variant_paths["rendered_wav_path"]).parent.mkdir(parents=True)
    Path(variant_paths["rendered_wav_path"]).write_text("wav", encoding="utf-8")

    record = inspect_variant(variant_paths, metadata_required=False, attempted=True, resumed=True)

    assert record == {
        "variant_id": "scene_static_0001__binaural_hrtf",
        "scene_id": "scene_static_0001",
        "output_type": "binaural_hrtf",
        "status": "completed",
        "resumed": True,
        "attempted": True,
        "metadata_required": False,
        "runtime_manifest_path": variant_paths["runtime_manifest_path"],
        "rendered_wav_path": variant_paths["rendered_wav_path"],
        "render_metadata_path": variant_paths["render_metadata_path"],
        "receiver_ir_path": variant_paths["receiver_ir_path"],
        "observed_outputs": {
            "runtime_manifest": True,
            "rendered_wav": True,
            "render_metadata": False,
        },
        "metadata_snapshot": None,
        "validation": {"ok": True, "issues": []},
        "updated_at": record["updated_at"],
    }
    assert record["updated_at"]


def test_build_planned_variant_record_preserves_expected_paths(tmp_path: Path) -> None:
    variant_paths = _variant_paths(tmp_path)

    record = build_planned_variant_record(variant_paths, metadata_required=True)

    assert record["status"] == "planned"
    assert record["attempted"] is False
    assert record["resumed"] is False
    assert record["metadata_required"] is True
    assert record["observed_outputs"] == {
        "runtime_manifest": False,
        "rendered_wav": False,
        "render_metadata": False,
    }
    assert record["validation"] == {"ok": True, "issues": []}
    assert record["metadata_snapshot"] is None


def test_upsert_variant_rewrites_current_record_per_variant(tmp_path: Path) -> None:
    index_path = tmp_path / "output_index.jsonl"
    records: dict[str, VariantRecord] = {}
    variant_paths = _variant_paths(tmp_path)

    first_record = inspect_variant(variant_paths, metadata_required=False, attempted=False)
    second_record = inspect_variant(variant_paths, metadata_required=False, attempted=True)

    upsert_variant(index_path, records, first_record)
    upsert_variant(index_path, records, second_record)

    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["variant_id"] == variant_paths["variant_id"]
    assert load_variant_index(index_path)[variant_paths["variant_id"]]["attempted"] is True


def test_metadata_disabled_allows_completed_without_metadata(tmp_path: Path) -> None:
    variant_paths = _variant_paths(tmp_path)
    Path(variant_paths["runtime_manifest_path"]).parent.mkdir(parents=True)
    Path(variant_paths["runtime_manifest_path"]).write_text("{}", encoding="utf-8")
    Path(variant_paths["rendered_wav_path"]).parent.mkdir(parents=True)
    Path(variant_paths["rendered_wav_path"]).write_text("wav", encoding="utf-8")

    record = inspect_variant(variant_paths, metadata_required=False, attempted=True)

    assert record["status"] == "completed"


def test_failed_attempt_with_partial_outputs_stays_partial(tmp_path: Path) -> None:
    variant_paths = _variant_paths(tmp_path)
    Path(variant_paths["runtime_manifest_path"]).parent.mkdir(parents=True)
    Path(variant_paths["runtime_manifest_path"]).write_text("{}", encoding="utf-8")

    record = inspect_variant(variant_paths, metadata_required=True, attempted=True)

    assert record["status"] == "partial"
    assert summarize_variant_index({record["variant_id"]: record})["partial_variants"] == 1


def test_failed_attempt_with_zero_outputs_is_failed(tmp_path: Path) -> None:
    variant_paths = _variant_paths(tmp_path)

    record = inspect_variant(variant_paths, metadata_required=True, attempted=True)

    assert record["status"] == "failed"
    assert summarize_variant_index({record["variant_id"]: record})["failed_variants"] == 1


def test_load_variant_index_normalizes_legacy_rows(tmp_path: Path) -> None:
    index_path = tmp_path / "output_index.jsonl"
    index_path.write_text(
        json.dumps(
            {
                "variant_id": "scene_static_0001__binaural_hrtf",
                "scene_id": "scene_static_0001",
                "output_type": "binaural_hrtf",
                "status": "completed",
                "resumed": False,
                "attempted": True,
                "metadata_required": False,
                "runtime_manifest_path": str(tmp_path / "logs" / "variant.json"),
                "rendered_wav_path": str(tmp_path / "wav" / "variant.wav"),
                "render_metadata_path": None,
                "receiver_ir_path": str(tmp_path / "catalog.daff"),
                "updated_at": "2026-04-23T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    record = load_variant_index(index_path)["scene_static_0001__binaural_hrtf"]

    assert record["observed_outputs"] == {
        "runtime_manifest": False,
        "rendered_wav": False,
        "render_metadata": False,
    }
    assert record["metadata_snapshot"] is None
    assert record["validation"] == {"ok": True, "issues": []}


def test_inspect_variant_collects_metadata_snapshot_from_summary_fallback(tmp_path: Path) -> None:
    variant_paths = _variant_paths(tmp_path)
    Path(variant_paths["runtime_manifest_path"]).parent.mkdir(parents=True)
    Path(variant_paths["runtime_manifest_path"]).write_text("{}", encoding="utf-8")
    Path(variant_paths["rendered_wav_path"]).parent.mkdir(parents=True)
    Path(variant_paths["rendered_wav_path"]).write_text("wav", encoding="utf-8")
    Path(variant_paths["render_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(variant_paths["render_metadata_path"]).write_text(
        json.dumps(
            {
                "summary": {
                    "scene_id": "scene_static_0001",
                    "output_type": "binaural_hrtf",
                    "hrtf_id": "binaural_hrtf",
                    "output_wav_path": variant_paths["rendered_wav_path"],
                    "output_metadata_path": variant_paths["render_metadata_path"],
                }
            }
        ),
        encoding="utf-8",
    )

    record = inspect_variant(variant_paths, metadata_required=True, attempted=True)

    assert record["status"] == "completed"
    assert record["metadata_snapshot"] == {
        "scene_id": "scene_static_0001",
        "output_type": "binaural_hrtf",
        "hrtf_id": "binaural_hrtf",
        "rendered_wav_path": variant_paths["rendered_wav_path"],
        "render_metadata_path": variant_paths["render_metadata_path"],
    }
    assert record["validation"] == {"ok": True, "issues": []}


def test_inspect_variant_preserves_metadata_mismatch_for_debugging(tmp_path: Path) -> None:
    variant_paths = _variant_paths(tmp_path)
    Path(variant_paths["runtime_manifest_path"]).parent.mkdir(parents=True)
    Path(variant_paths["runtime_manifest_path"]).write_text("{}", encoding="utf-8")
    Path(variant_paths["rendered_wav_path"]).parent.mkdir(parents=True)
    Path(variant_paths["rendered_wav_path"]).write_text("wav", encoding="utf-8")
    Path(variant_paths["render_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(variant_paths["render_metadata_path"]).write_text(
        json.dumps(
            {
                "scene_id": "scene_static_9999",
                "hrtf_id": "different_hrtf",
                "render": {
                    "output_wav_path": str(tmp_path / "wrong.wav"),
                    "output_metadata_path": variant_paths["render_metadata_path"],
                },
            }
        ),
        encoding="utf-8",
    )

    record = inspect_variant(variant_paths, metadata_required=True, attempted=True)

    assert record["status"] == "completed"
    assert record["validation"]["ok"] is False
    assert any("scene_id" in issue for issue in record["validation"]["issues"])
    assert any("output_type" in issue or "hrtf_id" in issue for issue in record["validation"]["issues"])
    assert any("rendered_wav_path" in issue for issue in record["validation"]["issues"])
    assert record["metadata_snapshot"] is not None


def test_should_resume_only_when_outputs_are_observably_complete(tmp_path: Path) -> None:
    variant_paths = _variant_paths(tmp_path)
    Path(variant_paths["runtime_manifest_path"]).parent.mkdir(parents=True)
    Path(variant_paths["runtime_manifest_path"]).write_text("{}", encoding="utf-8")
    Path(variant_paths["rendered_wav_path"]).parent.mkdir(parents=True)
    Path(variant_paths["rendered_wav_path"]).write_text("wav", encoding="utf-8")

    assert should_resume_variant({}, variant_paths, metadata_required=False) is True
    assert should_resume_variant({}, variant_paths, metadata_required=True) is False


def test_should_resume_rejects_completed_record_with_validation_issues(tmp_path: Path) -> None:
    variant_paths = _variant_paths(tmp_path)
    Path(variant_paths["runtime_manifest_path"]).parent.mkdir(parents=True)
    Path(variant_paths["runtime_manifest_path"]).write_text("{}", encoding="utf-8")
    Path(variant_paths["rendered_wav_path"]).parent.mkdir(parents=True)
    Path(variant_paths["rendered_wav_path"]).write_text("wav", encoding="utf-8")
    Path(variant_paths["render_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(variant_paths["render_metadata_path"]).write_text("{}", encoding="utf-8")

    existing_record = inspect_variant(variant_paths, metadata_required=True, attempted=True)
    existing_record["validation"] = {"ok": False, "issues": ["metadata could not be parsed"]}

    assert should_resume_variant(
        {existing_record["variant_id"]: existing_record},
        variant_paths,
        metadata_required=True,
    ) is False


def test_upsert_clarity_record_rewrites_current_record_per_variant(tmp_path: Path) -> None:
    index_path = tmp_path / "clarity_index.jsonl"
    records = {}
    job = _clarity_job(tmp_path)

    first_record = build_clarity_record(
        job,
        manifest_path=tmp_path / "clarity_jobs.jsonl",
        status="planned",
        attempted=False,
        resumed=False,
        runnable=True,
    )
    second_record = build_clarity_record(
        job,
        manifest_path=tmp_path / "clarity_jobs.jsonl",
        status="blocked",
        attempted=False,
        resumed=False,
        runnable=False,
        validation_issues=["missing_input_wav"],
    )

    upsert_clarity_record(index_path, records, first_record)
    upsert_clarity_record(index_path, records, second_record)

    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert load_clarity_index(index_path)[job["job_id"]]["status"] == "blocked"


def test_inspect_clarity_record_detects_completed_outputs(tmp_path: Path) -> None:
    job = _clarity_job(tmp_path)
    Path(job["expected_output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(job["expected_output_wav_path"]).write_text("wav", encoding="utf-8")
    Path(job["expected_output_metadata_path"]).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "job_id": job["job_id"],
                "variant_id": job["variant_id"],
                "scene_id": job["scene_id"],
                "output_type": job["output_type"],
                "hearing_profile_id": job["hearing_profile_id"],
                "status": "completed",
                "input_wav_path": job["input_wav_path"],
                "input_render_metadata_path": job["input_render_metadata_path"],
                "output_wav_path": job["expected_output_wav_path"],
                "degradation_applied": {"left": {"250": 10}, "right": {"250": 12}},
            }
        ),
        encoding="utf-8",
    )

    record = inspect_clarity_record(
        job,
        manifest_path=tmp_path / "clarity_jobs.jsonl",
        attempted=True,
    )

    assert record["status"] == "completed"
    assert record["validation"] == {"ok": True, "issues": []}


def test_should_resume_clarity_job_requires_existing_completed_outputs(tmp_path: Path) -> None:
    job = _clarity_job(tmp_path)
    Path(job["expected_output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(job["expected_output_wav_path"]).write_text("wav", encoding="utf-8")
    Path(job["expected_output_metadata_path"]).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "job_id": job["job_id"],
                "variant_id": job["variant_id"],
                "scene_id": job["scene_id"],
                "output_type": job["output_type"],
                "hearing_profile_id": job["hearing_profile_id"],
                "status": "completed",
                "input_wav_path": job["input_wav_path"],
                "input_render_metadata_path": job["input_render_metadata_path"],
                "output_wav_path": job["expected_output_wav_path"],
                "degradation_applied": {"left": {"250": 10}, "right": {"250": 12}},
            }
        ),
        encoding="utf-8",
    )

    assert should_resume_clarity_job({}, job, manifest_path=tmp_path / "clarity_jobs.jsonl") is True

    existing_record = inspect_clarity_record(
        job,
        manifest_path=tmp_path / "clarity_jobs.jsonl",
        attempted=True,
    )
    existing_record["validation"] = {"ok": False, "issues": ["bad metadata"]}

    assert (
        should_resume_clarity_job(
            {job["job_id"]: existing_record},
            job,
            manifest_path=tmp_path / "clarity_jobs.jsonl",
        )
        is False
    )


def test_inspect_clarity_record_marks_resumed_completed_outputs_as_skipped(tmp_path: Path) -> None:
    job = _clarity_job(tmp_path)
    Path(job["expected_output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(job["expected_output_wav_path"]).write_text("wav", encoding="utf-8")
    Path(job["expected_output_metadata_path"]).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "job_id": job["job_id"],
                "variant_id": job["variant_id"],
                "scene_id": job["scene_id"],
                "output_type": job["output_type"],
                "hearing_profile_id": job["hearing_profile_id"],
                "status": "completed",
                "input_wav_path": job["input_wav_path"],
                "input_render_metadata_path": job["input_render_metadata_path"],
                "output_wav_path": job["expected_output_wav_path"],
                "degradation_applied": {"left": {"250": 10}, "right": {"250": 12}},
            }
        ),
        encoding="utf-8",
    )

    record = inspect_clarity_record(
        job,
        manifest_path=tmp_path / "clarity_jobs.jsonl",
        attempted=False,
        resumed=True,
    )

    assert record["status"] == "skipped"
    assert record["resumed"] is True


def test_write_clarity_job_manifest_and_summary(tmp_path: Path) -> None:
    manifest_path = tmp_path / "clarity_jobs.jsonl"
    job = _clarity_job(tmp_path)
    write_clarity_job_manifest(manifest_path, [job])

    record = build_clarity_record(
        job,
        manifest_path=manifest_path,
        status="skipped",
        attempted=False,
        resumed=False,
        runnable=False,
        validation_issues=["output_type_not_targeted"],
    )
    summary = summarize_clarity_index({job["job_id"]: record})

    assert manifest_path.read_text(encoding="utf-8").strip()
    assert summary["total_jobs"] == 1
    assert summary["skipped_jobs"] == 1
    assert summary["invalid_jobs"] == 1
    assert summary["batch_status"] == "blocked"


def test_inspect_clarity_record_uses_failed_metadata_without_treating_batch_as_terminal_missing_wav(tmp_path: Path) -> None:
    job = _clarity_job(tmp_path)
    Path(job["expected_output_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(job["expected_output_metadata_path"]).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "job_id": job["job_id"],
                "variant_id": job["variant_id"],
                "scene_id": job["scene_id"],
                "output_type": job["output_type"],
                "hearing_profile_id": job["hearing_profile_id"],
                "status": "failed",
                "input_wav_path": job["input_wav_path"],
                "input_render_metadata_path": job["input_render_metadata_path"],
                "output_wav_path": None,
                "error": {"type": "FileNotFoundError", "message": "input missing"},
            }
        ),
        encoding="utf-8",
    )

    record = inspect_clarity_record(job, manifest_path=tmp_path / "clarity_jobs.jsonl", attempted=True)
    summary = summarize_clarity_index({job["job_id"]: record})

    assert record["status"] == "failed"
    assert record["validation"] == {"ok": True, "issues": []}
    assert summary["failed_jobs"] == 1
    assert summary["batch_status"] == "failed"


def test_inspect_clarity_record_treats_completed_metadata_without_wav_as_partial(tmp_path: Path) -> None:
    job = _clarity_job(tmp_path)
    Path(job["expected_output_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(job["expected_output_metadata_path"]).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "job_id": job["job_id"],
                "variant_id": job["variant_id"],
                "scene_id": job["scene_id"],
                "output_type": job["output_type"],
                "hearing_profile_id": job["hearing_profile_id"],
                "status": "completed",
                "input_wav_path": job["input_wav_path"],
                "input_render_metadata_path": job["input_render_metadata_path"],
                "output_wav_path": job["expected_output_wav_path"],
                "degradation_applied": {"left": {"250": 10}, "right": {"250": 12}},
            }
        ),
        encoding="utf-8",
    )

    record = inspect_clarity_record(job, manifest_path=tmp_path / "clarity_jobs.jsonl", attempted=True)

    assert record["status"] == "partial"
    assert record["validation"]["ok"] is False
    assert any("expected_output_wav_missing" in issue for issue in record["validation"]["issues"])


def test_summarize_clarity_index_marks_mixed_batch_as_partial(tmp_path: Path) -> None:
    completed_job = _clarity_job(tmp_path)
    failed_job = {
        **_clarity_job(tmp_path),
        "job_id": "clarity__scene_static_0001__binaural_hrtf__severe_loss",
        "hearing_profile_id": "severe_loss",
        "output_dir": str(tmp_path / "degraded" / "binaural_hrtf" / "severe_loss"),
        "expected_output_wav_path": str(tmp_path / "degraded" / "binaural_hrtf" / "severe_loss" / "scene_static_0001__binaural_hrtf.wav"),
        "expected_output_metadata_path": str(
            tmp_path / "degraded" / "binaural_hrtf" / "severe_loss" / "scene_static_0001__binaural_hrtf.json"
        ),
    }

    Path(completed_job["expected_output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(completed_job["expected_output_wav_path"]).write_text("wav", encoding="utf-8")
    Path(completed_job["expected_output_metadata_path"]).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "job_id": completed_job["job_id"],
                "variant_id": completed_job["variant_id"],
                "scene_id": completed_job["scene_id"],
                "output_type": completed_job["output_type"],
                "hearing_profile_id": completed_job["hearing_profile_id"],
                "status": "completed",
                "input_wav_path": completed_job["input_wav_path"],
                "input_render_metadata_path": completed_job["input_render_metadata_path"],
                "output_wav_path": completed_job["expected_output_wav_path"],
                "degradation_applied": {"left": {"250": 10}, "right": {"250": 12}},
            }
        ),
        encoding="utf-8",
    )
    Path(failed_job["expected_output_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(failed_job["expected_output_metadata_path"]).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "job_id": failed_job["job_id"],
                "variant_id": failed_job["variant_id"],
                "scene_id": failed_job["scene_id"],
                "output_type": failed_job["output_type"],
                "hearing_profile_id": failed_job["hearing_profile_id"],
                "status": "failed",
                "input_wav_path": failed_job["input_wav_path"],
                "input_render_metadata_path": failed_job["input_render_metadata_path"],
                "output_wav_path": None,
                "error": {"type": "RuntimeError", "message": "boom"},
            }
        ),
        encoding="utf-8",
    )

    records = {
        completed_job["job_id"]: inspect_clarity_record(completed_job, manifest_path=tmp_path / "clarity_jobs.jsonl", attempted=True),
        failed_job["job_id"]: inspect_clarity_record(failed_job, manifest_path=tmp_path / "clarity_jobs.jsonl", attempted=True),
    }

    summary = summarize_clarity_index(records)

    assert summary["completed_jobs"] == 1
    assert summary["failed_jobs"] == 1
    assert summary["batch_status"] == "partial"


def test_should_resume_clarity_job_rejects_legacy_fixed_filename_outputs(tmp_path: Path) -> None:
    job = _clarity_job(tmp_path)
    legacy_dir = tmp_path / "clarity" / "binaural_hrtf" / job["variant_id"]
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "degraded.wav").write_text("wav", encoding="utf-8")
    (legacy_dir / "clarity_result.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "job_id": job["job_id"],
                "variant_id": job["variant_id"],
                "scene_id": job["scene_id"],
                "output_type": job["output_type"],
                "hearing_profile_id": job["hearing_profile_id"],
                "status": "completed",
                "input_wav_path": job["input_wav_path"],
                "input_render_metadata_path": job["input_render_metadata_path"],
                "output_wav_path": str((legacy_dir / "degraded.wav").resolve()),
                "degradation_applied": {"left": {"250": 10}, "right": {"250": 12}},
            }
        ),
        encoding="utf-8",
    )

    assert should_resume_clarity_job({}, job, manifest_path=tmp_path / "clarity_jobs.jsonl") is False


def _variant_paths(tmp_path: Path) -> RenderVariantPaths:
    return {
        "variant_id": "scene_static_0001__binaural_hrtf",
        "scene_id": "scene_static_0001",
        "output_type": "binaural_hrtf",
        "output_subdir": "binaural_hrtf",
        "runtime_manifest_path": str(tmp_path / "artifacts" / "sim_test" / "manifests" / "runtime" / "render" / "scene_static_0001__binaural_hrtf.json"),
        "rendered_wav_path": str(tmp_path / "artifacts" / "sim_test" / "outputs" / "render" / "binaural_hrtf" / "scene_static_0001__binaural_hrtf.wav"),
        "render_metadata_path": str(tmp_path / "artifacts" / "sim_test" / "outputs" / "render" / "binaural_hrtf" / "scene_static_0001__binaural_hrtf__render.json"),
        "receiver_ir_path": str(tmp_path / "catalog.daff"),
    }


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
experiment:
  experiment_id: sim_test
  description: test
  scene_type: static
  random_seed: 123

execution:
  num_simulations: 1
  num_workers: 1
  overwrite_existing: true
  resume_if_possible: true
  save_scene_manifest: true
  save_render_metadata: true

raven:
  base_rpf_file: ./base_room.rpf

render:
  sample_rate_hz: 48000
  generate_rir: false
  generate_brir: true
  simulation_type_rt: true
  simulation_type_is: true
  num_particles: 100
  is_order_ps: 2

receiver_sampling:
  one_receiver_per_scene: true
  position_strategy:
    type: random_uniform_inside_room
    margin_m: {x: 0.2, y: 0.2, z: 0.2}
    fixed_height_m: {min: 1.2, max: 1.4}
  orientation_strategy:
    type: random_yaw
    yaw_deg: {min: -180.0, max: 180.0}
    pitch_deg: {fixed: 0.0}
    roll_deg: {fixed: 0.0}

receiver_outputs:
  binaural_hrtf:
    enabled: true
    ir_catalog_path: ./assets/hrtf
    file_pattern: "*.daff"
    output_subdir: binaural_hrtf
    num_channels: 2
    required: true

room_sampling:
  dimensions_m:
    length: {min: 4.0, max: 4.5}
    width: {min: 3.0, max: 3.5}
    height: {min: 2.4, max: 2.8}
  materials:
    walls: [painted_brick]
    floor: [wood]
    ceiling: [plaster]
  semantic_surfaces:
    enable_walls: true
    enable_floor: true
    enable_ceiling: true

source_sampling:
  min_sources: 1
  max_sources: 1
  timing:
    allow_offsets: false
    start_time_s: {min: 0.0, max: 0.0}
  gain_db: {min: 0.0, max: 0.0}
  default_orientation_strategy:
    type: random_yaw
    yaw_deg: {min: -180.0, max: 180.0}
    pitch_deg: {fixed: 0.0}
    roll_deg: {fixed: 0.0}
  source_types:
    - event_type: speech
      role: base
      min_count: 1
      max_count: 1
      probability: 1.0
      audio_dir: ./assets/events/speech
      spatial_policy:
        type: random_valid

scene_validation:
  min_distance_source_to_receiver_m: 0.1
  min_distance_between_sources_m: 0.1
  require_sources_inside_room: true
  require_receiver_inside_room: true
  max_sampling_attempts_per_scene: 10

outputs:
  artifact_root: ./artifacts
  naming:
    scene_id_prefix: scene_static
    wav_pattern: "{scene_id}__{output_type}.wav"
    metadata_pattern: "{scene_id}__{output_type}__render.json"

hearing_degradation:
  enabled: false
  input_targets: [binaural_hrtf]
  hearing_profiles_path: ./hearing_profiles.yaml
        """.strip(),
        encoding="utf-8",
    )

    (tmp_path / "base_room.rpf").write_text("dummy", encoding="utf-8")
    (tmp_path / "hearing_profiles.yaml").write_text("dummy", encoding="utf-8")
    (tmp_path / "assets" / "hrtf").mkdir(parents=True)
    (tmp_path / "assets" / "hrtf" / "subject01.daff").write_text("dummy", encoding="utf-8")
    (tmp_path / "assets" / "events" / "speech").mkdir(parents=True)
    (tmp_path / "assets" / "events" / "speech" / "speech_01.wav").write_text("dummy", encoding="utf-8")
    for material_id in ["painted_brick", "wood", "plaster"]:
        material_dir = tmp_path / "assets" / "materials" / material_id
        material_dir.mkdir(parents=True)
        (material_dir / "sample.mat").write_text(
            "[Material]\nname=test\nnotes=test\nabsorp=" + ", ".join(["0.2"] * 31) + "\nscatter=" + ", ".join(["0.1"] * 31),
            encoding="utf-8",
        )
    return config_path


def _clarity_job(tmp_path: Path) -> ClarityJobManifestRecord:
    return {
        "schema_version": "1.0",
        "run_id": "sim_test",
        "job_id": "clarity__scene_static_0001__binaural_hrtf__mild_loss",
        "variant_id": "scene_static_0001__binaural_hrtf",
        "scene_id": "scene_static_0001",
        "output_type": "binaural_hrtf",
        "hearing_profile_id": "mild_loss",
        "hearing_profiles_path": str(tmp_path / "hearing_profiles.yaml"),
        "backend_invocation": {
            "mode": "uv_project",
            "entrypoint": "clarity-backend",
            "use_uv": True,
            "backend_project_path": None,
        },
        "input_wav_path": str(tmp_path / "input.wav"),
        "input_render_metadata_path": str(tmp_path / "render.json"),
        "output_dir": str(tmp_path / "degraded" / "binaural_hrtf" / "mild_loss"),
        "expected_output_wav_path": str(
            tmp_path / "degraded" / "binaural_hrtf" / "mild_loss" / "scene_static_0001__binaural_hrtf.wav"
        ),
        "expected_output_metadata_path": str(
            tmp_path / "degraded" / "binaural_hrtf" / "mild_loss" / "scene_static_0001__binaural_hrtf.json"
        ),
    }
