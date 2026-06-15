from pathlib import Path

import pytest

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.validator import load_hearing_profile_catalog, validate_config


def test_load_hearing_profile_catalog_accepts_multiple_valid_profiles(tmp_path: Path) -> None:
    hearing_profiles_path = tmp_path / "hearing_profiles.yaml"
    hearing_profiles_path.write_text(
        """
profiles:
  - hearing_profile_id: mild_loss
    ears:
      left:
        loss_db_by_band:
          250: 10
          1000: 20
      right:
        loss_db_by_band:
          250: 12
  - hearing_profile_id: severe-loss
    ears:
      left:
        loss_db_by_band:
          500: 35
      right:
        loss_db_by_band:
          500: 40
        """.strip(),
        encoding="utf-8",
    )

    catalog = load_hearing_profile_catalog(hearing_profiles_path)

    assert [profile["hearing_profile_id"] for profile in catalog] == ["mild_loss", "severe-loss"]
    assert catalog[0]["left_loss_db_by_band"] == {"250": 10.0, "1000": 20.0}
    assert catalog[1]["right_loss_db_by_band"] == {"500": 40.0}


def test_load_hearing_profile_catalog_rejects_duplicate_ids_and_missing_ears(tmp_path: Path) -> None:
    hearing_profiles_path = tmp_path / "hearing_profiles.yaml"
    hearing_profiles_path.write_text(
        """
profiles:
  - hearing_profile_id: duplicate_profile
    ears:
      left:
        loss_db_by_band:
          250: 10
      right:
        loss_db_by_band:
          250: 12
  - hearing_profile_id: duplicate_profile
    ears:
      left:
        loss_db_by_band:
          500: 15
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        load_hearing_profile_catalog(hearing_profiles_path)

    assert "hearing_profile_id está duplicado" in str(exc_info.value)
    assert "ears.right" in str(exc_info.value)


def test_validate_config_rejects_semantically_invalid_hearing_profile_catalog(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    config.hearing_degradation.hearing_profiles_path.write_text("profiles: []", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        validate_config(config)

    assert "hearing_degradation.hearing_profiles_path profiles debe ser una lista no vacía" in str(exc_info.value)


def test_validate_config_accepts_omitting_source_directivity(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))

    validate_config(config)


def test_background_noise_defaults_to_disabled_when_omitted(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))

    validate_config(config)

    assert config.background_noise.enabled is False
    assert config.background_noise.strategies == []


def test_validate_config_rejects_enabled_background_noise_without_strategies(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, background_noise_block="""
background_noise:
  enabled: true
  snr_db: 5.0
  allow_multiple_layers: false
  strategies: []
""".strip()))

    with pytest.raises(ValueError, match="background_noise.strategies"):
        validate_config(config)


def test_load_config_rejects_unsupported_background_noise_color(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="background_noise.*colors"):
        load_config(_write_config(tmp_path, background_noise_block="""
background_noise:
  enabled: true
  snr_db: 5.0
  allow_multiple_layers: false
  strategies:
    - type: colored
      colors: [blue]
""".strip()))


def test_validate_config_rejects_non_finite_background_noise_snr(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, background_noise_block="""
background_noise:
  enabled: true
  snr_db: .nan
  allow_multiple_layers: false
  strategies:
    - type: colored
      colors: [white]
""".strip()))

    with pytest.raises(ValueError, match="background_noise.snr_db"):
        validate_config(config)


def test_validate_config_rejects_missing_background_noise_folder(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, background_noise_block="""
background_noise:
  enabled: true
  snr_db: 5.0
  allow_multiple_layers: false
  strategies:
    - type: audio_folder
      noise_type: cafeteria
      audio_dir: assets/noise/missing
      file_pattern: "*.wav"
""".strip()))

    with pytest.raises(ValueError, match=r"background_noise.strategies\[0\].audio_dir"):
        validate_config(config)


def test_validate_config_rejects_empty_background_noise_pattern_match(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, background_noise_block="""
background_noise:
  enabled: true
  snr_db: 5.0
  allow_multiple_layers: false
  strategies:
    - type: audio_folder
      noise_type: cafeteria
      audio_dir: assets/noise/cafeteria
      file_pattern: "*.flac"
""".strip()))

    with pytest.raises(ValueError, match="no contiene archivos"):
        validate_config(config)


def test_validate_config_accepts_valid_background_noise_folder_strategy(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, background_noise_block="""
background_noise:
  enabled: true
  snr_db: 5.0
  allow_multiple_layers: false
  strategies:
    - type: audio_folder
      noise_type: cafeteria
      audio_dir: assets/noise/cafeteria
      file_pattern: "*.wav"
""".strip()))

    validate_config(config)
    strategy = config.background_noise.strategies[0]
    assert strategy.type == "audio_folder"
    assert strategy.audio_dir.is_absolute()


def test_validate_config_rejects_missing_source_directivity_file(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, directivity="assets/directivity/missing.daff")

    with pytest.raises(ValueError) as exc_info:
        validate_config(load_config(config_path))

    assert "source_types.speech.directivity" in str(exc_info.value)


def test_validate_config_rejects_non_daff_source_directivity(tmp_path: Path) -> None:
    invalid_directivity = tmp_path / "assets" / "directivity" / "speech.txt"
    invalid_directivity.parent.mkdir(parents=True, exist_ok=True)
    invalid_directivity.write_text("not daff", encoding="utf-8")
    config_path = _write_config(tmp_path, directivity="assets/directivity/speech.txt")

    with pytest.raises(ValueError) as exc_info:
        validate_config(load_config(config_path))

    assert "source_types.speech.directivity debe apuntar a un archivo .daff" in str(exc_info.value)


def test_validate_config_rejects_minimums_that_exceed_global_max_sources(tmp_path: Path) -> None:
    config = load_config(
        _write_config(
            tmp_path,
            min_sources=1,
            max_sources=1,
            source_min_count=2,
            source_max_count=2,
        )
    )

    with pytest.raises(ValueError, match="La suma de source_types.min_count excede source_sampling.max_sources"):
        validate_config(config)


def test_validate_config_rejects_global_min_sources_above_reachable_maximum(tmp_path: Path) -> None:
    config = load_config(
        _write_config(
            tmp_path,
            min_sources=2,
            max_sources=2,
            source_min_count=1,
            source_max_count=1,
        )
    )

    with pytest.raises(ValueError, match="La suma de source_types.max_count no alcanza source_sampling.min_sources"):
        validate_config(config)


def test_load_config_rejects_source_type_without_min_count(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config_path.write_text(config_path.read_text(encoding="utf-8").replace("      min_count: 2\n", "", 1), encoding="utf-8")

    with pytest.raises(Exception, match=r"source_sampling\.source_types\.0\.min_count"):
        load_config(config_path)


def test_validate_config_accepts_random_valid_away_from_receiver_with_radius(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, spatial_policy_block="""
      spatial_policy:
        type: random_valid_away_from_receiver
        min_radius_from_receiver_m: 1.5
""".rstrip()))

    validate_config(config)


@pytest.mark.parametrize("radius", [None, -0.1, ".nan"])
def test_validate_config_rejects_invalid_away_policy_radius(tmp_path: Path, radius: object) -> None:
    radius_line = "" if radius is None else f"        min_radius_from_receiver_m: {radius}\n"
    config = load_config(_write_config(tmp_path, spatial_policy_block=(
        "      spatial_policy:\n"
        "        type: random_valid_away_from_receiver\n"
        f"{radius_line}"
    ).rstrip()))

    with pytest.raises(ValueError, match="min_radius_from_receiver_m"):
        validate_config(config)


def test_validate_config_rejects_away_radius_on_random_valid(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, spatial_policy_block="""
      spatial_policy:
        type: random_valid
        min_radius_from_receiver_m: 1.5
""".rstrip()))

    with pytest.raises(ValueError, match="solo se permite"):
        validate_config(config)


def test_validate_config_rejects_single_source_scenes(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, min_sources=1, max_sources=2, source_min_count=1, source_max_count=2))

    with pytest.raises(ValueError, match="source_sampling.min_sources debe ser > 1"):
        validate_config(config)


def _write_config(
    tmp_path: Path,
    directivity: str | None = None,
    *,
    min_sources: int = 2,
    max_sources: int = 2,
    source_min_count: int = 2,
    source_max_count: int = 2,
    source_probability: float = 1.0,
    background_noise_block: str = "",
    spatial_policy_block: str = "      spatial_policy:\n        type: random_valid",
) -> Path:
    artifact_root = tmp_path / "artifacts"
    assets_root = tmp_path / "assets"
    hearing_profiles = tmp_path / "hearing_profiles.yaml"
    config_path = tmp_path / "config.yml"
    directivity_block = f"      directivity: {directivity}\n" if directivity is not None else ""
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
            "  save_render_metadata: true\n"
            "raven:\n"
            f"  base_rpf_file: {tmp_path.as_posix()}/base_room.rpf\n"
            "render:\n"
            "  sample_rate_hz: 44100\n"
            f"{background_noise_block}\n"
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
            f"  min_sources: {min_sources}\n"
            f"  max_sources: {max_sources}\n"
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
            f"    - event_type: speech\n      role: base\n      min_count: {source_min_count}\n      max_count: {source_max_count}\n      probability: {source_probability}\n      audio_dir: {assets_root.as_posix()}/audio\n{directivity_block}{spatial_policy_block}\n"
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
            "    entrypoint: clarity-backend\n"
            "    auto_submit: false\n"
            "    use_uv: true\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "base_room.rpf").write_text("rpf", encoding="utf-8")
    (assets_root / "hrtf").mkdir(parents=True, exist_ok=True)
    (assets_root / "hrtf" / "sample.daff").write_text("daff", encoding="utf-8")
    (assets_root / "audio").mkdir(parents=True, exist_ok=True)
    (assets_root / "audio" / "sample.wav").write_text("wav", encoding="utf-8")
    (assets_root / "noise" / "cafeteria").mkdir(parents=True, exist_ok=True)
    (assets_root / "noise" / "cafeteria" / "noise.wav").write_text("wav", encoding="utf-8")
    (assets_root / "directivity").mkdir(parents=True, exist_ok=True)
    (assets_root / "directivity" / "speech.daff").write_text("daff", encoding="utf-8")
    for material_id in ["brick", "wood", "plaster"]:
        material_dir = assets_root / "materials" / material_id
        material_dir.mkdir(parents=True, exist_ok=True)
        (material_dir / "sample.mat").write_text(_material_file_text(), encoding="utf-8")
    hearing_profiles.write_text(
        """
profiles:
  - hearing_profile_id: baseline
    ears:
      left:
        loss_db_by_band:
          250: 10
      right:
        loss_db_by_band:
          250: 12
        """.strip(),
        encoding="utf-8",
    )
    return config_path


def _material_file_text() -> str:
    absorp = ", ".join(["0.2"] * 31)
    scatter = ", ".join(["0.1"] * 31)
    return f"[Material]\nname=test\nnotes=test\nabsorp={absorp}\nscatter={scatter}\n"
