import json
from pathlib import Path

import pytest

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline.output_index import load_clarity_index
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes, run_clarity_handoff


def test_run_clarity_handoff_prepare_then_resume_without_duplicate_jobs(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    layout = _prepare_completed_render_variant(config_path)

    first_summary = run_clarity_handoff(config_path, submit=False)
    first_manifest_rows = _read_jsonl(layout["clarity_jobs_path"])

    Path(first_manifest_rows[0]["expected_output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(first_manifest_rows[0]["expected_output_wav_path"]).write_text("wav", encoding="utf-8")
    Path(first_manifest_rows[0]["expected_output_metadata_path"]).write_text(
        _completed_clarity_result(first_manifest_rows[0]),
        encoding="utf-8",
    )

    second_summary = run_clarity_handoff(config_path, submit=False)
    records = load_clarity_index(layout["clarity_index_path"])

    assert first_summary["planned_jobs"] == 1
    assert len(first_manifest_rows) == 1
    assert second_summary["planned_jobs"] == 0
    assert second_summary["resumed_jobs"] == 1
    assert second_summary["skipped_jobs"] == 1
    assert second_summary["batch_status"] == "completed"
    assert layout["clarity_jobs_path"].read_text(encoding="utf-8") == ""
    assert len(records) == 1
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["status"] == "skipped"
    assert json.loads(Path(first_manifest_rows[0]["expected_output_metadata_path"]).read_text(encoding="utf-8")) == {
        "schema_version": "1.0",
        "job_id": first_manifest_rows[0]["job_id"],
        "variant_id": first_manifest_rows[0]["variant_id"],
        "scene_id": first_manifest_rows[0]["scene_id"],
        "output_type": first_manifest_rows[0]["output_type"],
        "hearing_profile_id": first_manifest_rows[0]["hearing_profile_id"],
        "status": "completed",
        "input_wav_path": first_manifest_rows[0]["input_wav_path"],
        "input_render_metadata_path": first_manifest_rows[0]["input_render_metadata_path"],
        "output_wav_path": first_manifest_rows[0]["expected_output_wav_path"],
        "degradation_applied": {"left": {"250": 10}, "right": {"250": 12}},
    }


@pytest.mark.parametrize(
    ("resume_if_possible", "force_rerun"),
    [
        (False, False),
        (True, True),
    ],
)
def test_run_clarity_handoff_can_force_rerun_completed_outputs(
    tmp_path: Path,
    resume_if_possible: bool,
    force_rerun: bool,
) -> None:
    config_path = _write_config(
        tmp_path,
        resume_if_possible=resume_if_possible,
        force_rerun=force_rerun,
    )
    layout = _prepare_completed_render_variant(config_path)

    first_summary = run_clarity_handoff(config_path, submit=False)
    first_manifest_rows = _read_jsonl(layout["clarity_jobs_path"])

    Path(first_manifest_rows[0]["expected_output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(first_manifest_rows[0]["expected_output_wav_path"]).write_text("wav", encoding="utf-8")
    Path(first_manifest_rows[0]["expected_output_metadata_path"]).write_text(
        _completed_clarity_result(first_manifest_rows[0]),
        encoding="utf-8",
    )

    second_summary = run_clarity_handoff(config_path, submit=False)
    second_manifest_rows = _read_jsonl(layout["clarity_jobs_path"])
    records = load_clarity_index(layout["clarity_index_path"])

    assert first_summary["planned_jobs"] == 1
    assert len(first_manifest_rows) == 1
    assert second_summary["planned_jobs"] == 1
    assert second_summary["resumed_jobs"] == 0
    assert second_summary["batch_status"] == "planned"
    assert len(second_manifest_rows) == 1
    assert second_manifest_rows[0]["job_id"] == first_manifest_rows[0]["job_id"]
    assert len(records) == 1
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["status"] == "planned"
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["resumed"] is False


def test_render_static_scenes_auto_submits_clarity_for_enabled_static_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        backend_project_path="./clarity-backend",
        resume_if_possible=False,
        force_rerun=True,
        auto_submit=True,
        save_render_metadata=True,
    )
    layout = resolve_artifact_layout(
        load_config(config_path).outputs,
        "sim_test",
    )
    (tmp_path / "hearing_profiles.yaml").write_text(_hearing_profiles_yaml(), encoding="utf-8")
    submissions: list[list[dict[str, object]]] = []

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        render = manifest["render"]
        Path(render["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(render["output_wav_path"]).write_text("wav", encoding="utf-8")
        Path(render["output_metadata_path"]).write_text(
            json.dumps(
                {
                    "scene_id": manifest["scene_id"],
                    "output_type": manifest["receiver"]["hrtfs"][0]["hrtf_id"],
                    "hrtf_id": manifest["receiver"]["hrtfs"][0]["hrtf_id"],
                    "render": {
                        "output_wav_path": render["output_wav_path"],
                        "output_metadata_path": render["output_metadata_path"],
                    },
                }
            ),
            encoding="utf-8",
        )

    def fake_submit(runner, manifest_path: Path):
        jobs = _read_jsonl(manifest_path)
        submissions.append(jobs)
        job = jobs[0]
        Path(job["expected_output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(job["expected_output_wav_path"]).write_text("wav", encoding="utf-8")
        Path(job["expected_output_metadata_path"]).write_text(
            _completed_clarity_result(job),
            encoding="utf-8",
        )
        return {
            "status": "submitted",
            "command": ["uv", "run", "--project", str(runner.backend_project_path), runner.entrypoint, "run-manifest", manifest_path.as_posix()],
            "message": "submitted",
        }

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_handoff.submit_clarity_manifest", fake_submit)

    _, summary = render_static_scenes(config_path)
    records = load_clarity_index(layout["clarity_index_path"])

    assert len(submissions) == 1
    assert len(submissions[0]) == 1
    assert submissions[0][0]["job_id"] == "clarity__scene_static_0001__binaural_hrtf__mild_loss"
    assert summary["clarity"] is not None
    assert summary["clarity"]["auto_submit"] is True
    assert summary["clarity"]["submitted"] is True
    assert summary["clarity"]["completed_jobs"] == 1
    assert summary["clarity"]["resumed_jobs"] == 0
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["status"] == "completed"


def test_run_clarity_handoff_retry_reuses_same_job_without_duplicate_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, backend_project_path="./clarity-backend")
    layout = _prepare_completed_render_variant(config_path)
    attempts = {"count": 0}

    def fake_submit(runner, manifest_path: Path):
        attempts["count"] += 1
        jobs = _read_jsonl(manifest_path)
        if attempts["count"] == 1:
            Path(jobs[0]["expected_output_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(jobs[0]["expected_output_metadata_path"]).write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "job_id": jobs[0]["job_id"],
                        "variant_id": jobs[0]["variant_id"],
                        "scene_id": jobs[0]["scene_id"],
                        "output_type": jobs[0]["output_type"],
                        "hearing_profile_id": jobs[0]["hearing_profile_id"],
                        "status": "failed",
                        "input_wav_path": jobs[0]["input_wav_path"],
                        "input_render_metadata_path": jobs[0]["input_render_metadata_path"],
                        "output_wav_path": None,
                        "error": {"type": "RuntimeError", "message": "backend failed"},
                    }
                ),
                encoding="utf-8",
            )
            return {
                "status": "submitted",
                "command": ["uv", "run", "--project", "backend", "clarity-backend", "run-manifest", manifest_path.as_posix()],
                "message": "backend failed but results were written",
            }

        Path(jobs[0]["expected_output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(jobs[0]["expected_output_wav_path"]).write_text("wav", encoding="utf-8")
        Path(jobs[0]["expected_output_metadata_path"]).write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "job_id": jobs[0]["job_id"],
                    "variant_id": jobs[0]["variant_id"],
                    "scene_id": jobs[0]["scene_id"],
                    "output_type": jobs[0]["output_type"],
                    "hearing_profile_id": jobs[0]["hearing_profile_id"],
                    "status": "completed",
                    "input_wav_path": jobs[0]["input_wav_path"],
                    "input_render_metadata_path": jobs[0]["input_render_metadata_path"],
                    "output_wav_path": jobs[0]["expected_output_wav_path"],
                    "degradation_applied": {"left": {"250": 10}, "right": {"250": 12}},
                }
            ),
            encoding="utf-8",
        )
        return {
            "status": "submitted",
            "command": ["uv", "run", "--project", "backend", "clarity-backend", "run-manifest", manifest_path.as_posix()],
            "message": "submitted",
        }

    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_handoff.submit_clarity_manifest", fake_submit)

    first_summary = run_clarity_handoff(config_path, submit=True)
    first_records = load_clarity_index(layout["clarity_index_path"])
    second_summary = run_clarity_handoff(config_path, submit=True)
    second_records = load_clarity_index(layout["clarity_index_path"])

    assert first_summary["submitted"] is True
    assert first_summary["failed_jobs"] == 1
    assert first_summary["batch_status"] == "failed"
    assert len(first_records) == 1
    assert first_records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["status"] == "failed"
    assert second_summary["submitted"] is True
    assert second_summary["completed_jobs"] == 1
    assert second_summary["batch_status"] == "completed"
    assert len(second_records) == 1
    assert second_records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["status"] == "completed"
    assert attempts["count"] == 2


def test_run_clarity_handoff_marks_mixed_backend_results_as_partial_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, backend_project_path="./clarity-backend")
    layout = _prepare_completed_render_variant(config_path)
    Path((tmp_path / "hearing_profiles.yaml")).write_text(
        _hearing_profiles_yaml(profile_ids=["mild_loss", "severe_loss"]),
        encoding="utf-8",
    )

    def fake_submit(runner, manifest_path: Path):
        jobs = _read_jsonl(manifest_path)
        completed_job = next(job for job in jobs if job["hearing_profile_id"] == "mild_loss")
        failed_job = next(job for job in jobs if job["hearing_profile_id"] == "severe_loss")

        Path(completed_job["expected_output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(completed_job["expected_output_wav_path"]).write_text("wav", encoding="utf-8")
        Path(completed_job["expected_output_metadata_path"]).write_text(
            _completed_clarity_result(completed_job),
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
        return {
            "status": "submitted",
            "command": ["uv", "run", "--project", "backend", "clarity-backend", "run-manifest", manifest_path.as_posix()],
            "message": "submitted with mixed results",
        }

    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_handoff.submit_clarity_manifest", fake_submit)

    summary = run_clarity_handoff(config_path, submit=True)
    records = load_clarity_index(layout["clarity_index_path"])

    assert summary["submitted"] is True
    assert summary["completed_jobs"] == 1
    assert summary["failed_jobs"] == 1
    assert summary["batch_status"] == "partial"
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["status"] == "completed"
    assert records["clarity__scene_static_0001__binaural_hrtf__severe_loss"]["status"] == "failed"
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["output_dir"] != records["clarity__scene_static_0001__binaural_hrtf__severe_loss"]["output_dir"]
    assert Path(records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["output_dir"]).parts[-2:] == ("binaural_hrtf", "mild_loss")
    assert Path(records["clarity__scene_static_0001__binaural_hrtf__severe_loss"]["output_dir"]).parts[-2:] == ("binaural_hrtf", "severe_loss")
    assert json.loads(
        Path(layout["degraded_output_root"] / "binaural_hrtf" / "severe_loss" / "scene_static_0001__binaural_hrtf.json").read_text(
            encoding="utf-8"
        )
    ) == {
        "schema_version": "1.0",
        "job_id": "clarity__scene_static_0001__binaural_hrtf__severe_loss",
        "variant_id": "scene_static_0001__binaural_hrtf",
        "scene_id": "scene_static_0001",
        "output_type": "binaural_hrtf",
        "hearing_profile_id": "severe_loss",
        "status": "failed",
        "input_wav_path": str(layout["render_outputs_root"] / "binaural_hrtf" / "scene_static_0001__binaural_hrtf.wav"),
        "input_render_metadata_path": str(
            layout["render_outputs_root"] / "binaural_hrtf" / "scene_static_0001__binaural_hrtf__render.json"
        ),
        "output_wav_path": None,
        "error": {"type": "RuntimeError", "message": "boom"},
    }


def _prepare_completed_render_variant(config_path: Path) -> dict[str, Path]:
    from acoustic_orchestrator.config.loader import load_config

    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    layout["render_index_path"].parent.mkdir(parents=True, exist_ok=True)
    variant_record = _completed_variant_record(layout)
    layout["render_index_path"].write_text(json.dumps(variant_record) + "\n", encoding="utf-8")
    Path(variant_record["rendered_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(variant_record["rendered_wav_path"]).write_text("wav", encoding="utf-8")
    Path(variant_record["render_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(variant_record["render_metadata_path"]).write_text("{}", encoding="utf-8")
    Path(config.hearing_degradation.hearing_profiles_path).write_text(_hearing_profiles_yaml(), encoding="utf-8")
    return layout


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _write_config(
    tmp_path: Path,
    backend_project_path: str | None = None,
    *,
    resume_if_possible: bool = True,
    force_rerun: bool = False,
    auto_submit: bool = False,
    save_render_metadata: bool = False,
) -> Path:
    artifact_root = tmp_path / "artifacts"
    assets_root = tmp_path / "assets"
    hearing_profiles = tmp_path / "hearing_profiles.yaml"
    backend_project_line = (
        f"    backend_project_path: {backend_project_path}\n" if backend_project_path is not None else ""
    )
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        (
            "experiment:\n"
            "  experiment_id: sim_test\n"
            "  description: test\n"
            "  scene_type: static\n"
            "  random_seed: 1\n"
            "execution:\n"
            "  num_simulations: 1\n"
            "  num_workers: 1\n"
            "  overwrite_existing: true\n"
            f"  resume_if_possible: {str(resume_if_possible).lower()}\n"
            "  save_scene_manifest: true\n"
            f"  save_render_metadata: {str(save_render_metadata).lower()}\n"
            "raven:\n"
            f"  base_rpf_file: {tmp_path.as_posix()}/base_room.rpf\n"
            "render:\n"
            "  sample_rate_hz: 44100\n"
            "receiver_sampling:\n"
            "  one_receiver_per_scene: true\n"
            "  position_strategy:\n"
            "    type: random_uniform_inside_room\n"
            "    margin_m: {x: 0.1, y: 0.1, z: 0.1}\n"
            "    fixed_height_m: {min: 1.2, max: 1.3}\n"
            "  orientation_strategy:\n"
            "    type: random_yaw\n"
            "    yaw_deg: {min: -180.0, max: 180.0}\n"
            "    pitch_deg: {fixed: 0.0}\n"
            "    roll_deg: {fixed: 0.0}\n"
            "receiver_outputs:\n"
            f"  binaural_hrtf:\n    enabled: true\n    ir_catalog_path: {assets_root.as_posix()}/hrtf\n    file_pattern: '*.daff'\n    output_subdir: binaural_hrtf\n    num_channels: 2\n    required: true\n"
            "room_sampling:\n"
            "  dimensions_m:\n"
            "    length: {min: 4.0, max: 4.5}\n"
            "    width: {min: 3.0, max: 3.5}\n"
            "    height: {min: 2.4, max: 2.8}\n"
            "  materials:\n"
            "    walls: [brick]\n"
            "    floor: [wood]\n"
            "    ceiling: [plaster]\n"
            "  semantic_surfaces:\n"
            "    enable_walls: true\n"
            "    enable_floor: true\n"
            "    enable_ceiling: true\n"
            "source_sampling:\n"
            "  min_sources: 2\n"
            "  max_sources: 2\n"
            "  timing:\n"
            "    allow_offsets: false\n"
            "    start_time_s: {min: 0.0, max: 0.0}\n"
            "  gain_db: {min: 0.0, max: 0.0}\n"
            "  default_orientation_strategy:\n"
            "    type: random_yaw\n"
            "    yaw_deg: {min: -180.0, max: 180.0}\n"
            "    pitch_deg: {fixed: 0.0}\n"
            "    roll_deg: {fixed: 0.0}\n"
            "  source_types:\n"
            f"    - event_type: speech\n      role: base\n      min_count: 2\n      max_count: 2\n      probability: 1.0\n      audio_dir: {assets_root.as_posix()}/audio\n      spatial_policy:\n        type: random_valid\n"
            "scene_validation:\n"
            "  min_distance_source_to_receiver_m: 0.5\n"
            "  min_distance_between_sources_m: 0.1\n"
            "  require_sources_inside_room: true\n"
            "  require_receiver_inside_room: true\n"
            "  max_sampling_attempts_per_scene: 5\n"
            "outputs:\n"
            f"  artifact_root: {artifact_root.as_posix()}\n"
            "  naming:\n"
            "    scene_id_prefix: scene_static\n"
            "    wav_pattern: '{scene_id}__{output_type}.wav'\n"
            "    metadata_pattern: '{scene_id}__{output_type}__render.json'\n"
            "hearing_degradation:\n"
            "  enabled: true\n"
            "  input_targets: [binaural_hrtf]\n"
            f"  hearing_profiles_path: {hearing_profiles.as_posix()}\n"
            "  runner:\n"
            f"{backend_project_line}"
            "    entrypoint: clarity-backend\n"
            f"    auto_submit: {str(auto_submit).lower()}\n"
            f"    force_rerun: {str(force_rerun).lower()}\n"
            "    use_uv: true\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "base_room.rpf").write_text("rpf", encoding="utf-8")
    (assets_root / "hrtf").mkdir(parents=True, exist_ok=True)
    (assets_root / "hrtf" / "sample.daff").write_text("daff", encoding="utf-8")
    (assets_root / "audio").mkdir(parents=True, exist_ok=True)
    (assets_root / "audio" / "sample.wav").write_text("wav", encoding="utf-8")
    for material_id in ["brick", "wood", "plaster"]:
        material_dir = assets_root / "materials" / material_id
        material_dir.mkdir(parents=True, exist_ok=True)
        (material_dir / "sample.mat").write_text(_material_file_text(), encoding="utf-8")
    if backend_project_path is not None:
        backend_dir = (tmp_path / backend_project_path).resolve()
        backend_dir.mkdir(parents=True, exist_ok=True)
        (backend_dir / "pyproject.toml").write_text("[project]\nname='clarity-backend'\nversion='0.1.0'\n", encoding="utf-8")
    return config_path


def _completed_variant_record(layout: dict[str, Path]) -> dict[str, object]:
    return {
        "variant_id": "scene_static_0001__binaural_hrtf",
        "scene_id": "scene_static_0001",
        "output_type": "binaural_hrtf",
        "status": "completed",
        "resumed": False,
        "attempted": True,
        "metadata_required": False,
        "runtime_manifest_path": str(layout["runtime_manifest_dir"] / "scene_static_0001__binaural_hrtf.json"),
        "rendered_wav_path": str(layout["render_outputs_root"] / "binaural_hrtf" / "scene_static_0001__binaural_hrtf.wav"),
        "render_metadata_path": str(
            layout["render_outputs_root"] / "binaural_hrtf" / "scene_static_0001__binaural_hrtf__render.json"
        ),
        "receiver_ir_path": str(layout["run_root"] / "receiver.daff"),
        "observed_outputs": {"runtime_manifest": True, "rendered_wav": True, "render_metadata": True},
        "metadata_snapshot": None,
        "validation": {"ok": True, "issues": []},
        "updated_at": "2026-04-24T00:00:00+00:00",
    }


def _material_file_text() -> str:
    absorp = ", ".join(["0.2"] * 31)
    scatter = ", ".join(["0.1"] * 31)
    return f"[Material]\nname=test\nnotes=test\nabsorp={absorp}\nscatter={scatter}\n"


def _hearing_profiles_yaml(profile_ids: list[str] | None = None) -> str:
    if profile_ids is None:
        profile_ids = ["mild_loss"]

    lines = ["profiles:"]
    for index, profile_id in enumerate(profile_ids, start=1):
        lines.extend(
            [
                f"  - hearing_profile_id: {profile_id}",
                "    ears:",
                "      left:",
                "        loss_db_by_band:",
                f"          250: {9 + index}",
                "      right:",
                "        loss_db_by_band:",
                f"          250: {11 + index}",
            ]
        )
    return "\n".join(lines)


def _completed_clarity_result(job: dict[str, object]) -> str:
    return json.dumps(
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
    )
