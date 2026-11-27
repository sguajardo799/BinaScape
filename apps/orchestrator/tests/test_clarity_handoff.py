import json
from pathlib import Path
import pytest
from render_fixtures import write_variant_fixture

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline.clarity_handoff import build_clarity_job_record, get_clarity_job_issues, prepare_clarity_handoff
from acoustic_orchestrator.pipeline.level_calibration import load_level_calibration_index
from acoustic_orchestrator.pipeline.output_index import load_clarity_index
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout


@pytest.mark.parametrize("output_type", ["circular_array", "bte_rear_hartf", "bte_front_hartf", "binaural_hrtf__1"])
def test_runtime_eligibility_independent_of_config_targets(tmp_path: Path, output_type: str) -> None:
    config = load_config(_write_config(tmp_path))
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    variant = _completed_variant_record(layout)
    variant["output_type"] = output_type
    variant["variant_id"] = f"{variant['scene_id']}__{output_type}"
    config.hearing_degradation.input_targets = [output_type, "binaural_hrtf"]
    job = build_clarity_job_record(config=config, layout=layout, variant_record=variant, hearing_profile_id="mild_loss")
    issues = get_clarity_job_issues(config=config, variant_record=variant, job=job)
    assert any(issue.startswith("output_type_ineligible") for issue in issues) == (output_type != "binaural_hrtf__1")


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

    assert job["job_id"] == "clarity__scene_static_0001__binaural_hrtf__mild_loss__msbg"
    assert job["run_id"] == "sim_test"
    assert job["hearing_profile_id"] == "mild_loss"
    assert job["backend_invocation"] == {
        "mode": "uv_project",
        "entrypoint": "clarity-backend",
        "use_uv": True,
        "backend_project_path": None,
    }
    assert Path(job["output_dir"]).parts[-4:] == ("degraded", "msbg", "binaural_hrtf", "mild_loss")
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
    write_variant_fixture(_completed_variant_record(layout), metadata=True)
    Path(config.hearing_degradation.hearing_profiles_path).write_text(_hearing_profiles_yaml(), encoding="utf-8")

    summary = prepare_clarity_handoff(config, submit=False)
    records = load_clarity_index(layout["clarity_index_path"])

    assert summary["planned_jobs"] == 2
    assert summary["skipped_jobs"] == 2
    assert layout["clarity_jobs_path"].is_file()
    manifest_rows = [json.loads(line) for line in layout["clarity_jobs_path"].read_text(encoding="utf-8").splitlines()]
    assert len(manifest_rows) == 2
    assert {row["job_id"] for row in manifest_rows} == {
        "clarity__scene_static_0001__binaural_hrtf__mild_loss__msbg",
        "clarity__scene_static_0001__binaural_hrtf__severe_loss__msbg",
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
            "processor",
            "calibration",
            "outer_middle_ear",
            "processor_config",
            "processing_signature",
        }
        assert row["processor"] == "msbg"
        assert row["outer_middle_ear"] == "free_field"
        assert row["processor_config"] == {"level_handling": "true_level"}
        assert row["calibration"]["effective_scale_db_spl"] == 90.0
        assert row["calibration"]["is_default"] is True
        assert row["processing_signature"]["calibration"]["is_default"] is True
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
        assert Path(row["output_dir"]).parts[-3:] == ("msbg", "binaural_hrtf", row["hearing_profile_id"])
        assert Path(row["expected_output_wav_path"]).name == "scene_static_0001__binaural_hrtf.wav"
        assert Path(row["expected_output_metadata_path"]).name == "scene_static_0001__binaural_hrtf.json"
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss__msbg"]["status"] == "planned"
    assert records["clarity__scene_static_0001__binaural_hrtf__severe_loss__msbg"]["status"] == "planned"
    assert records["clarity__scene_static_0002__binaural_hrtf__mild_loss__msbg"]["status"] == "skipped"
    assert records["clarity__scene_static_0002__binaural_hrtf__severe_loss__msbg"]["status"] == "skipped"


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
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss__msbg"]["status"] == "blocked"
    assert records["clarity__scene_static_0001__binaural_hrtf__severe_loss__msbg"]["status"] == "blocked"


def test_prepare_clarity_handoff_persists_blocked_records_for_invalid_hearing_profile_catalog(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    layout["render_index_path"].parent.mkdir(parents=True, exist_ok=True)
    layout["render_index_path"].write_text(json.dumps(_completed_variant_record(layout)) + "\n", encoding="utf-8")
    write_variant_fixture(_completed_variant_record(layout), metadata=True)
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
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss__msbg"] == {
        "run_id": "sim_test",
        "job_id": "clarity__scene_static_0001__binaural_hrtf__mild_loss__msbg",
        "variant_id": "scene_static_0001__binaural_hrtf",
        "scene_id": "scene_static_0001",
        "output_type": "binaural_hrtf",
        "hearing_profile_id": "mild_loss",
        "processor": "msbg",
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
            layout["degraded_output_root"] / "msbg" / "binaural_hrtf" / "mild_loss"
        ).resolve().as_posix(),
        "expected_output_wav_path": (
            layout["degraded_output_root"] / "msbg" / "binaural_hrtf" / "mild_loss" / "scene_static_0001__binaural_hrtf.wav"
        ).resolve().as_posix(),
        "expected_output_metadata_path": (
            layout["degraded_output_root"]
            / "msbg"
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
        "updated_at": records["clarity__scene_static_0001__binaural_hrtf__mild_loss__msbg"]["updated_at"],
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
    write_variant_fixture(_completed_variant_record(layout), metadata=True)
    Path(config.hearing_degradation.hearing_profiles_path).write_text(_hearing_profiles_yaml(profile_ids=["mild_loss"]), encoding="utf-8")
    monkeypatch.setattr(
        "acoustic_orchestrator.pipeline.clarity_handoff.submit_clarity_manifest",
        lambda runner, manifest_path, **kwargs: {"status": "blocked", "command": None, "message": "backend missing"},
    )

    summary = prepare_clarity_handoff(config, submit=True)
    records = load_clarity_index(layout["clarity_index_path"])

    assert summary["submitted"] is False
    assert summary["batch_status"] == "blocked"
    assert summary["blocked_jobs"] == 1
    assert summary["message"] == "backend missing"
    assert records["clarity__scene_static_0001__binaural_hrtf__mild_loss__msbg"]["status"] == "blocked"


def _write_config(tmp_path: Path, clarity_output_override: str | None = None, hearing_extra: str = "") -> Path:
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
            "    margin_m: {x: 0.5, y: 0.1, z: 0.5}\n"
            "    fixed_height_m: {min: 1.2, max: 1.3}\n"
            "  orientation_strategy:\n"
            "    type: random_yaw\n"
            "    yaw_deg: {min: -180.0, max: 180.0}\n"
            "    pitch_deg: {fixed: 0.0}\n"
            "    roll_deg: {fixed: 0.0}\n"
            "receiver_outputs:\n"
            f"  binaural_hrtf:\n    enabled: true\n    ir_catalog_path: {tmp_path.as_posix()}/hrtf\n    file_pattern: '*.daff'\n    output_subdir: binaural_hrtf\n    num_channels: 2\n    required: true\n"
            "room_sampling:\n"
            "  reverberation:\n"
            "    metric: estimated_rt30_s\n    estimator: sabine\n    estimator_version: sabine_polygon_octaves_v4\n    aggregation: arithmetic_mean\n"
            "    mean_bands_hz: [125, 250, 500, 1000, 2000, 4000, 8000]\n"
            "    distribution: {type: uniform, scope: global, range_s: {min: 0.1, max: 1.2}, bin_width_s: 0.1, quota_tolerance_fraction: 0.10}\n"
            "    treatment: {catalog_version: 1, mix_model: area_weighted_linear, mix_model_version: 1, eligible_surface_types: [wall, ceiling], max_treatments_per_surface: 1, preserve_base_scattering: true, allow_none: true, wall_coverage: {min: 0.0, max: 1.0}, ceiling_coverage: {min: 0.0, max: 1.0}}\n"
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
            f"{hearing_extra}"
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


def _hearing_profiles_yaml(profile_ids: list[str] | None = None, *, compression_health: bool = False) -> str:
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
                    *(["        compression_health: 1"] if compression_health else []),
                    "      right:",
                    "        loss_db_by_band:",
                    f"          250: {right_loss}",
                    *(["        compression_health: 0.5"] if compression_health else []),
                ]
            )
        )
    return "profiles:\n" + "\n".join(profiles)


WHIS_HEARING = (
    "  processors: [msbg, whis]\n"
    "  calibration: {mode: presentation_level, presentation_level_range_db_spl: [55, 80]}\n"
    "  outer_middle_ear: itu\n"
    "  whis: {install_root: ./whis}\n"
)


def _prepare_whis_run(tmp_path: Path, hearing_extra: str = WHIS_HEARING):
    config = load_config(_write_config(tmp_path, hearing_extra=hearing_extra))
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    layout["render_index_path"].parent.mkdir(parents=True, exist_ok=True)
    layout["render_index_path"].write_text(json.dumps(_completed_variant_record(layout)) + "\n", encoding="utf-8")
    write_variant_fixture(_completed_variant_record(layout), metadata=True)
    Path(config.hearing_degradation.hearing_profiles_path).write_text(
        _hearing_profiles_yaml(compression_health=True), encoding="utf-8",
    )
    return config, layout


def _manifest_rows(layout) -> list[dict]:
    return [json.loads(line) for line in layout["clarity_jobs_path"].read_text(encoding="utf-8").splitlines()]


def test_prepare_clarity_handoff_plans_one_job_per_profile_and_processor_sharing_k(tmp_path: Path) -> None:
    config, layout = _prepare_whis_run(tmp_path)

    summary = prepare_clarity_handoff(config, submit=False)
    rows = _manifest_rows(layout)

    assert summary["planned_jobs"] == 4
    assert {row["job_id"] for row in rows} == {
        f"clarity__scene_static_0001__binaural_hrtf__{profile}__{processor}"
        for profile in ("mild_loss", "severe_loss") for processor in ("msbg", "whis")
    }
    calibrations = [row["calibration"] for row in rows]
    assert all(calibration == calibrations[0] for calibration in calibrations)
    assert 55 <= calibrations[0]["presentation_level_db_spl"] <= 80
    assert calibrations[0]["effective_scale_db_spl"] is not None
    assert {row["outer_middle_ear"] for row in rows} == {"itu"}
    assert len({row["output_dir"] for row in rows}) == 4

    index = load_level_calibration_index(layout["level_calibration_index_path"])
    assert index == {"scene_static_0001__binaural_hrtf": calibrations[0]}


def test_whis_job_carries_processor_config_and_signature(tmp_path: Path) -> None:
    config, layout = _prepare_whis_run(tmp_path)

    prepare_clarity_handoff(config, submit=False)
    whis_job = next(row for row in _manifest_rows(layout) if row["processor"] == "whis")
    msbg_job = next(row for row in _manifest_rows(layout) if row["processor"] == "msbg")

    assert whis_job["processor_config"] == {
        "install_root": (tmp_path / "whis").resolve().as_posix(),
        "matlab_executable": "matlab",
        "synth_method": "dtvf",
    }
    assert whis_job["processing_signature"] == {
        "processor": "whis",
        "calibration": {
            "mode": "presentation_level",
            "configured": {"presentation_level_range_db_spl": [55.0, 80.0]},
            "is_default": False,
        },
        "random_seed": 1,
        "outer_middle_ear": "itu",
        "whis": {"synth_method": "dtvf"},
    }
    assert msbg_job["processing_signature"]["msbg"] == {"level_handling": "true_level"}
    assert Path(whis_job["output_dir"]).parts[-3:] == ("whis", "binaural_hrtf", whis_job["hearing_profile_id"])


def test_silent_render_with_presentation_level_blocks_all_its_jobs(tmp_path: Path) -> None:
    config, layout = _prepare_whis_run(tmp_path)
    import soundfile as sf
    import numpy as np

    sf.write(_completed_variant_record(layout)["rendered_wav_path"], np.zeros((200, 2)), 44100, subtype="PCM_16")
    from acoustic_orchestrator.pipeline import clarity_handoff

    original = clarity_handoff._revalidate_render_input
    clarity_handoff._revalidate_render_input = lambda *args, **kwargs: []
    try:
        summary = prepare_clarity_handoff(config, submit=False)
    finally:
        clarity_handoff._revalidate_render_input = original
    records = load_clarity_index(layout["clarity_index_path"])

    assert summary["planned_jobs"] == 0
    assert summary["blocked_jobs"] == 4
    assert all(record["validation"]["issues"] == ["calibration_undefined_silent_input"] for record in records.values())
    calibration = load_level_calibration_index(layout["level_calibration_index_path"])["scene_static_0001__binaural_hrtf"]
    assert calibration["status"] == "undefined_silent_input"


def _complete_jobs(rows: list[dict]) -> None:
    for row in rows:
        Path(row["expected_output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(row["expected_output_wav_path"]).write_text("wav", encoding="utf-8")
        Path(row["expected_output_metadata_path"]).write_text(json.dumps({
            "schema_version": "1.0",
            **{key: row[key] for key in ("job_id", "variant_id", "scene_id", "output_type", "hearing_profile_id",
                                          "input_wav_path", "input_render_metadata_path", "processing_signature",
                                          "calibration")},
            "status": "completed",
            "output_wav_path": row["expected_output_wav_path"],
            "processor": {"name": row["processor"]},
            "degradation_applied": {"left": {}, "right": {}},
        }), encoding="utf-8")


def test_adding_whis_to_a_completed_msbg_run_only_plans_whis_jobs(tmp_path: Path) -> None:
    msbg_only = "  calibration: {mode: presentation_level, presentation_level_range_db_spl: [55, 80]}\n  outer_middle_ear: itu\n"
    config, layout = _prepare_whis_run(tmp_path, hearing_extra=msbg_only)
    prepare_clarity_handoff(config, submit=False)
    _complete_jobs(_manifest_rows(layout))

    config = load_config(_write_config(tmp_path, hearing_extra=WHIS_HEARING))
    summary = prepare_clarity_handoff(config, submit=False)
    rows = _manifest_rows(layout)

    assert {row["processor"] for row in rows} == {"whis"}
    assert summary["planned_jobs"] == 2
    assert summary["resumed_jobs"] == 2


def test_changing_the_seed_with_a_level_range_reuses_nothing(tmp_path: Path) -> None:
    config, layout = _prepare_whis_run(tmp_path)
    prepare_clarity_handoff(config, submit=False)
    first_k = _manifest_rows(layout)[0]["calibration"]["effective_scale_db_spl"]
    _complete_jobs(_manifest_rows(layout))

    config.experiment.random_seed = 2
    summary = prepare_clarity_handoff(config, submit=False)

    assert summary["planned_jobs"] == 4 and summary["resumed_jobs"] == 0
    assert _manifest_rows(layout)[0]["calibration"]["effective_scale_db_spl"] != first_k


def test_resuming_with_another_outer_middle_ear_reuses_nothing(tmp_path: Path) -> None:
    config, layout = _prepare_whis_run(tmp_path)
    prepare_clarity_handoff(config, submit=False)
    _complete_jobs(_manifest_rows(layout))

    assert prepare_clarity_handoff(config, submit=False)["resumed_jobs"] == 4
    config.hearing_degradation.outer_middle_ear = "free_field"
    summary = prepare_clarity_handoff(config, submit=False)

    assert summary["planned_jobs"] == 4 and summary["resumed_jobs"] == 0


def test_previous_layout_outputs_and_records_are_not_reused(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    layout["render_index_path"].parent.mkdir(parents=True, exist_ok=True)
    layout["render_index_path"].write_text(json.dumps(_completed_variant_record(layout)) + "\n", encoding="utf-8")
    write_variant_fixture(_completed_variant_record(layout), metadata=True)
    Path(config.hearing_degradation.hearing_profiles_path).write_text(_hearing_profiles_yaml(["mild_loss"]), encoding="utf-8")
    legacy_dir = layout["degraded_output_root"] / "binaural_hrtf" / "mild_loss"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "scene_static_0001__binaural_hrtf.wav").write_text("wav", encoding="utf-8")
    (legacy_dir / "scene_static_0001__binaural_hrtf.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    legacy_record = {
        "job_id": "clarity__scene_static_0001__binaural_hrtf__mild_loss", "variant_id": "scene_static_0001__binaural_hrtf",
        "scene_id": "scene_static_0001", "output_type": "binaural_hrtf", "hearing_profile_id": "mild_loss",
        "status": "completed", "manifest_path": "m", "input_wav_path": "i", "hearing_profiles_path": "h",
        "output_dir": legacy_dir.as_posix(), "expected_output_wav_path": "w", "expected_output_metadata_path": "j",
    }
    layout["clarity_index_path"].write_text(json.dumps(legacy_record) + "\n", encoding="utf-8")

    summary = prepare_clarity_handoff(config, submit=False)
    records = load_clarity_index(layout["clarity_index_path"])

    assert summary["planned_jobs"] == 1 and summary["resumed_jobs"] == 0
    assert set(records) == {"clarity__scene_static_0001__binaural_hrtf__mild_loss__msbg"}
    assert "/degraded/msbg/" in _manifest_rows(layout)[0]["output_dir"]


def test_whis_jobs_withheld_by_preflight_are_blocked_while_msbg_runs(tmp_path: Path, monkeypatch) -> None:
    config, layout = _prepare_whis_run(tmp_path)
    submitted: list[list[str]] = []

    def fake_submit(runner, manifest_path, **kwargs):
        rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()]
        whis_ids = [row["job_id"] for row in rows if row["processor"] == "whis"]
        submitted.append([row["job_id"] for row in rows if row["processor"] == "msbg"])
        return {"status": "submitted", "command": ["backend"], "message": "ok",
                "blocked_jobs": {job_id: "WHIS no disponible: falta GCFBv234" for job_id in whis_ids}}

    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_handoff.submit_clarity_manifest", fake_submit)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_handoff._revalidate_render_input", lambda *a, **k: [])

    summary = prepare_clarity_handoff(config, submit=True)
    records = load_clarity_index(layout["clarity_index_path"])

    assert len(submitted[0]) == 2
    assert summary["blocked_jobs"] == 2
    for job_id, record in records.items():
        if job_id.endswith("__whis"):
            assert record["status"] == "blocked"
            assert record["validation"]["issues"] == ["WHIS no disponible: falta GCFBv234"]
        else:
            assert record["status"] != "blocked" and record["attempted"] is True
