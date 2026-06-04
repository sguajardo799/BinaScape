from collections import Counter
import json
import math
from pathlib import Path
import wave

import pytest
from typer.testing import CliRunner

import acoustic_orchestrator.pipeline.render_pipeline as render_pipeline
from acoustic_orchestrator.cli import app
from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.validator import validate_config
from acoustic_orchestrator.pipeline.matlab_runner import build_raven_project_name, build_render_variant_paths
from acoustic_orchestrator.pipeline.render_pipeline import (
    RenderStaticRunError,
    RenderSummary,
    generate_static_manifests,
    render_static_scenes,
)
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout


def test_generate_static_manifests_is_deterministic(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    first_config = _write_config(workspace, "run_a")
    second_config = _write_config(workspace, "run_b")

    first_paths = generate_static_manifests(first_config)
    second_paths = generate_static_manifests(second_config)

    assert len(first_paths) == 2
    assert len(second_paths) == 2
    assert [path.name for path in first_paths] == ["scene_static_0001.json", "scene_static_0002.json"]
    assert [json.loads(path.read_text(encoding="utf-8")) for path in first_paths] == [
        json.loads(path.read_text(encoding="utf-8")) for path in second_paths
    ]

    manifest = json.loads(first_paths[0].read_text(encoding="utf-8"))
    assert manifest["scene_type"] == "static"
    assert len(manifest["receiver"]["hrtfs"]) == 3
    assert len(manifest["sources"]) >= 2
    assert manifest["render"]["seed"] == 123
    assert Path(manifest["receiver"]["hrtfs"][0]["hrtf_path"]).is_absolute()
    assert Path(manifest["sources"][0]["audio_path"]).is_absolute()
    speech_source = next(source for source in manifest["sources"] if source["event_type"] == "speech")
    assert Path(speech_source["directivity_path"]).is_absolute()
    assert list(manifest["room"]["material_files"]) == [
        "north_wall",
        "south_wall",
        "east_wall",
        "west_wall",
        "floor",
        "ceiling",
    ]
    assert Path(manifest["room"]["material_files"]["north_wall"]["material_path"]).is_absolute()
    assert Path(manifest["render"]["output_wav_path"]).is_absolute()
    assert Path(manifest["render"]["output_metadata_path"]).is_absolute()
    assert Path(manifest["render"]["output_wav_path"]).parts[-2:] == ("binaural_hrtf", "scene_static_0001__binaural_hrtf.wav")
    assert Path(manifest["render"]["output_metadata_path"]).parts[-2:] == (
        "binaural_hrtf",
        "scene_static_0001__binaural_hrtf__render.json",
    )
    assert Path(manifest["render"]["output_wav_path"]).parts[-6:-2] == ("artifacts", "sim_test", "outputs", "render")
    assert manifest["room"]["material_files"] == json.loads(second_paths[0].read_text(encoding="utf-8"))["room"]["material_files"]
    assert manifest["background_noise"] == {"enabled": False, "layers": []}


def test_background_noise_does_not_perturb_existing_static_sampling(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    disabled_config = _write_config(workspace, "noise_disabled", num_simulations=4)
    enabled_config = _write_config(workspace, "noise_enabled", num_simulations=4, background_noise_block=_background_noise_block())

    disabled_manifests = [json.loads(path.read_text(encoding="utf-8")) for path in generate_static_manifests(disabled_config)]
    enabled_manifests = [json.loads(path.read_text(encoding="utf-8")) for path in generate_static_manifests(enabled_config)]

    for disabled_manifest, enabled_manifest in zip(disabled_manifests, enabled_manifests, strict=True):
        disabled_without_noise = {key: value for key, value in disabled_manifest.items() if key != "background_noise"}
        enabled_without_noise = {key: value for key, value in enabled_manifest.items() if key != "background_noise"}
        assert enabled_without_noise == disabled_without_noise
        assert enabled_manifest["background_noise"]["enabled"] is True


def test_generate_static_manifests_allows_omitting_optional_hartf_outputs(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "only_hrtf", include_optional_hartf_outputs=False)

    manifest_paths = generate_static_manifests(config_path)

    assert len(manifest_paths) == 2
    manifest = json.loads(manifest_paths[0].read_text(encoding="utf-8"))
    assert [output["hrtf_id"] for output in manifest["receiver"]["hrtfs"]] == ["binaural_hrtf"]


def test_background_noise_folder_layers_use_concrete_absolute_paths_and_are_deterministic(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path, noise_wav_names=["b_noise.wav", "a_noise.wav"])
    first_config = _write_config(workspace, "noise_a", num_simulations=6, background_noise_block=_background_noise_block())
    second_config = _write_config(workspace, "noise_b", num_simulations=6, background_noise_block=_background_noise_block())

    first_layers = [json.loads(path.read_text(encoding="utf-8"))["background_noise"]["layers"] for path in generate_static_manifests(first_config)]
    second_layers = [json.loads(path.read_text(encoding="utf-8"))["background_noise"]["layers"] for path in generate_static_manifests(second_config)]

    assert first_layers == second_layers
    folder_layers = [layers[0] for layers in first_layers if layers[0]["strategy"] == "audio_folder"]
    assert folder_layers
    assert all(Path(layer["path"]).is_absolute() for layer in folder_layers)
    assert {Path(layer["path"]).name for layer in folder_layers} <= {"a_noise.wav", "b_noise.wav"}


def test_background_noise_single_layer_balances_strategies_and_variants(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path, noise_wav_names=["ambience_1.wav", "ambience_2.wav"])
    config_path = _write_config(workspace, "balanced_noise", num_simulations=10, background_noise_block=_background_noise_block())

    layers = [json.loads(path.read_text(encoding="utf-8"))["background_noise"]["layers"][0] for path in generate_static_manifests(config_path)]
    strategy_counts = Counter(layer["strategy"] for layer in layers)
    color_counts = Counter(layer["color"] for layer in layers if layer["strategy"] == "colored")
    file_counts = Counter(Path(layer["path"]).name for layer in layers if layer["strategy"] == "audio_folder")

    assert max(strategy_counts.values()) - min(strategy_counts.values()) <= 1
    assert max(color_counts.values()) - min(color_counts.values()) <= 1
    assert max(file_counts.values()) - min(file_counts.values()) <= 1


def test_background_noise_colored_only_balances_three_colors_across_nine_scenes(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "balanced_colored_noise",
        num_simulations=9,
        background_noise_block=_colored_background_noise_block(),
    )

    layers = [json.loads(path.read_text(encoding="utf-8"))["background_noise"]["layers"][0] for path in generate_static_manifests(config_path)]

    assert Counter(layer["color"] for layer in layers) == {"white": 3, "pink": 3, "brown": 3}


def test_background_noise_multiple_layers_include_every_strategy_per_scene(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "multi_layer_noise",
        num_simulations=3,
        background_noise_block=_background_noise_block(allow_multiple_layers=True),
    )

    for manifest_path in generate_static_manifests(config_path):
        background_noise = json.loads(manifest_path.read_text(encoding="utf-8"))["background_noise"]
        assert background_noise["enabled"] is True
        assert [layer["noise_id"] for layer in background_noise["layers"]] == ["noise_0001", "noise_0002"]
        assert [layer["strategy"] for layer in background_noise["layers"]] == ["colored", "audio_folder"]


def test_runtime_manifests_preserve_scene_background_noise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "runtime_noise", background_noise_block=_background_noise_block())
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        Path(manifest["render"]["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest["render"]["output_wav_path"]).write_text("wav", encoding="utf-8")

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    render_static_scenes(config_path)

    scene_manifest = json.loads((layout["scene_manifest_dir"] / "scene_static_0001.json").read_text(encoding="utf-8"))
    runtime_background_noise = {
        json.dumps(json.loads(path.read_text(encoding="utf-8"))["background_noise"], sort_keys=True)
        for path in layout["runtime_manifest_dir"].glob("scene_static_0001__*.json")
    }
    assert runtime_background_noise == {json.dumps(scene_manifest["background_noise"], sort_keys=True)}


def test_runtime_manifests_use_unique_stable_raven_project_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "runtime_project_names", num_simulations=1)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        Path(manifest["render"]["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest["render"]["output_wav_path"]).write_text("wav", encoding="utf-8")

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    render_static_scenes(config_path)
    first_project_names = [
        json.loads(path.read_text(encoding="utf-8"))["project_name"]
        for path in sorted(layout["runtime_manifest_dir"].glob("scene_static_0001__*.json"))
    ]

    render_static_scenes(config_path)
    second_project_names = [
        json.loads(path.read_text(encoding="utf-8"))["project_name"]
        for path in sorted(layout["runtime_manifest_dir"].glob("scene_static_0001__*.json"))
    ]

    assert len(first_project_names) == 3
    assert len(set(first_project_names)) == 3
    assert first_project_names == second_project_names
    assert all(project_name.startswith("sim_test__scene_static_0001__") for project_name in first_project_names)
    assert build_raven_project_name("sim test", "scene/static:0001") == "sim_test__scene_static_0001"


def test_generate_static_manifests_keeps_sources_at_least_half_meter_from_walls_and_receiver(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "source_clearance")

    manifest = json.loads(generate_static_manifests(config_path)[0].read_text(encoding="utf-8"))
    room_dimensions = manifest["room"]["dimensions_m"]
    receiver_position = manifest["receiver"]["position_m"]

    assert len(manifest["sources"]) >= 2
    for source in manifest["sources"]:
        x, y, z = source["position_m"]
        assert 0.5 <= x <= room_dimensions[0] - 0.5
        assert 0.5 <= y <= room_dimensions[1] - 0.5
        assert 0.5 <= z <= room_dimensions[2] - 0.5
        assert math.dist(source["position_m"], receiver_position) >= 0.5


def test_generate_static_manifests_places_wall_targets_half_meter_from_the_selected_wall(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "wall_target",
        min_sources=1,
        max_sources=1,
        speech_min_count=0,
        speech_max_count=0,
        clapping_min_count=1,
        clapping_max_count=1,
        clapping_probability=1.0,
    )

    manifest = json.loads(generate_static_manifests(config_path)[0].read_text(encoding="utf-8"))

    assert [source["event_type"] for source in manifest["sources"]] == ["clapping"]
    source_position = manifest["sources"][0]["position_m"]
    room_length, room_width, room_height = manifest["room"]["dimensions_m"]

    wall_distances = [
        source_position[0],
        room_length - source_position[0],
        source_position[1],
        room_width - source_position[1],
    ]
    assert min(wall_distances) == pytest.approx(0.5)
    assert 0.5 <= source_position[2] <= room_height - 0.5


def test_generate_static_manifests_probability_one_keeps_variability_above_minimum(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "probability_one_variability",
        num_simulations=12,
        min_sources=1,
        max_sources=3,
        speech_min_count=1,
        speech_max_count=3,
        clapping_min_count=0,
        clapping_max_count=0,
    )

    speech_counts = []
    for manifest_path in generate_static_manifests(config_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        speech_counts.append(sum(1 for source in manifest["sources"] if source["event_type"] == "speech"))

    assert speech_counts
    assert all(1 <= count <= 3 for count in speech_counts)
    assert any(count < 3 for count in speech_counts)
    assert len(set(speech_counts)) > 1


def test_generate_static_manifests_probability_zero_disables_only_optional_extras(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "probability_zero_extras",
        min_sources=1,
        max_sources=3,
        speech_min_count=1,
        speech_max_count=3,
        speech_probability=0.0,
        clapping_min_count=0,
        clapping_max_count=0,
    )

    manifest = json.loads(generate_static_manifests(config_path)[0].read_text(encoding="utf-8"))
    speech_count = sum(1 for source in manifest["sources"] if source["event_type"] == "speech")

    assert speech_count == 1


def test_generate_static_manifests_uses_optional_capacity_to_reach_global_min_sources(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "min_sources_fallback",
        min_sources=4,
        max_sources=4,
        speech_min_count=1,
        speech_max_count=3,
        speech_probability=0.0,
        clapping_min_count=0,
        clapping_max_count=2,
        clapping_probability=0.0,
    )

    manifest = json.loads(generate_static_manifests(config_path)[0].read_text(encoding="utf-8"))
    counts = Counter(source["event_type"] for source in manifest["sources"])

    assert sum(counts.values()) == 4
    assert 1 <= counts["speech"] <= 3
    assert counts["clapping"] <= 2


def test_generate_static_manifests_preserves_mandatory_counts_when_global_max_leaves_one_optional_slot(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "max_sources_one_optional_slot",
        num_simulations=12,
        min_sources=3,
        max_sources=4,
        speech_min_count=2,
        speech_max_count=3,
        clapping_min_count=1,
        clapping_max_count=2,
        clapping_probability=1.0,
    )

    optional_totals = set()
    for manifest_path in generate_static_manifests(config_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        counts = Counter(source["event_type"] for source in manifest["sources"])

        # This test only locks the behavioral scope where max_sources can cap extras;
        # incompatibility rejection stays covered in validator tests.
        assert counts["speech"] >= 2
        assert counts["clapping"] >= 1
        assert sum(counts.values()) in {3, 4}

        optional_total = (counts["speech"] - 2) + (counts["clapping"] - 1)
        assert optional_total in {0, 1}
        optional_totals.add(optional_total)

    assert optional_totals
    assert optional_totals <= {0, 1}


def test_generate_static_manifests_prefers_unique_wavs_for_repeated_events(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path, speech_wav_names=["speech_01.wav", "speech_02.wav"])
    config_path = _write_config(
        workspace,
        "unique_wavs_before_reuse",
        min_sources=2,
        max_sources=2,
        speech_min_count=2,
        speech_max_count=2,
        clapping_min_count=0,
        clapping_max_count=0,
    )

    manifest = json.loads(generate_static_manifests(config_path)[0].read_text(encoding="utf-8"))
    speech_paths = [Path(source["audio_path"]) for source in manifest["sources"] if source["event_type"] == "speech"]

    assert len(speech_paths) == 2
    assert len(set(speech_paths)) == 2
    assert all(path.parent == workspace / "assets" / "events" / "speech" for path in speech_paths)


def test_generate_static_manifests_reuses_wavs_only_after_unique_pool_is_exhausted(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path, speech_wav_names=["speech_01.wav", "speech_02.wav"])
    config_path = _write_config(
        workspace,
        "reuse_after_exhaustion",
        min_sources=3,
        max_sources=3,
        speech_min_count=3,
        speech_max_count=3,
        clapping_min_count=0,
        clapping_max_count=0,
    )

    manifest = json.loads(generate_static_manifests(config_path)[0].read_text(encoding="utf-8"))
    speech_paths = [Path(source["audio_path"]) for source in manifest["sources"] if source["event_type"] == "speech"]

    assert len(speech_paths) == 3
    assert len(set(speech_paths[:2])) == 2
    assert len(set(speech_paths)) == 2


def test_validate_config_allows_omitting_total_duration(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "omit_duration")

    config = load_config(config_path)

    validate_config(config)
    assert config.source_sampling.timing.total_duration_s is None


@pytest.mark.parametrize("total_duration_s", [0.0, -1.0])
def test_generate_static_manifests_rejects_non_positive_total_duration(tmp_path: Path, total_duration_s: float) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "bad_duration", total_duration_s=total_duration_s)

    with pytest.raises(ValueError, match=r"source_sampling\.timing\.total_duration_s debe ser > 0"):
        generate_static_manifests(config_path)


def test_load_config_rejects_stale_render_target_duration_field(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "stale_render_target")
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace("  is_order_ps: 2\n", "  is_order_ps: 2\n  target_duration_s: 10\n")
    config_path.write_text(config_text, encoding="utf-8")

    with pytest.raises(Exception, match=r"render\.target_duration_s"):
        load_config(config_path)


def test_generate_static_manifests_propagates_total_duration_when_configured(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "with_duration", total_duration_s=12.5)

    manifest = json.loads(generate_static_manifests(config_path)[0].read_text(encoding="utf-8"))

    assert manifest["render"]["target_duration_s"] == 12.5


def test_generate_static_manifests_omits_total_duration_when_not_configured(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "without_duration")

    manifest = json.loads(generate_static_manifests(config_path)[0].read_text(encoding="utf-8"))

    assert "target_duration_s" not in manifest["render"]


def test_generate_static_manifests_clamps_start_time_to_zero_when_source_is_trimmed_to_total_duration(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    _write_test_wav(workspace / "assets" / "events" / "speech" / "speech_01.wav", duration_s=12.0)
    config_path = _write_config(
        workspace,
        "trimmed_start",
        total_duration_s=5.0,
        start_time_range=(7.0, 7.0),
        min_sources=1,
        max_sources=1,
        speech_min_count=1,
        speech_max_count=1,
        clapping_min_count=0,
        clapping_max_count=0,
    )

    manifest = json.loads(generate_static_manifests(config_path)[0].read_text(encoding="utf-8"))

    assert manifest["render"]["target_duration_s"] == 5.0
    assert len(manifest["sources"]) == 1
    assert manifest["sources"][0]["start_time_s"] == 0.0


def test_generate_static_manifests_limits_start_time_by_remaining_timeline_margin(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    _write_test_wav(workspace / "assets" / "events" / "speech" / "speech_01.wav", duration_s=9.0)
    config_path = _write_config(
        workspace,
        "remaining_margin",
        total_duration_s=10.0,
        start_time_range=(7.0, 7.0),
        min_sources=1,
        max_sources=1,
        speech_min_count=1,
        speech_max_count=1,
        clapping_min_count=0,
        clapping_max_count=0,
    )

    manifest = json.loads(generate_static_manifests(config_path)[0].read_text(encoding="utf-8"))

    assert manifest["render"]["target_duration_s"] == 10.0
    assert len(manifest["sources"]) == 1
    assert manifest["sources"][0]["start_time_s"] == 1.0


def test_validation_fails_when_required_paths_do_not_exist(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "broken")
    missing_audio = workspace / "assets" / "events" / "speech" / "speech_01.wav"
    missing_audio.unlink()

    with pytest.raises(ValueError, match="audio_dir no contiene archivos de audio"):
        generate_static_manifests(config_path)


def test_generate_static_manifests_fails_when_material_folder_is_missing(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "missing_material_folder")
    missing_material_dir = workspace / "assets" / "materials" / "painted_brick"
    for child in missing_material_dir.iterdir():
        child.unlink()
    missing_material_dir.rmdir()

    with pytest.raises(ValueError, match="painted_brick"):
        generate_static_manifests(config_path)


def test_generate_static_manifests_fails_when_material_folder_has_no_mat_files(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "empty_material_folder")
    material_dir = workspace / "assets" / "materials" / "plaster"
    for child in list(material_dir.iterdir()):
        child.unlink()
    (material_dir / "readme.txt").write_text("not a mat", encoding="utf-8")

    with pytest.raises(ValueError, match="plaster"):
        generate_static_manifests(config_path)


def test_generate_static_manifests_emits_only_enabled_surface_material_files(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "walls_only",
        enable_floor=False,
        enable_ceiling=False,
    )

    manifest_path = generate_static_manifests(config_path)[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert list(manifest["room"]["material_files"]) == [
        "north_wall",
        "south_wall",
        "east_wall",
        "west_wall",
    ]
    for surface_id, material_entry in manifest["room"]["material_files"].items():
        assert material_entry["material_id"] == manifest["room"]["materials"][surface_id]
        assert Path(material_entry["material_path"]).is_absolute()
        assert "surface_id" not in material_entry


def test_generate_static_manifests_skips_invalid_material_candidates_when_valid_alternative_exists(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "material_fallback")
    invalid_material = workspace / "assets" / "materials" / "painted_brick" / "a_variant.mat"
    invalid_material.write_text(_material_file_text(absorp_values=[1.2] + [0.2] * 30), encoding="utf-8")

    manifest_path = generate_static_manifests(config_path)[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    painted_brick_surfaces = [
        material_entry
        for material_entry in manifest["room"]["material_files"].values()
        if material_entry["material_id"] == "painted_brick"
    ]
    assert painted_brick_surfaces
    assert all(Path(entry["material_path"]).name != "a_variant.mat" for entry in painted_brick_surfaces)


def test_generate_static_manifests_fails_when_all_material_candidates_are_invalid(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "all_invalid_materials")
    material_dir = workspace / "assets" / "materials" / "painted_brick"
    for material_path in material_dir.glob("*.mat"):
        material_path.write_text(_material_file_text(absorp_values=[0.2] * 30), encoding="utf-8")

    with pytest.raises(ValueError, match="painted_brick") as exc_info:
        generate_static_manifests(config_path)

    assert "debe tener 31 valores" in str(exc_info.value)


def test_generate_static_manifests_rejects_unsupported_output_placeholders(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "bad_pattern", wav_pattern="{scene_id}_{receiver_id}.wav")

    with pytest.raises(ValueError, match=r"outputs\.naming\.wav_pattern"):
        generate_static_manifests(config_path)


def test_generate_static_manifests_rejects_unsafe_output_subdir(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "bad_subdir", binaural_output_subdir="../escape")

    with pytest.raises(ValueError, match=r"receiver_outputs\.binaural_hrtf\.output_subdir"):
        generate_static_manifests(config_path)


def test_generate_static_manifests_rejects_multi_output_metadata_pattern_without_output_type(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "bad_metadata_pattern",
        save_render_metadata=True,
        metadata_pattern="{scene_id}__render.json",
    )

    with pytest.raises(ValueError, match=r"outputs\.naming\.metadata_pattern debe incluir \{output_type\}"):
        generate_static_manifests(config_path)


def test_generate_static_manifests_allows_metadata_pattern_without_output_type_when_optional_outputs_are_disabled(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "disabled_optional_outputs",
        optional_hartf_outputs_enabled=False,
        save_render_metadata=True,
        metadata_pattern="{scene_id}__render.json",
    )

    manifest_paths = generate_static_manifests(config_path)

    assert len(manifest_paths) == 2


def test_render_static_scenes_invokes_matlab_once_per_scene_hrtf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "render_run",
        min_sources=2,
        max_sources=2,
        speech_min_count=1,
        speech_max_count=1,
        clapping_min_count=1,
        clapping_max_count=1,
        clapping_probability=1.0,
    )
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")
    matlab_calls: list[Path] = []

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        matlab_calls.append(manifest_path)
        index_path = layout["render_index_path"]
        planned_records = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
        assert len(planned_records) == 6
        if len(matlab_calls) == 1:
            assert {record["status"] for record in planned_records} == {"planned"}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        render = manifest["render"]
        Path(render["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(render["output_wav_path"]).write_text("wav", encoding="utf-8")

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    manifest_paths, summary = render_static_scenes(config_path)

    assert len(manifest_paths) == 2
    assert summary["render_jobs"] == 6
    assert summary["completed_variants"] == 6
    assert summary["partial_variants"] == 0
    assert summary["planned_variants"] == 0
    assert summary["inconsistent_variants"] == 0
    assert len(matlab_calls) == 6
    runtime_manifest_dir = layout["runtime_manifest_dir"]
    runtime_manifests = sorted(runtime_manifest_dir.glob("*.json"))
    assert len(runtime_manifests) == 6
    index_path = layout["render_index_path"]
    assert index_path.is_file()
    assert len(index_path.read_text(encoding="utf-8").splitlines()) == 6

    variant_manifest = json.loads(runtime_manifests[0].read_text(encoding="utf-8"))
    assert len(variant_manifest["receiver"]["hrtfs"]) == 1
    variant_hrtf_id = variant_manifest["receiver"]["hrtfs"][0]["hrtf_id"]
    speech_source = next(source for source in variant_manifest["sources"] if source["event_type"] == "speech")
    clapping_source = next(source for source in variant_manifest["sources"] if source["event_type"] == "clapping")
    assert Path(speech_source["directivity_path"]).is_absolute()
    assert "directivity_path" not in clapping_source
    assert Path(variant_manifest["render"]["output_wav_path"]).parts[-2:] == (
        variant_hrtf_id,
        f"{variant_manifest['scene_id']}__{variant_hrtf_id}.wav",
    )
    assert Path(variant_manifest["render"]["output_metadata_path"]).parts[-2:] == (
        variant_hrtf_id,
        f"{variant_manifest['scene_id']}__{variant_hrtf_id}__render.json",
    )


def test_render_static_scenes_num_workers_one_uses_sequential_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "sequential_workers", num_simulations=1, num_workers=1)
    matlab_calls: list[str] = []

    def fail_if_executor_is_used(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        raise AssertionError("ThreadPoolExecutor should not be used when num_workers <= 1")

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        matlab_calls.append(manifest["receiver"]["hrtfs"][0]["hrtf_id"])
        Path(manifest["render"]["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest["render"]["output_wav_path"]).write_text("wav", encoding="utf-8")

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.ThreadPoolExecutor", fail_if_executor_is_used)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    _, summary = render_static_scenes(config_path)

    assert matlab_calls == ["binaural_hrtf", "bte_rear_hartf", "bte_front_hartf"]
    assert summary["render_jobs"] == 3
    assert summary["completed_variants"] == 3


def test_render_static_scenes_prepares_trimmed_runtime_audio_once_per_scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _build_workspace(tmp_path)
    _write_test_wav(workspace / "assets" / "events" / "speech" / "speech_01.wav", duration_s=2.0)
    _write_test_wav(workspace / "assets" / "events" / "clapping" / "clap_01.wav", duration_s=0.5)
    config_path = _write_config(
        workspace,
        "trimmed_runtime_audio",
        total_duration_s=1.0,
        start_time_range=(7.0, 7.0),
    )
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        render = manifest["render"]
        Path(render["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(render["output_wav_path"]).write_text("wav", encoding="utf-8")

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    render_static_scenes(config_path)

    runtime_manifests = sorted(layout["runtime_manifest_dir"].glob("scene_static_0001__*.json"))
    assert len(runtime_manifests) == 3

    scene_manifest = json.loads((layout["scene_manifest_dir"] / "scene_static_0001.json").read_text(encoding="utf-8"))
    original_sources = {source["source_id"]: source for source in scene_manifest["sources"]}
    runtime_sources_by_manifest = [
        {source["source_id"]: source for source in json.loads(path.read_text(encoding="utf-8"))["sources"]}
        for path in runtime_manifests
    ]

    for source_id, original_source in original_sources.items():
        runtime_audio_paths = {sources[source_id]["audio_path"] for sources in runtime_sources_by_manifest}
        assert len(runtime_audio_paths) == 1

        runtime_source = runtime_sources_by_manifest[0][source_id]

        original_duration = _wav_duration_s(Path(original_source["audio_path"]))
        runtime_audio_path = Path(runtime_source["audio_path"])
        if original_duration > 1.0:
            assert runtime_source["start_time_s"] == 0.0
            assert runtime_audio_path != Path(original_source["audio_path"])
            assert runtime_audio_path.parent == layout["prepared_audio_dir"] / "scene_static_0001"
            assert _wav_duration_s(runtime_audio_path) == 1.0
        else:
            assert runtime_source["start_time_s"] == 0.5
            assert runtime_audio_path == Path(original_source["audio_path"])

        assert all(sources[source_id]["start_time_s"] == runtime_source["start_time_s"] for sources in runtime_sources_by_manifest)


def test_render_static_scenes_negates_receiver_and_source_z_coordinates_in_runtime_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "raven_z_axis")
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        render = manifest["render"]
        Path(render["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(render["output_wav_path"]).write_text("wav", encoding="utf-8")

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    render_static_scenes(config_path)

    scene_manifest = json.loads((layout["scene_manifest_dir"] / "scene_static_0001.json").read_text(encoding="utf-8"))
    runtime_manifest = json.loads(
        next(layout["runtime_manifest_dir"].glob("scene_static_0001__*.json")).read_text(encoding="utf-8")
    )

    assert runtime_manifest["receiver"]["position_m"] == [
        scene_manifest["receiver"]["position_m"][0],
        scene_manifest["receiver"]["position_m"][1],
        -scene_manifest["receiver"]["position_m"][2],
    ]
    assert [source["position_m"] for source in runtime_manifest["sources"]] == [
        [source["position_m"][0], source["position_m"][1], -source["position_m"][2]]
        for source in scene_manifest["sources"]
    ]


def test_multi_output_manifests_use_configured_patterns_and_subdirectories(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "multi_output")
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)

    manifest_path = generate_static_manifests(config_path)[0]
    scene_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert Path(scene_manifest["render"]["output_wav_path"]).parts[-2:] == (
        "binaural_hrtf",
        "scene_static_0001__binaural_hrtf.wav",
    )
    assert Path(scene_manifest["render"]["output_metadata_path"]).parts[-2:] == (
        "binaural_hrtf",
        "scene_static_0001__binaural_hrtf__render.json",
    )

    rear_variant = build_render_variant_paths(
        scene_manifest=scene_manifest,
        hrtf=scene_manifest["receiver"]["hrtfs"][1],
        runtime_manifest_dir=layout["runtime_manifest_dir"],
        outputs=config.outputs,
        receiver_output=config.receiver_outputs.bte_rear_hartf,
    )

    assert rear_variant["variant_id"] == "scene_static_0001__bte_rear_hartf"
    assert Path(rear_variant["rendered_wav_path"]).parts[-2:] == ("bte_rear_hartf", "scene_static_0001__bte_rear_hartf.wav")
    assert Path(rear_variant["render_metadata_path"]).parts[-2:] == (
        "bte_rear_hartf",
        "scene_static_0001__bte_rear_hartf__render.json",
    )


def test_render_static_scenes_resumes_completed_variants_and_keeps_partial_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "resume_run", resume_if_possible=True)
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    runtime_manifest_dir = layout["runtime_manifest_dir"]

    manifests = generate_static_manifests(config_path)
    first_scene = json.loads(manifests[0].read_text(encoding="utf-8"))
    resumed_variant = build_render_variant_paths(
        scene_manifest=first_scene,
        hrtf=first_scene["receiver"]["hrtfs"][0],
        runtime_manifest_dir=runtime_manifest_dir,
        outputs=config.outputs,
        receiver_output=config.receiver_outputs.binaural_hrtf,
    )
    Path(resumed_variant["runtime_manifest_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(resumed_variant["runtime_manifest_path"]).write_text("{}", encoding="utf-8")
    Path(resumed_variant["rendered_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(resumed_variant["rendered_wav_path"]).write_text("wav", encoding="utf-8")

    matlab_calls: list[str] = []

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        variant_id = f"{manifest['scene_id']}__{manifest['receiver']['hrtfs'][0]['hrtf_id']}"
        matlab_calls.append(variant_id)
        render = manifest["render"]
        Path(render["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)

        if variant_id.endswith("bte_rear_hartf"):
            raise RuntimeError("matlab failed before wav")
        if variant_id.endswith("bte_front_hartf"):
            raise RuntimeError("matlab failed hard")

        Path(render["output_wav_path"]).write_text("wav", encoding="utf-8")

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    with pytest.raises(RenderStaticRunError, match="matlab failed before wav") as exc_info:
        render_static_scenes(config_path)

    assert resumed_variant["variant_id"] not in matlab_calls
    assert exc_info.value.summary["index_path"].endswith("render_index.jsonl")
    assert exc_info.value.summary["completed_variants"] == 1
    assert exc_info.value.summary["partial_variants"] == 1
    assert exc_info.value.summary["failed_variants"] == 0
    assert exc_info.value.summary["planned_variants"] == 4
    assert exc_info.value.summary["inconsistent_variants"] == 0
    index_path = layout["render_index_path"]
    records = {
        record["variant_id"]: record
        for record in [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    }
    assert records[resumed_variant["variant_id"]]["status"] == "completed"
    assert records[resumed_variant["variant_id"]]["resumed"] is True
    assert any(record["status"] == "partial" and record["attempted"] for record in records.values())
    assert sum(record["status"] == "planned" for record in records.values()) == 4


def test_render_static_scenes_failure_summary_keeps_failed_variants_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "failed_run", resume_if_possible=False)

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        variant_id = f"{manifest['scene_id']}__{manifest['receiver']['hrtfs'][0]['hrtf_id']}"
        if variant_id.endswith("binaural_hrtf"):
            manifest_path.unlink()
            raise RuntimeError("matlab failed hard")

        render = manifest["render"]
        Path(render["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(render["output_wav_path"]).write_text("wav", encoding="utf-8")

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    with pytest.raises(RenderStaticRunError, match="matlab failed hard") as exc_info:
        render_static_scenes(config_path)

    assert exc_info.value.summary["completed_variants"] == 0
    assert exc_info.value.summary["partial_variants"] == 0
    assert exc_info.value.summary["failed_variants"] == 1
    assert exc_info.value.summary["planned_variants"] == 5
    assert exc_info.value.summary["total_variants"] == 6


def test_render_static_scenes_parallel_prepares_before_fanout_and_parent_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "parallel_run", num_simulations=1, num_workers=4)
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    matlab_calls: list[Path] = []
    upsert_threads: list[str] = []
    worker_completed = 0
    original_upsert = render_pipeline.upsert_variant
    original_executor = render_pipeline.ThreadPoolExecutor
    executor_worker_counts: list[int] = []

    class RecordingThreadPoolExecutor(original_executor):
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            max_workers = kwargs.get("max_workers", args[0] if args else 0)
            executor_worker_counts.append(max_workers)
            super().__init__(*args, **kwargs)

    def recording_upsert(index_path, records, record) -> None:  # type: ignore[no-untyped-def]
        import threading

        upsert_threads.append(threading.current_thread().name)
        original_upsert(index_path, records, record)

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        nonlocal worker_completed
        runtime_manifests = sorted(layout["runtime_manifest_dir"].glob("scene_static_0001__*.json"))
        assert len(runtime_manifests) == 3
        for runtime_manifest_path in runtime_manifests:
            manifest = json.loads(runtime_manifest_path.read_text(encoding="utf-8"))
            for source in manifest["sources"]:
                assert Path(source["audio_path"]).is_file()
        assert len(layout["render_index_path"].read_text(encoding="utf-8").splitlines()) == 3

        matlab_calls.append(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        render = manifest["render"]
        Path(render["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(render["output_wav_path"]).write_text("wav", encoding="utf-8")
        worker_completed += 1

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.ThreadPoolExecutor", RecordingThreadPoolExecutor)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.upsert_variant", recording_upsert)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    _, summary = render_static_scenes(config_path)

    assert summary["render_jobs"] == 3
    assert summary["completed_variants"] == 3
    assert len(matlab_calls) == 3
    assert executor_worker_counts == [3]
    assert worker_completed == 3
    assert all(not thread_name.startswith("ThreadPoolExecutor") for thread_name in upsert_threads)


def test_render_static_scenes_parallel_resumes_completed_variant_and_submits_only_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace,
        "parallel_resume",
        num_simulations=1,
        num_workers=4,
        resume_if_possible=True,
    )
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    manifests = generate_static_manifests(config_path)
    first_scene = json.loads(manifests[0].read_text(encoding="utf-8"))
    resumed_variant = build_render_variant_paths(
        scene_manifest=first_scene,
        hrtf=first_scene["receiver"]["hrtfs"][0],
        runtime_manifest_dir=layout["runtime_manifest_dir"],
        outputs=config.outputs,
        receiver_output=config.receiver_outputs.binaural_hrtf,
    )
    Path(resumed_variant["rendered_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(resumed_variant["rendered_wav_path"]).write_text("wav", encoding="utf-8")

    original_executor = render_pipeline.ThreadPoolExecutor
    executor_worker_counts: list[int] = []
    submitted_variant_ids: list[str] = []
    matlab_variant_ids: list[str] = []

    class RecordingThreadPoolExecutor(original_executor):
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            max_workers = kwargs.get("max_workers", args[0] if args else 0)
            executor_worker_counts.append(max_workers)
            super().__init__(*args, **kwargs)

        def submit(self, fn, /, *args, **kwargs):  # type: ignore[no-untyped-def]
            task = args[0]
            submitted_variant_ids.append(task["variant_paths"]["variant_id"])
            return super().submit(fn, *args, **kwargs)

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        variant_id = f"{manifest['scene_id']}__{manifest['receiver']['hrtfs'][0]['hrtf_id']}"
        matlab_variant_ids.append(variant_id)
        render = manifest["render"]
        Path(render["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(render["output_wav_path"]).write_text("wav", encoding="utf-8")

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.ThreadPoolExecutor", RecordingThreadPoolExecutor)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    _, summary = render_static_scenes(config_path)

    assert executor_worker_counts == [2]
    assert submitted_variant_ids == [
        "scene_static_0001__bte_rear_hartf",
        "scene_static_0001__bte_front_hartf",
    ]
    assert sorted(matlab_variant_ids) == sorted(submitted_variant_ids)
    assert resumed_variant["variant_id"] not in submitted_variant_ids
    assert summary["render_jobs"] == 2
    assert summary["completed_variants"] == 3
    assert summary["resumed_variants"] == 1


def test_render_static_scenes_parallel_indexes_all_submitted_results_before_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "parallel_failure", num_simulations=1, num_workers=2)
    layout = resolve_artifact_layout(load_config(config_path).outputs, "sim_test")

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        hrtf_id = manifest["receiver"]["hrtfs"][0]["hrtf_id"]
        render = manifest["render"]
        Path(render["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        if hrtf_id == "binaural_hrtf":
            Path(render["output_wav_path"]).write_text("wav", encoding="utf-8")
            return
        if hrtf_id == "bte_rear_hartf":
            raise RuntimeError("parallel matlab failed")
        Path(render["output_wav_path"]).write_text("wav", encoding="utf-8")

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    with pytest.raises(RenderStaticRunError, match="parallel matlab failed") as exc_info:
        render_static_scenes(config_path)

    assert exc_info.value.summary["completed_variants"] == 2
    assert exc_info.value.summary["partial_variants"] == 1
    assert exc_info.value.summary["planned_variants"] == 0
    records = [json.loads(line) for line in layout["render_index_path"].read_text(encoding="utf-8").splitlines()]
    assert {record["status"] for record in records} == {"completed", "partial"}


def test_render_static_scenes_reports_metadata_inconsistencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "metadata_inconsistent", save_render_metadata=True)

    def fake_render(manifest_path: Path, matlab_executable: str = "matlab") -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        render = manifest["render"]
        Path(render["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(render["output_wav_path"]).write_text("wav", encoding="utf-8")
        Path(render["output_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(render["output_metadata_path"]).write_text(
            json.dumps(
                {
                    "scene_id": "unexpected_scene",
                    "hrtf_id": manifest["receiver"]["hrtfs"][0]["hrtf_id"],
                    "render": {
                        "output_wav_path": render["output_wav_path"],
                        "output_metadata_path": render["output_metadata_path"],
                    },
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab", fake_render)

    _, summary = render_static_scenes(config_path)

    assert summary["completed_variants"] == 6
    assert summary["inconsistent_variants"] == 6
    assert summary["planned_variants"] == 0


def test_render_static_cli_reports_summary_when_render_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "cli_render_failure")
    runner = CliRunner()

    def fake_render_static(_config_path: Path) -> tuple[list[Path], RenderSummary]:
        raise RenderStaticRunError(
            manifest_paths=[Path("scene_static_0001.json")],
            summary={
                "render_jobs": 1,
                "index_path": "artifacts/sim_test/indexes/render_index.jsonl",
                "total_variants": 3,
                "completed_variants": 1,
                "partial_variants": 1,
                "failed_variants": 1,
                "planned_variants": 1,
                "resumed_variants": 0,
                "inconsistent_variants": 1,
                "clarity": None,
            },
            cause=RuntimeError("matlab failed hard"),
        )

    monkeypatch.setattr("acoustic_orchestrator.cli.render_static_scenes", fake_render_static)

    result = runner.invoke(app, ["render-static", str(config_path)])

    assert result.exit_code == 1
    assert "Índice: artifacts/sim_test/indexes/render_index.jsonl" in result.stdout
    assert "fallidas=1" in result.stdout
    assert "planificadas=1" in result.stdout
    assert "inconsistencias=1" in result.stdout


def test_render_static_cli_preserves_generate_manifests_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "cli_render")
    runner = CliRunner()

    monkeypatch.setattr(
        "acoustic_orchestrator.cli.render_static_scenes",
        lambda config_path: (
            [Path("scene_static_0001.json")],
            RenderSummary(
                {
                "render_jobs": 3,
                "index_path": "artifacts/sim_test/indexes/render_index.jsonl",
                "total_variants": 3,
                "completed_variants": 2,
                "partial_variants": 1,
                "failed_variants": 0,
                "planned_variants": 0,
                "resumed_variants": 1,
                "inconsistent_variants": 0,
                "clarity": None,
                }
            ),
        ),
    )

    result = runner.invoke(app, ["render-static", str(config_path)])

    assert result.exit_code == 0
    assert "ejecutados 3 render(s) de MATLAB" in result.stdout
    assert "Índice: artifacts/sim_test/indexes/render_index.jsonl" in result.stdout
    assert "completadas=2" in result.stdout
    assert "planificadas=0" in result.stdout

    generate_result = runner.invoke(app, ["generate-manifests", str(config_path)])

    assert generate_result.exit_code == 0
    assert "Generados 2 manifiestos" in generate_result.stdout


def _build_workspace(
    tmp_path: Path,
    *,
    speech_wav_names: list[str] | None = None,
    clapping_wav_names: list[str] | None = None,
    noise_wav_names: list[str] | None = None,
) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "assets" / "hrtf").mkdir(parents=True)
    (workspace / "assets" / "directivity").mkdir(parents=True)
    (workspace / "assets" / "hartf" / "rear").mkdir(parents=True)
    (workspace / "assets" / "hartf" / "front").mkdir(parents=True)
    (workspace / "assets" / "events" / "speech").mkdir(parents=True)
    (workspace / "assets" / "events" / "clapping").mkdir(parents=True)
    (workspace / "assets" / "noise" / "cafeteria").mkdir(parents=True)
    for material_id in ["painted_brick", "window_glass", "wood", "plaster"]:
        (workspace / "assets" / "materials" / material_id).mkdir(parents=True)
    (workspace / "outputs").mkdir(parents=True)
    (workspace / "logs").mkdir(parents=True)

    for file_path in [
        workspace / "assets" / "hrtf" / "subject01.daff",
        workspace / "assets" / "directivity" / "speech_talker.daff",
        workspace / "assets" / "hartf" / "rear" / "rear01.daff",
        workspace / "assets" / "hartf" / "front" / "front01.daff",
        workspace / "base_room.rpf",
        workspace / "hearing_profiles.yaml",
    ]:
        file_path.write_text("dummy", encoding="utf-8")

    for speech_wav_name in speech_wav_names or ["speech_01.wav"]:
        _write_test_wav(workspace / "assets" / "events" / "speech" / speech_wav_name, duration_s=2.0)
    for clapping_wav_name in clapping_wav_names or ["clap_01.wav"]:
        _write_test_wav(workspace / "assets" / "events" / "clapping" / clapping_wav_name, duration_s=0.5)
    for noise_wav_name in noise_wav_names or ["noise_01.wav", "noise_02.wav"]:
        _write_test_wav(workspace / "assets" / "noise" / "cafeteria" / noise_wav_name, duration_s=1.0)

    for file_path in [
        workspace / "assets" / "materials" / "painted_brick" / "b_variant.mat",
        workspace / "assets" / "materials" / "painted_brick" / "a_variant.mat",
        workspace / "assets" / "materials" / "window_glass" / "window.mat",
        workspace / "assets" / "materials" / "wood" / "wood.mat",
        workspace / "assets" / "materials" / "plaster" / "ceiling.mat",
    ]:
        file_path.write_text(_material_file_text(), encoding="utf-8")

    return workspace


def _write_config(
    workspace: Path,
    run_name: str,
    num_simulations: int = 2,
    num_workers: int = 1,
    include_optional_hartf_outputs: bool = True,
    optional_hartf_outputs_enabled: bool = True,
    resume_if_possible: bool = False,
    enable_floor: bool = True,
    enable_ceiling: bool = True,
    save_render_metadata: bool = False,
    wav_pattern: str = "{scene_id}__{output_type}.wav",
    metadata_pattern: str = "{scene_id}__{output_type}__render.json",
    binaural_output_subdir: str = "binaural_hrtf",
    total_duration_s: float | None = None,
    start_time_range: tuple[float, float] = (0.0, 1.0),
    min_sources: int = 2,
    max_sources: int = 3,
    speech_min_count: int = 1,
    speech_max_count: int = 2,
    speech_probability: float = 1.0,
    clapping_min_count: int = 0,
    clapping_max_count: int = 1,
    clapping_probability: float = 0.4,
    speech_directivity: str | None = "./assets/directivity/speech_talker.daff",
    background_noise_block: str = "",
) -> Path:
    config_path = workspace / f"{run_name}.yml"
    optional_hartf_outputs = """
  bte_rear_hartf:
    enabled: {optional_enabled}
    ir_catalog_path: ./assets/hartf/rear
    file_pattern: \"*.daff\"
    output_subdir: bte_rear_hartf
    num_channels: 2
    required: false
  bte_front_hartf:
    enabled: {optional_enabled}
    ir_catalog_path: ./assets/hartf/front
    file_pattern: \"*.daff\"
    output_subdir: bte_front_hartf
    num_channels: 2
    required: false
""".format(optional_enabled=str(optional_hartf_outputs_enabled).lower()) if include_optional_hartf_outputs else ""
    total_duration_block = (
        f"    total_duration_s: {total_duration_s}\n"
        if total_duration_s is not None
        else ""
    )
    speech_directivity_block = (
        f"      directivity: {speech_directivity}\n"
        if speech_directivity is not None
        else ""
    )
    config_path.write_text(
        f"""
experiment:
  experiment_id: sim_test
  description: test
  scene_type: static
  random_seed: 123

execution:
  num_simulations: {num_simulations}
  num_workers: {num_workers}
  overwrite_existing: true
  resume_if_possible: {str(resume_if_possible).lower()}
  save_scene_manifest: true
  save_render_metadata: {str(save_render_metadata).lower()}

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

{background_noise_block.rstrip()}

receiver_sampling:
  one_receiver_per_scene: true
  position_strategy:
    type: random_uniform_inside_room
    margin_m:
      x: 0.2
      y: 0.2
      z: 0.2
    fixed_height_m:
      min: 1.2
      max: 1.4
  orientation_strategy:
    type: random_yaw
    yaw_deg:
      min: -180.0
      max: 180.0
    pitch_deg:
      fixed: 0.0
    roll_deg:
      fixed: 0.0

receiver_outputs:
  binaural_hrtf:
    enabled: true
    ir_catalog_path: ./assets/hrtf
    file_pattern: "*.daff"
    output_subdir: "{binaural_output_subdir}"
    num_channels: 2
    required: true
{optional_hartf_outputs.rstrip()}

room_sampling:
  dimensions_m:
    length:
      min: 4.0
      max: 4.5
    width:
      min: 3.0
      max: 3.5
    height:
      min: 2.4
      max: 2.8
  materials:
    walls: [painted_brick, window_glass]
    floor: [wood]
    ceiling: [plaster]
  semantic_surfaces:
    enable_walls: true
    enable_floor: {str(enable_floor).lower()}
    enable_ceiling: {str(enable_ceiling).lower()}

source_sampling:
  min_sources: {min_sources}
  max_sources: {max_sources}
  timing:
    allow_offsets: true
    start_time_s:
      min: {start_time_range[0]}
      max: {start_time_range[1]}
{total_duration_block.rstrip()}
  gain_db:
    min: -3.0
    max: 1.0
  default_orientation_strategy:
    type: random_yaw
    yaw_deg:
      min: -180.0
      max: 180.0
    pitch_deg:
      fixed: 0.0
    roll_deg:
      fixed: 0.0
  source_types:
    - event_type: speech
      role: base
      min_count: {speech_min_count}
      max_count: {speech_max_count}
      probability: {speech_probability}
      audio_dir: ./assets/events/speech
{speech_directivity_block.rstrip()}
      spatial_policy:
        type: random_valid
    - event_type: clapping
      role: optional
      min_count: {clapping_min_count}
      max_count: {clapping_max_count}
      probability: {clapping_probability}
      audio_dir: ./assets/events/clapping
      spatial_policy:
        type: weighted_targets
        targets:
          wall: 1.0
      orientation_strategy:
        type: facing_surface_normal

scene_validation:
  min_distance_source_to_receiver_m: 0.5
  min_distance_between_sources_m: 0.1
  require_sources_inside_room: true
  require_receiver_inside_room: true
  max_sampling_attempts_per_scene: 10

outputs:
  artifact_root: ./artifacts
  naming:
    scene_id_prefix: scene_static
    wav_pattern: "{wav_pattern}"
    metadata_pattern: "{metadata_pattern}"

hearing_degradation:
  enabled: false
  input_targets: [binaural_hrtf]
  hearing_profiles_path: ./hearing_profiles.yaml
""".strip(),
        encoding="utf-8",
    )
    return config_path


def _background_noise_block(*, allow_multiple_layers: bool = False) -> str:
    return f"""
background_noise:
  enabled: true
  snr_db: 6.0
  allow_multiple_layers: {str(allow_multiple_layers).lower()}
  strategies:
    - type: colored
      colors: [white, pink, brown]
    - type: audio_folder
      noise_type: cafeteria
      audio_dir: ./assets/noise/cafeteria
      file_pattern: "*.wav"
""".strip()


def _colored_background_noise_block() -> str:
    return """
background_noise:
  enabled: true
  snr_db: 6.0
  allow_multiple_layers: false
  strategies:
    - type: colored
      colors: [white, pink, brown]
""".strip()


def _material_file_text(*, absorp_values: list[float] | None = None, scatter_values: list[float] | None = None) -> str:
    absorp_values = absorp_values or [0.2] * 31
    scatter_values = scatter_values or [0.1] * 31
    absorp = ", ".join(f"{value:.4f}" for value in absorp_values)
    scatter = ", ".join(f"{value:.4f}" for value in scatter_values)
    return "\n".join(
        [
            "[Material]",
            "name= Test Material",
            "notes= generated by tests",
            f"absorp= {absorp}",
            f"scatter= {scatter}",
            "interpol= " + ", ".join(["1"] * 31),
        ]
    )


def _write_test_wav(path: Path, *, duration_s: float, sample_rate_hz: int = 8000) -> None:
    total_frames = int(duration_s * sample_rate_hz)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate_hz)
        wav_file.writeframes(b"\x00\x00" * total_frames)


def _wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getnframes() / wav_file.getframerate()
