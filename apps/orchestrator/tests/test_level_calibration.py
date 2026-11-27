import math
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from acoustic_orchestrator.config.models import HearingDegradationConfig, LevelCalibrationConfig
from acoustic_orchestrator.pipeline.level_calibration import (
    compute_render_calibration,
    effective_calibration,
    effective_outer_middle_ear,
    load_level_calibration_index,
    sample_presentation_level,
    upsert_level_calibration_records,
)


def _hearing(calibration: dict | None = None, **extra) -> HearingDegradationConfig:
    return HearingDegradationConfig(
        input_targets=["binaural_hrtf"],
        hearing_profiles_path=Path("profiles.yaml"),
        calibration=LevelCalibrationConfig(**calibration) if calibration else None,
        **extra,
    )


def _wav(path: Path, left_rms: float, right_rms: float, *, samples: int = 4410) -> Path:
    rng = np.random.default_rng(0)
    noise = rng.standard_normal((samples, 2))
    noise /= np.sqrt(np.mean(noise**2, axis=0))
    audio = noise * np.array([left_rms, right_rms])
    sf.write(path, audio, 44100, subtype="FLOAT")
    return path


def test_default_calibration_is_fixed_90_db_and_marked_default() -> None:
    hearing = _hearing()
    assert effective_calibration(hearing) == {
        "mode": "fixed_scale", "configured": {"digital_rms1_spl_db": 90.0}, "is_default": True,
    }
    assert effective_outer_middle_ear(hearing) == "free_field"
    assert effective_outer_middle_ear(_hearing(outer_middle_ear="itu")) == "itu"


def test_fixed_scale_uses_configured_scale_and_per_channel_leq(tmp_path: Path) -> None:
    wav = _wav(tmp_path / "in.wav", 0.1, 0.05)
    record = compute_render_calibration(
        _hearing({"mode": "fixed_scale", "digital_rms1_spl_db": 100}), random_seed=1, variant_id="v", input_wav_path=wav,
    )
    assert record["effective_scale_db_spl"] == 100.0
    assert record["presentation_level_db_spl"] is None
    assert record["is_default"] is False and record["random_seed"] is None
    assert record["channels"]["left"]["leq_db_spl"] == pytest.approx(100 + 20 * math.log10(0.1))
    assert record["channels"]["right"]["leq_db_spl"] == pytest.approx(100 + 20 * math.log10(0.05))
    assert record["level_measure"] == "rms_unweighted_full_file"
    assert record["status"] == "ok"


def test_presentation_level_formula_and_preserved_ild(tmp_path: Path) -> None:
    wav = _wav(tmp_path / "in.wav", 0.2, 0.1)  # ILD = 6.02 dB
    record = compute_render_calibration(
        _hearing({"mode": "presentation_level", "presentation_level_db_spl": 65}),
        random_seed=1, variant_id="v", input_wav_path=wav,
    )
    expected_k = 65 - 10 * math.log10((0.2**2 + 0.1**2) / 2)
    assert record["effective_scale_db_spl"] == pytest.approx(expected_k)
    left, right = record["channels"]["left"]["leq_db_spl"], record["channels"]["right"]["leq_db_spl"]
    assert left - right == pytest.approx(20 * math.log10(2))
    binaural = 10 * math.log10((10 ** (left / 10) + 10 ** (right / 10)) / 2)
    assert binaural == pytest.approx(65)


def test_renders_with_different_digital_gain_get_k_shifted_by_the_gain(tmp_path: Path) -> None:
    hearing = _hearing({"mode": "presentation_level", "presentation_level_db_spl": 65})
    quiet = compute_render_calibration(hearing, random_seed=1, variant_id="a", input_wav_path=_wav(tmp_path / "a.wav", 0.05, 0.05))
    loud = compute_render_calibration(
        hearing, random_seed=1, variant_id="b", input_wav_path=_wav(tmp_path / "b.wav", 0.05 * 10 ** (12 / 20), 0.05 * 10 ** (12 / 20)),
    )
    assert quiet["effective_scale_db_spl"] - loud["effective_scale_db_spl"] == pytest.approx(12)
    assert quiet["channels"]["left"]["leq_db_spl"] == pytest.approx(loud["channels"]["left"]["leq_db_spl"])


def test_level_range_is_deterministic_per_seed_and_variant(tmp_path: Path) -> None:
    hearing = _hearing({"mode": "presentation_level", "presentation_level_range_db_spl": [55, 80]})
    wav = _wav(tmp_path / "in.wav", 0.1, 0.1)
    first = compute_render_calibration(hearing, random_seed=42, variant_id="scene_1__binaural_hrtf", input_wav_path=wav)
    again = compute_render_calibration(hearing, random_seed=42, variant_id="scene_1__binaural_hrtf", input_wav_path=wav)
    other_seed = compute_render_calibration(hearing, random_seed=43, variant_id="scene_1__binaural_hrtf", input_wav_path=wav)

    assert first == again
    assert 55 <= first["presentation_level_db_spl"] <= 80
    assert first["random_seed"] == 42
    assert other_seed["presentation_level_db_spl"] != first["presentation_level_db_spl"]
    assert other_seed["effective_scale_db_spl"] != first["effective_scale_db_spl"]


def test_level_range_sampling_covers_the_range_uniformly() -> None:
    levels = [sample_presentation_level(7, f"scene_{i}", 55.0, 80.0) for i in range(2000)]
    assert min(levels) >= 55 and max(levels) <= 80
    assert np.mean(levels) == pytest.approx(67.5, abs=0.6)
    assert sample_presentation_level(7, "x", 60.0, 60.0) == 60.0


def test_one_silent_channel_is_marked_without_infinite_levels(tmp_path: Path) -> None:
    wav = _wav(tmp_path / "in.wav", 0.1, 0.0)
    record = compute_render_calibration(_hearing(), random_seed=1, variant_id="v", input_wav_path=wav)
    assert record["status"] == "ok"
    assert record["silent_channels"] == ["right"]
    assert record["channels"]["right"] == {"mean_square": 0.0, "leq_db_spl": None, "silent": True}
    assert record["channels"]["left"]["silent"] is False


def test_fully_silent_render_with_presentation_level_has_undefined_scale(tmp_path: Path) -> None:
    wav = _wav(tmp_path / "in.wav", 0.0, 0.0)
    record = compute_render_calibration(
        _hearing({"mode": "presentation_level", "presentation_level_db_spl": 65}),
        random_seed=1, variant_id="v", input_wav_path=wav,
    )
    assert record["status"] == "undefined_silent_input"
    assert record["effective_scale_db_spl"] is None
    assert record["silent_channels"] == ["left", "right"]


def test_fully_silent_render_with_fixed_scale_keeps_its_scale(tmp_path: Path) -> None:
    record = compute_render_calibration(
        _hearing(), random_seed=1, variant_id="v", input_wav_path=_wav(tmp_path / "in.wav", 0.0, 0.0),
    )
    assert record["status"] == "ok" and record["effective_scale_db_spl"] == 90.0


def test_calibration_index_roundtrip(tmp_path: Path) -> None:
    index_path = tmp_path / "metadata" / "indexes" / "level_calibration.jsonl"
    record = compute_render_calibration(
        _hearing(), random_seed=1, variant_id="v1", input_wav_path=_wav(tmp_path / "in.wav", 0.1, 0.1),
    )
    records = load_level_calibration_index(index_path)
    upsert_level_calibration_records(index_path, records, [record])
    assert load_level_calibration_index(index_path) == {"v1": record}


def test_mono_input_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "mono.wav"
    sf.write(path, np.zeros(100), 44100)
    with pytest.raises(ValueError, match="estéreo"):
        compute_render_calibration(_hearing(), random_seed=1, variant_id="v", input_wav_path=path)
