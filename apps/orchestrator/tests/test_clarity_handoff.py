import json
from pathlib import Path

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline.clarity_handoff import build_clarity_job_record, get_clarity_job_issues, prepare_clarity_handoff
from acoustic_orchestrator.pipeline.output_index import load_clarity_index
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout


def test_build_clarity_job_record_uses_default_reserved_output_layout(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    variant_record = _completed_variant_record(layout)

    job = build_clarity_job_record(
        config=config,
        layout=layout,
        variant_record=variant_record,
        hearing_profile_id="mild_loss",
    )

    assert job["job_id"] == "clarity__scene_static_0001__binaural_hrtf__mild_loss"
    assert job["run_id"] == "sim_test"
    assert job["hearing_profile_id"] == "mild_loss"
    assert job["backend_invocation"] == {
        "mode": "uv_project",
        "entrypoint": "clarity-backend",
        "use_uv": True,
        "backend_project_path": None,
    }
    assert Path(job["output_dir"]).parts[-2:] == (
        "binaural_hrtf",
        "mild_loss",
    )
    assert Path(job["output_dir"]).parts[-3] == "degraded"
    assert Path(job["expected_output_wav_path"]).name == "scene_static_0001__binaural_hrtf.wav"
    assert Path(job["expected_output_metadata_path"]).name == "scene_static_0001__binaural_hrtf.json"


def test_build_clarity_job_record_respects_output_override(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, clarity_output_override="./custom_clarity")
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    job = build_clarity_job_record(
        config=config,
        layout=layout,
        variant_record=_completed_variant_record(layout),
        hearing_profile_id="mild_loss",
    )

    assert "custom_clarity" in Path(job["output_dir"]).parts


def test_get_clarity_job_issues_filters_non_targeted_and_missing_inputs(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    variant_record = _completed_variant_record(layout)
    variant_record["output_type"] = "bte_rear_hartf"
    variant_record["variant_id"] = "scene_static_0001__bte_rear_hartf"
    job = build_clarity_job_record(
        config=config,
        layout=layout,
        variant_record=variant_record,
        hearing_profile_id="mild_loss",
    )

    issues = get_clarity_job_issues(config=config, variant_record=variant_record, job=job)

    assert "output_type_not_targeted" in issues


def test_prepare_clarity_handoff_expands_variant_per_profile_and_skips_invalid_variants(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    layout["render_index_path"].parent.mkdir(parents=True, exist_ok=True)
    layout["render_index_path"].write_text(
        "\n".join(
            [
                json.dumps(_completed_variant_record(layout)),
                json.dumps(_partial_variant_record(layout)),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    Path(_completed_variant_record(layout)["rendered_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(_completed_variant_record(layout)["rendered_wav_path"]).write_text("wav", encoding="utf-8")
    Path(_completed_variant_record(layout)["render_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(_completed_variant_record(layout)["render_metadata_path"]).write_text("{}", encoding="utf-8")
    Path(config.hearing_degradation.hearing_profiles_path).write_text(_hearing_profiles_yaml(), encoding="utf-8")

    summary = prepare_clarity_handoff(config, submit=False)
    records = load_clarity_index(layout["clarity_index_path"])

    assert summary["planned_jobs"] == 2
    assert summary["skipped_jobs"] == 2
    assert layout["clarity_jobs_path"].is_file()
    manifest_rows = [json.loads(line) for line in layout["clarity_jobs_path"].read_text(encoding="utf-8").splitlines()]
    assert len(manifest_rows) == 2
    assert {row["job_id"] for row in manifest_rows} == {
        "clarity__scene_static_0001__binaural_hrtf__mild_loss",
        "clarity__scene_static_0001__binaural_hrtf__severe_loss",
    }
    for row in manifest_rows:
        assert set(row) == {
            "schema_version",
            "run_id",
            "job_id",
            "variant_id",
            "scene_id",
            "output_type",
            "hearing_profile_id",
            "hearing_profiles_path",
            "backend_invocation",
            "input_wav_path",
            "input_render_metadata_path",
            "output_dir",
            "expected_output_wav_path",
            "expected_output_metadata_path",
        }
        assert row["schema_version"] == "1.0"
        assert row["run_id"] == "sim_test"
        assert row["variant_id"] == "scene_static_0001__binaural_hrtf"
        assert row["scene_id"] == "scene_static_0001"
        assert row["output_type"] == "binaural_hrtf"
        assert row["hearing_profiles_path"] == Path(config.hearing_degradation.hearing_profiles_path).resolve().as_posix()
        assert row["input_wav_path"] == _completed_variant_record(layout)["rendered_wav_path"]
        assert row["input_render_metadata_path"] == _completed_variant_record(layout)["render_metadata_path"]
        assert row["backend_invocation"] == {
            "mode": "uv_project",
            "entrypoint": "clarity-backend",
            "use_uv": True,
            "backend_project_path": None,
        }
        assert Path(row["output_dir"]).parts[-2:] == ("binaural_hrtf", row["hearing_profile_id"])
        assert Path(row["expected_output_wav_path"]).name == "scene_static_0001__binaural_hrtf.wav"
        assert Path(row["expected_output_metadata_path"]).name == "scene_static_0001__binaural_hrtf.json"
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["status"] == "planned"
    assert records["clarity__scene_static_0001__binaural_hrtf__severe_loss"]["status"] == "planned"
    assert records["clarity__scene_static_0002__binaural_hrtf__mild_loss"]["status"] == "skipped"
    assert records["clarity__scene_static_0002__binaural_hrtf__severe_loss"]["status"] == "skipped"


def test_prepare_clarity_handoff_blocks_jobs_without_render_metadata(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    layout["render_index_path"].parent.mkdir(parents=True, exist_ok=True)
    layout["render_index_path"].write_text(json.dumps(_completed_variant_record(layout, render_metadata_path=None)) + "\n", encoding="utf-8")
    Path(_completed_variant_record(layout)["rendered_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(_completed_variant_record(layout)["rendered_wav_path"]).write_text("wav", encoding="utf-8")
    Path(config.hearing_degradation.hearing_profiles_path).write_text(_hearing_profiles_yaml(), encoding="utf-8")

    summary = prepare_clarity_handoff(config, submit=False)
    records = load_clarity_index(layout["clarity_index_path"])

    assert summary["planned_jobs"] == 0
    assert summary["blocked_jobs"] == 2
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["status"] == "blocked"
    assert records["clarity__scene_static_0001__binaural_hrtf__severe_loss"]["status"] == "blocked"


def test_prepare_clarity_handoff_persists_blocked_records_for_invalid_hearing_profile_catalog(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    layout["render_index_path"].parent.mkdir(parents=True, exist_ok=True)
    layout["render_index_path"].write_text(json.dumps(_completed_variant_record(layout)) + "\n", encoding="utf-8")
    Path(_completed_variant_record(layout)["rendered_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(_completed_variant_record(layout)["rendered_wav_path"]).write_text("wav", encoding="utf-8")
    Path(_completed_variant_record(layout)["render_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(_completed_variant_record(layout)["render_metadata_path"]).write_text("{}", encoding="utf-8")
    Path(config.hearing_degradation.hearing_profiles_path).write_text(
        "\n".join(
            [
                "profiles:",
                "  - hearing_profile_id: mild_loss",
                "    ears:",
                "      left:",
                "        loss_db_by_band:",
                "          250: 10",
            ]
        ),
        encoding="utf-8",
    )

    summary = prepare_clarity_handoff(config, submit=False)
    records = load_clarity_index(layout["clarity_index_path"])

    assert summary["planned_jobs"] == 0
    assert summary["blocked_jobs"] == 1
    assert summary["batch_status"] == "blocked"
    assert layout["clarity_jobs_path"].read_text(encoding="utf-8") == ""
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss"] == {
        "run_id": "sim_test",
        "job_id": "clarity__scene_static_0001__binaural_hrtf__mild_loss",
        "variant_id": "scene_static_0001__binaural_hrtf",
        "scene_id": "scene_static_0001",
        "output_type": "binaural_hrtf",
        "hearing_profile_id": "mild_loss",
        "status": "blocked",
        "resumed": False,
        "attempted": False,
        "runnable": False,
        "manifest_path": layout["clarity_jobs_path"].resolve().as_posix(),
        "input_wav_path": _completed_variant_record(layout)["rendered_wav_path"],
        "input_render_metadata_path": _completed_variant_record(layout)["render_metadata_path"],
        "hearing_profiles_path": Path(config.hearing_degradation.hearing_profiles_path).resolve().as_posix(),
        "backend_invocation": {
            "mode": "uv_project",
            "entrypoint": "clarity-backend",
            "use_uv": True,
            "backend_project_path": None,
        },
        "output_dir": (
            layout["degraded_output_root"] / "binaural_hrtf" / "mild_loss"
        ).resolve().as_posix(),
        "expected_output_wav_path": (
            layout["degraded_output_root"] / "binaural_hrtf" / "mild_loss" / "scene_static_0001__binaural_hrtf.wav"
        ).resolve().as_posix(),
        "expected_output_metadata_path": (
            layout["degraded_output_root"]
            / "binaural_hrtf"
            / "mild_loss"
            / "scene_static_0001__binaural_hrtf.json"
        ).resolve().as_posix(),
        "backend_command": None,
        "observed_outputs": {
            "expected_output_wav": False,
            "expected_output_metadata": False,
        },
        "validation": {
            "ok": False,
            "issues": [
                "invalid_hearing_profiles_catalog:profiles[1].ears.right debe ser un objeto"
            ],
        },
        "updated_at": records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["updated_at"],
    }


def test_prepare_clarity_handoff_reports_blocked_submission_without_backend(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    layout["render_index_path"].parent.mkdir(parents=True, exist_ok=True)
    layout["render_index_path"].write_text(json.dumps(_completed_variant_record(layout)) + "\n", encoding="utf-8")
    Path(_completed_variant_record(layout)["rendered_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(_completed_variant_record(layout)["rendered_wav_path"]).write_text("wav", encoding="utf-8")
    Path(_completed_variant_record(layout)["render_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(_completed_variant_record(layout)["render_metadata_path"]).write_text("{}", encoding="utf-8")
    Path(config.hearing_degradation.hearing_profiles_path).write_text(_hearing_profiles_yaml(profile_ids=["mild_loss"]), encoding="utf-8")
    monkeypatch.setattr(
        "acoustic_orchestrator.pipeline.clarity_handoff.submit_clarity_manifest",
        lambda runner, manifest_path: {"status": "blocked", "command": None, "message": "backend missing"},
    )

    summary = prepare_clarity_handoff(config, submit=True)
    records = load_clarity_index(layout["clarity_index_path"])

    assert summary["submitted"] is False
    assert summary["batch_status"] == "blocked"
    assert summary["blocked_jobs"] == 1
    assert summary["message"] == "backend missing"
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss"]["status"] == "blocked"


def _write_config(tmp_path: Path, clarity_output_override: str | None = None) -> Path:
    artifact_root = tmp_path / "artifacts"
    hearing_profiles = tmp_path / "hearing_profiles.yaml"
    output_dir_line = f"  output_dir: {clarity_output_override}\n" if clarity_output_override is not None else ""
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
            "  resume_if_possible: true\n"
            "  save_scene_manifest: true\n"
            "  save_render_metadata: false\n"
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
            f"  binaural_hrtf:\n    enabled: true\n    ir_catalog_path: {tmp_path.as_posix()}/hrtf\n    file_pattern: '*.daff'\n    output_subdir: binaural_hrtf\n    num_channels: 2\n    required: true\n"
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
            "  min_sources: 1\n"
            "  max_sources: 1\n"
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
            f"    - event_type: speech\n      role: base\n      min_count: 1\n      max_count: 1\n      probability: 1.0\n      audio_dir: {tmp_path.as_posix()}/audio\n      spatial_policy:\n        type: random_valid\n"
            "scene_validation:\n"
            "  min_distance_source_to_receiver_m: 0.1\n"
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
            f"{output_dir_line}"
            "  runner:\n"
            "    entrypoint: clarity-backend\n"
            "    auto_submit: false\n"
            "    use_uv: true\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "base_room.rpf").write_text("rpf", encoding="utf-8")
    (tmp_path / "hrtf").mkdir(exist_ok=True)
    (tmp_path / "hrtf" / "sample.daff").write_text("daff", encoding="utf-8")
    (tmp_path / "audio").mkdir(exist_ok=True)
    (tmp_path / "audio" / "sample.wav").write_text("wav", encoding="utf-8")
    return config_path


def _completed_variant_record(layout: dict[str, Path], render_metadata_path: str | None = "") -> dict:
    normalized_render_metadata_path = render_metadata_path
    if render_metadata_path == "":
        normalized_render_metadata_path = str(
            layout["render_outputs_root"] / "binaural_hrtf" / "scene_static_0001__binaural_hrtf__render.json"
        )
    return _completed_variant_record_with_metadata(layout, normalized_render_metadata_path)


def _completed_variant_record_with_metadata(layout: dict[str, Path], render_metadata_path: str | None) -> dict:
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
        "render_metadata_path": render_metadata_path,
        "receiver_ir_path": str(layout["run_root"] / "receiver.daff"),
        "observed_outputs": {
            "runtime_manifest": True,
            "rendered_wav": True,
            "render_metadata": render_metadata_path is not None,
        },
        "metadata_snapshot": None,
        "validation": {"ok": True, "issues": []},
        "updated_at": "2026-04-24T00:00:00+00:00",
    }


def _partial_variant_record(layout: dict[str, Path]) -> dict:
    record = _completed_variant_record(layout)
    record.update(
        {
            "variant_id": "scene_static_0002__binaural_hrtf",
            "scene_id": "scene_static_0002",
            "status": "partial",
            "rendered_wav_path": str(layout["render_outputs_root"] / "binaural_hrtf" / "scene_static_0002__binaural_hrtf.wav"),
        }
    )
    return record


def _hearing_profiles_yaml(profile_ids: list[str] | None = None) -> str:
    selected_profile_ids = profile_ids or ["mild_loss", "severe_loss"]
    profiles: list[str] = []
    for profile_id, base_loss in {
        "mild_loss": (10, 12),
        "severe_loss": (35, 40),
    }.items():
        if profile_id not in selected_profile_ids:
            continue
        left_loss, right_loss = base_loss
        profiles.append(
            "\n".join(
                [
                    f"  - hearing_profile_id: {profile_id}",
                    "    ears:",
                    "      left:",
                    "        loss_db_by_band:",
                    f"          250: {left_loss}",
                    "      right:",
                    "        loss_db_by_band:",
                    f"          250: {right_loss}",
                ]
            )
        )
    return "profiles:\n" + "\n".join(profiles)
