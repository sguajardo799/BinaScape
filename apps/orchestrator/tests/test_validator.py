from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.models import (
    CircularArrayOutputConfig,
    ReceiverRandomYawOrientationStrategy,
    ReceiverRandomYawPitchOrientationStrategy,
)
from acoustic_orchestrator.config.validator import load_hearing_profile_catalog, validate_config


@pytest.mark.parametrize("layout", ["twenty_ids", "mixed", "empty", "sidecars", "deep", "missing", "file"])
def test_source_audio_layout_validation_matches_candidates(tmp_path: Path, layout: str) -> None:
    from acoustic_orchestrator.experiment.sampler import _build_audio_pools
    from acoustic_orchestrator.source_audio import list_source_wavs

    config = load_config(_write_config(tmp_path))
    source = config.source_sampling.source_types[0]
    root = tmp_path / "source_database"
    source.audio_dir = root
    if layout == "file":
        root.write_text("not a directory", encoding="utf-8")
    elif layout != "missing":
        root.mkdir()
        (root / "empty_id").mkdir()
    if layout == "twenty_ids":
        for index in range(20):
            folder = root / f"ID{index:02d}"
            folder.mkdir()
            (folder / "clip.WAV").write_bytes(b"")
    elif layout == "mixed":
        (root / "flat.wav").write_bytes(b"")
        (root / "ID01").mkdir()
        (root / "ID01/clip.wav").write_bytes(b"")
        (root / "labels.json").write_text("{}", encoding="utf-8")
    elif layout == "sidecars":
        (root / "labels.json").write_text("{}", encoding="utf-8")
    elif layout == "deep":
        (root / "ID01/session").mkdir(parents=True)
        (root / "ID01/session/clip.wav").write_bytes(b"")

    if layout in {"twenty_ids", "mixed"}:
        validate_config(config)
        candidates = list_source_wavs(root)
        assert len(candidates) == (20 if layout == "twenty_ids" else 2)
        assert _build_audio_pools([source])[source.event_type]["all_files"] == candidates
    else:
        with pytest.raises(ValueError) as error:
            validate_config(config)
        assert str(root) in str(error.value)
        if root.is_dir():
            assert "WAV" in str(error.value)
            assert list_source_wavs(root) == []


def test_validate_config_accepts_array_only_without_catalog(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    config.receiver_outputs.binaural_hrtf = None
    config.receiver_outputs.circular_array = CircularArrayOutputConfig(
        enabled=True, num_microphones=8, radius_m=0.05
    )
    config.hearing_degradation.enabled = False
    config.hearing_degradation.input_targets = []
    validate_config(config)


def test_validate_config_accepts_mixed_receiver_outputs(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    config.receiver_outputs.circular_array = CircularArrayOutputConfig(
        enabled=True, num_microphones=8, radius_m=0.05
    )
    config.receiver_outputs.bte_rear_hartf = config.receiver_outputs.binaural_hrtf.model_copy(
        update={"output_subdir": "bte_rear_hartf"}
    )
    validate_config(config)


@pytest.mark.parametrize("subdir", ["", "../escape", "/absolute", "C:/absolute"])
def test_validate_config_rejects_unsafe_array_subdir(tmp_path: Path, subdir: str) -> None:
    config = load_config(_write_config(tmp_path))
    config.receiver_outputs.circular_array = CircularArrayOutputConfig(
        enabled=True, num_microphones=8, radius_m=0.05, output_subdir=subdir
    )
    with pytest.raises(ValueError, match=r"circular_array\.output_subdir"):
        validate_config(config)


def test_validate_config_rejects_required_disabled_array(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    config.receiver_outputs.circular_array = CircularArrayOutputConfig(required=True)
    with pytest.raises(ValueError, match="circular_array no puede ser required=true si enabled=false"):
        validate_config(config)


def test_validate_config_rejects_all_receiver_outputs_disabled(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    config.receiver_outputs.binaural_hrtf = None
    config.receiver_outputs.circular_array = CircularArrayOutputConfig()
    config.hearing_degradation.enabled = False
    config.hearing_degradation.input_targets = []
    with pytest.raises(ValueError, match="al menos un receiver_output habilitado"):
        validate_config(config)


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("target", ["bte_rear_hartf", "bte_front_hartf", "circular_array"])
def test_validate_config_rejects_ineligible_clarity_targets(tmp_path: Path, target: str, enabled: bool) -> None:
    config = load_config(_write_config(tmp_path))
    config.receiver_outputs.circular_array = CircularArrayOutputConfig(
        enabled=True, num_microphones=2, radius_m=0.05
    )
    config.hearing_degradation.enabled = enabled
    config.hearing_degradation.input_targets = [target]
    with pytest.raises(ValueError, match=f"target no elegible: {target}"):
        validate_config(config)


@pytest.mark.parametrize("target", ["unknown", "binaural_hrtf__1"])
def test_validate_config_rejects_unknown_or_variant_clarity_target(tmp_path: Path, target: str) -> None:
    config = load_config(_write_config(tmp_path))
    config.hearing_degradation.input_targets = [target]
    with pytest.raises(ValueError, match=f"target desconocido: {target}"):
        validate_config(config)


@pytest.mark.parametrize("absent", [True, False])
def test_enabled_clarity_requires_available_hrtf(tmp_path: Path, absent: bool) -> None:
    config = load_config(_write_config(tmp_path))
    config.receiver_outputs.circular_array = CircularArrayOutputConfig(
        enabled=True, num_microphones=8, radius_m=0.05
    )
    if absent:
        config.receiver_outputs.binaural_hrtf = None
    else:
        config.receiver_outputs.binaural_hrtf.enabled = False
        config.receiver_outputs.binaural_hrtf.required = False
    with pytest.raises(ValueError, match="binaural_hrtf"):
        validate_config(config)


def test_enabled_clarity_rejects_empty_targets(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    config.hearing_degradation.input_targets = []
    with pytest.raises(ValueError, match="input_targets debe incluir al menos un target"):
        validate_config(config)


def test_documented_array_config_validates_with_fixture_paths(tmp_path: Path) -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Circular Array Configuration", 1)[1]
    excerpt = yaml.safe_load(section.split("```yaml\n", 1)[1].split("```", 1)[0])
    config_path = _write_config(tmp_path)
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    excerpt["hearing_degradation"]["hearing_profiles_path"] = raw_config["hearing_degradation"]["hearing_profiles_path"]
    raw_config.update(excerpt)
    config_path.write_text(yaml.safe_dump(raw_config), encoding="utf-8")
    config = load_config(config_path)
    validate_config(config)
    assert config.receiver_outputs.binaural_hrtf is None
    assert config.receiver_outputs.circular_array.num_channels == 8


@pytest.mark.parametrize("name, expected_channels", [("circular_array_only", 8), ("circular_array_mixed", 3)])
def test_array_experiment_examples_validate_with_fixture_assets(
    tmp_path: Path, name: str, expected_channels: int
) -> None:
    fixture_config = load_config(_write_config(tmp_path))
    repository = Path(__file__).resolve().parents[3]
    config = load_config(repository / "configs" / "experiments" / f"{name}.yml")
    config.raven = fixture_config.raven
    config.room_sampling.materials = fixture_config.room_sampling.materials
    config.hearing_degradation.hearing_profiles_path = fixture_config.hearing_degradation.hearing_profiles_path
    config.hearing_degradation.runner.backend_project_path = None
    for source in config.source_sampling.source_types:
        source.audio_dir = fixture_config.source_sampling.source_types[0].audio_dir
    if config.receiver_outputs.binaural_hrtf is not None:
        config.receiver_outputs.binaural_hrtf.ir_catalog_path = fixture_config.receiver_outputs.binaural_hrtf.ir_catalog_path
    validate_config(config)
    assert config.receiver_outputs.circular_array.num_channels == expected_channels
    assert (config.receiver_outputs.binaural_hrtf is None) == (name == "circular_array_only")


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


def test_load_config_accepts_random_yaw_receiver_orientation(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))

    validate_config(config)

    strategy = config.receiver_sampling.orientation_strategy
    assert isinstance(strategy, ReceiverRandomYawOrientationStrategy)
    assert strategy.type == "random_yaw"
    assert strategy.yaw_deg.min == -180.0
    assert strategy.yaw_deg.max == 180.0
    assert strategy.pitch_deg.fixed == 0.0
    assert strategy.roll_deg.fixed == 0.0


def test_load_config_accepts_random_yaw_pitch_receiver_orientation(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _make_receiver_orientation_compatible(config_path)

    config = load_config(config_path)
    validate_config(config)

    strategy = config.receiver_sampling.orientation_strategy
    assert isinstance(strategy, ReceiverRandomYawPitchOrientationStrategy)
    assert strategy.type == "random_yaw_pitch"
    assert strategy.yaw_deg.min == -180.0
    assert strategy.yaw_deg.max == 180.0
    assert strategy.pitch_deg.min == strategy.pitch_deg.max == 0.0
    assert strategy.roll_deg.min == strategy.roll_deg.max == 0.0


def test_load_config_rejects_random_yaw_with_range_pitch(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "    pitch_deg: {fixed: 0.0}\n",
            "    pitch_deg: {min: 0.0, max: 0.0}\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match=r"receiver_sampling\.orientation_strategy\.random_yaw\.pitch_deg"):
        load_config(config_path)


def test_load_config_rejects_random_yaw_pitch_with_fixed_pitch(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _make_receiver_orientation_compatible(config_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "    pitch_deg: {min: 0.0, max: 0.0}\n",
            "    pitch_deg: {fixed: 0.0}\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValidationError,
        match=r"receiver_sampling\.orientation_strategy\.random_yaw_pitch\.pitch_deg",
    ):
        load_config(config_path)


def test_validate_config_rejects_reversed_random_yaw_pitch_ranges(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _make_receiver_orientation_compatible(config_path)
    config = load_config(config_path)
    strategy = config.receiver_sampling.orientation_strategy
    assert isinstance(strategy, ReceiverRandomYawPitchOrientationStrategy)
    strategy.pitch_deg.min = 1.0
    strategy.pitch_deg.max = -1.0
    strategy.roll_deg.min = 2.0
    strategy.roll_deg.max = -2.0

    with pytest.raises(ValueError) as exc_info:
        validate_config(config)

    message = str(exc_info.value)
    assert "receiver_sampling.orientation_strategy.pitch_deg.min" in message
    assert "receiver_sampling.orientation_strategy.roll_deg.min" in message


def test_room_sampling_reverberation_is_loaded(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _make_receiver_orientation_compatible(config_path)
    config = load_config(config_path)

    validate_config(config)

    assert config.room_sampling.reverberation.distribution.bin_width_s == 0.1


def test_load_config_rejects_legacy_room_sampling_max_rt30(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config_path.write_text(config_path.read_text(encoding="utf-8").replace(
        "room_sampling:\n", "room_sampling:\n  max_rt30_s: 1.0\n", 1
    ), encoding="utf-8")

    with pytest.raises(ValueError, match=r"room_sampling\.max_rt30_s ya no es válido"):
        load_config(config_path)


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


def test_validate_config_accepts_single_source_scenes(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, min_sources=1, max_sources=2, source_min_count=1, source_max_count=2))

    validate_config(config)


def test_validate_config_rejects_zero_minimum_sources(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, min_sources=0, max_sources=2, source_min_count=1, source_max_count=2))

    with pytest.raises(ValueError, match="source_sampling.min_sources debe ser >= 1"):
        validate_config(config)


def test_validate_config_accepts_fixed_position_lists(tmp_path: Path) -> None:
    config = load_config(_write_config(
        tmp_path,
        num_simulations=4,
        min_sources=1,
        max_sources=1,
        source_min_count=1,
        source_max_count=1,
        spatial_policy_block="""      spatial_policy:
        type: fixed_position
        azimuths_deg: [-90, 90]
        elevations_deg: [0, 15]
        distances_m: [1.0]
""".rstrip(),
    ))

    validate_config(config)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("azimuths_deg", "[]"),
        ("azimuths_deg", "[-181]"),
        ("azimuths_deg", "[0, 0]"),
        ("azimuths_deg", "[.nan]"),
        ("elevations_deg", "[91]"),
        ("elevations_deg", "[0, 0]"),
        ("distances_m", "[0]"),
        ("distances_m", "[1.0, 1.0]"),
        ("distances_m", "[.inf]"),
    ],
)
def test_validate_config_rejects_invalid_fixed_position_lists(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    values = {
        "azimuths_deg": "[0]",
        "elevations_deg": "[0]",
        "distances_m": "[1.0]",
    }
    values[field] = value
    policy = "      spatial_policy:\n        type: fixed_position\n" + "".join(
        f"        {name}: {configured}\n" for name, configured in values.items()
    )
    config = load_config(_write_config(
        tmp_path,
        min_sources=1,
        max_sources=1,
        source_min_count=1,
        source_max_count=1,
        spatial_policy_block=policy.rstrip(),
    ))

    with pytest.raises(ValueError, match=field):
        validate_config(config)


def test_validate_config_rejects_fixed_position_without_required_source(tmp_path: Path) -> None:
    config = load_config(_write_config(
        tmp_path,
        min_sources=1,
        source_min_count=0,
        spatial_policy_block="""      spatial_policy:
        type: fixed_position
        azimuths_deg: [0]
        elevations_deg: [0]
        distances_m: [1.0]
""".rstrip(),
    ))

    with pytest.raises(ValueError, match="fixed_position requiere min_count >= 1"):
        validate_config(config)


def test_validate_config_rejects_too_few_simulations_for_fixed_position_product(tmp_path: Path) -> None:
    config = load_config(_write_config(
        tmp_path,
        num_simulations=3,
        min_sources=1,
        max_sources=1,
        source_min_count=1,
        source_max_count=1,
        spatial_policy_block="""      spatial_policy:
        type: fixed_position
        azimuths_deg: [-90, 90]
        elevations_deg: [0, 15]
        distances_m: [1.0]
""".rstrip(),
    ))

    with pytest.raises(ValueError, match="num_simulations debe ser >="):
        validate_config(config)


def test_validate_config_rejects_duplicate_event_types(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    duplicate = """    - event_type: speech
      role: optional
      min_count: 0
      max_count: 0
      probability: 0.0
      audio_dir: ./assets/audio
      spatial_policy:
        type: random_valid
"""
    config_path.write_text(text.replace("scene_validation:\n", f"{duplicate}scene_validation:\n"), encoding="utf-8")

    with pytest.raises(ValueError, match="event_type debe ser único"):
        validate_config(load_config(config_path))


def test_legacy_room_and_retry_budget_are_normalized_once(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))

    assert config.room_sampling.geometry.height_m.min == 2.4
    assert len(config.room_sampling.geometry.shape_mix) == 1
    shoebox = config.room_sampling.geometry.shape_mix[0]
    assert shoebox.type == "shoebox"
    assert shoebox.probability == 1.0
    assert shoebox.length_m.min == 4.0
    assert shoebox.width_m.max == 3.5
    assert config.scene_validation.max_scene_attempts == 5
    assert config.scene_validation.max_receiver_attempts == 5


def test_load_and_validate_all_room_geometry_variants(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _replace_room_sampling_with_geometry(config_path)

    config = load_config(config_path)
    validate_config(config)

    assert [shape.type for shape in config.room_sampling.geometry.shape_mix] == [
        "shoebox",
        "trapezoid",
        "l_shape",
    ]


@pytest.mark.parametrize("probabilities", [(0.2, 0.2, 0.2), (0.5, 0.5, 0.0), (0.5, 0.5, float("nan"))])
def test_validate_config_rejects_invalid_shape_probabilities(
    tmp_path: Path,
    probabilities: tuple[float, float, float],
) -> None:
    config_path = _write_config(tmp_path)
    _replace_room_sampling_with_geometry(config_path)
    config = load_config(config_path)
    for shape, probability in zip(config.room_sampling.geometry.shape_mix, probabilities, strict=True):
        shape.probability = probability

    with pytest.raises(ValueError, match="probabil"):
        validate_config(config)


def test_validate_config_rejects_duplicate_or_empty_shape_mix(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _replace_room_sampling_with_geometry(config_path)
    config = load_config(config_path)
    config.room_sampling.geometry.shape_mix[1].type = "shoebox"  # type: ignore[assignment]

    with pytest.raises(ValueError, match="tipos duplicados"):
        validate_config(config)

    config = load_config(config_path)
    config.room_sampling.geometry.shape_mix = []
    with pytest.raises(ValueError, match="shape_mix no puede estar vacío"):
        validate_config(config)


def test_validate_config_rejects_invalid_ranges_and_l_shape_corners(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _replace_room_sampling_with_geometry(config_path)
    config = load_config(config_path)
    trapezoid = config.room_sampling.geometry.shape_mix[1]
    trapezoid.depth_m.min = float("inf")  # type: ignore[union-attr]
    l_shape = config.room_sampling.geometry.shape_mix[2]
    l_shape.removed_corners = []  # type: ignore[union-attr]

    with pytest.raises(ValueError) as exc_info:
        validate_config(config)

    message = str(exc_info.value)
    assert "depth_m debe contener valores finitos" in message
    assert "removed_corners no puede estar vacío" in message


def test_new_geometry_rejects_legacy_semantic_surfaces(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    _replace_room_sampling_with_geometry(config_path, include_semantic_surfaces=True)

    with pytest.raises(ValueError, match="semantic_surfaces solo se admite"):
        load_config(config_path)


def test_legacy_semantic_surfaces_are_consumed_by_normalization(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("    enable_floor: true", "    enable_floor: false"),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert not hasattr(config.room_sampling, "semantic_surfaces")


def test_validate_config_rejects_receiver_lateral_margin_below_half_meter(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    config.receiver_sampling.position_strategy.margin_m.x = 0.49

    with pytest.raises(ValueError, match=r"margin_m\.x y \.z deben ser >= 0\.5"):
        validate_config(config)


def test_loads_separate_scene_and_receiver_retry_budgets(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "  max_sampling_attempts_per_scene: 5",
            "  max_scene_attempts: 7\n  max_receiver_attempts: 11",
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    validate_config(config)

    assert config.scene_validation.max_scene_attempts == 7
    assert config.scene_validation.max_receiver_attempts == 11


def _replace_room_sampling_with_geometry(
    config_path: Path,
    *,
    include_semantic_surfaces: bool = False,
) -> None:
    text = config_path.read_text(encoding="utf-8")
    start = text.index("room_sampling:\n")
    end = text.index("source_sampling:\n")
    semantic_surfaces = (
        "  semantic_surfaces:\n"
        "    enable_walls: true\n"
        "    enable_floor: true\n"
        "    enable_ceiling: true\n"
        if include_semantic_surfaces
        else ""
    )
    replacement = (
        "room_sampling:\n"
        "  reverberation:\n"
        "    metric: estimated_rt30_s\n"
        "    estimator: sabine\n"
        "    estimator_version: sabine_polygon_octaves_v4\n"
        "    aggregation: arithmetic_mean\n"
        "    mean_bands_hz: [125, 250, 500, 1000, 2000, 4000, 8000]\n"
        "    distribution: {type: uniform, scope: global, range_s: {min: 0.1, max: 1.2}, bin_width_s: 0.1, quota_tolerance_fraction: 0.10}\n"
        "    treatment: {catalog_version: 1, mix_model: area_weighted_linear, mix_model_version: 1, eligible_surface_types: [wall, ceiling], max_treatments_per_surface: 1, preserve_base_scattering: true, allow_none: true, wall_coverage: {min: 0.0, max: 1.0}, ceiling_coverage: {min: 0.0, max: 1.0}}\n"
        "  geometry:\n"
        "    height_m: {min: 2.4, max: 2.8}\n"
        "    shape_mix:\n"
        "      - type: shoebox\n"
        "        probability: 0.3\n"
        "        length_m: {min: 4.0, max: 5.0}\n"
        "        width_m: {min: 3.0, max: 4.0}\n"
        "      - type: trapezoid\n"
        "        probability: 0.3\n"
        "        base_a_m: {min: 4.0, max: 5.0}\n"
        "        base_b_m: {min: 3.0, max: 5.0}\n"
        "        depth_m: {min: 3.0, max: 4.0}\n"
        "        top_offset_m: {min: -1.0, max: 1.0}\n"
        "      - type: l_shape\n"
        "        probability: 0.4\n"
        "        outer_length_m: {min: 6.0, max: 7.0}\n"
        "        outer_width_m: {min: 5.0, max: 6.0}\n"
        "        cutout_length_m: {min: 1.0, max: 2.0}\n"
        "        cutout_width_m: {min: 1.0, max: 2.0}\n"
        "        removed_corners: [north_east, south_west]\n"
        "  materials:\n"
        "    walls: [brick]\n"
        "    floor: [wood]\n"
        "    ceiling: [plaster]\n"
        f"{semantic_surfaces}"
    )
    config_path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


def _write_config(
    tmp_path: Path,
    directivity: str | None = None,
    *,
    num_simulations: int = 1,
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
            f"  num_simulations: {num_simulations}\n"
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
            "    margin_m: {x: 0.5, y: 0.1, z: 0.5}\n"
            "    fixed_height_m: {min: 1.2, max: 1.3}\n"
            "  orientation_strategy:\n"
            "    type: random_yaw\n"
            "    yaw_deg: {min: -180.0, max: 180.0}\n"
            "    pitch_deg: {fixed: 0.0}\n"
            "    roll_deg: {fixed: 0.0}\n"
            "receiver_outputs:\n"
            f"  binaural_hrtf:\n    enabled: true\n    ir_catalog_path: {assets_root.as_posix()}/hrtf\n    file_pattern: '*.daff'\n    output_subdir: binaural_hrtf\n    num_channels: 2\n    required: true\n"
            "room_sampling:\n"
            "  reverberation:\n"
            "    metric: estimated_rt30_s\n"
            "    estimator: sabine\n"
            "    estimator_version: sabine_polygon_octaves_v4\n"
            "    aggregation: arithmetic_mean\n"
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


def _make_receiver_orientation_compatible(config_path: Path) -> None:
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(
        "    type: random_yaw\n"
        "    yaw_deg: {min: -180.0, max: 180.0}\n"
        "    pitch_deg: {fixed: 0.0}\n"
        "    roll_deg: {fixed: 0.0}\n",
        "    type: random_yaw_pitch\n"
        "    yaw_deg: {min: -180.0, max: 180.0}\n"
        "    pitch_deg: {min: 0.0, max: 0.0}\n"
        "    roll_deg: {min: 0.0, max: 0.0}\n",
        1,
    )
    config_path.write_text(text, encoding="utf-8")
