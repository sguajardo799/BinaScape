"""Signal synthesis, noise, WAV export and T30 (numpy/scipy only; tasks 4.1–4.6)."""

import math
from pathlib import Path

import pytest

from pra_support import require_backend_dependencies, write_tone_wav

require_backend_dependencies()

import numpy as np  # noqa: E402
import soundfile  # noqa: E402

from acoustic_orchestrator.backends.pyroomacoustics import synthesis, t30  # noqa: E402
from acoustic_orchestrator.backends.pyroomacoustics.errors import InvalidRenderInput  # noqa: E402
from acoustic_orchestrator.pipeline.render_backends import get_render_backend  # noqa: E402
from acoustic_orchestrator.pipeline.render_validation import RenderValidation, validate_noise, validate_t30, validate_wav  # noqa: E402

FS = 44100


# --- 4.1 rounding ------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [(22050.5, 22051), (-0.5, -1), (2.5, 3), (0.5, 1), (2.4999, 2), (-2.5, -3), (0.0, 0)])
def test_round_half_away_from_zero(value: float, expected: int) -> None:
    assert synthesis.round_half_away_from_zero(value) == expected


def test_offset_uses_matlab_rounding() -> None:
    assert synthesis.source_offset_samples(22050.5 / FS, FS) == 22051
    assert synthesis.source_offset_samples(-1.0, FS) == 0


# --- 4.2 source preparation --------------------------------------------------------

def test_mono_source_normalized_then_gain() -> None:
    audio = np.array([[0.1], [-0.4], [0.2]])
    prepared = synthesis.prepare_source(audio, FS, FS, gain_db=-6.0)
    assert np.max(np.abs(prepared.signal)) == pytest.approx(0.99 * 10 ** (-6 / 20))
    assert prepared.original_num_channels == 1 and prepared.downmix_policy == "none"
    assert prepared.original_duration_s == pytest.approx(3 / FS)


def test_stereo_source_arithmetic_mean() -> None:
    audio = np.array([[0.2, 0.4], [0.0, -0.8], [0.5, 0.1]])
    prepared = synthesis.prepare_source(audio, FS, FS, gain_db=0.0)
    mean = audio.mean(axis=1)
    np.testing.assert_allclose(prepared.signal, mean * 0.99 / np.max(np.abs(mean)))
    assert (prepared.original_num_channels, prepared.downmix_policy) == (2, "arithmetic_mean")


def test_silent_source_unchanged() -> None:
    prepared = synthesis.prepare_source(np.zeros((100, 1)), FS, FS, gain_db=12.0)
    assert not np.any(prepared.signal)


def test_48k_source_resampled_polyphase() -> None:
    seconds = 0.5
    n = np.arange(int(48000 * seconds))
    audio = np.sin(2 * np.pi * 1000 * n / 48000)[:, None]
    prepared = synthesis.prepare_source(audio, 48000, FS, gain_db=0.0)
    assert len(prepared.signal) == int(FS * seconds)
    assert prepared.original_duration_s == pytest.approx(seconds)
    spectrum = np.abs(np.fft.rfft(prepared.signal))
    assert np.fft.rfftfreq(len(prepared.signal), 1 / FS)[np.argmax(spectrum)] == pytest.approx(1000, abs=3)


def test_gain_applied_after_normalization() -> None:
    loud = synthesis.prepare_source(np.array([[0.9], [0.1]]), FS, FS, gain_db=6.0)
    quiet = synthesis.prepare_source(np.array([[0.09], [0.01]]), FS, FS, gain_db=6.0)
    np.testing.assert_allclose(loud.signal, quiet.signal)
    assert np.max(np.abs(loud.signal)) == pytest.approx(0.99 * 10 ** (6 / 20))


# --- 4.3 convolution, offsets, trimming, guard ---------------------------------------

def _rirs(lengths: list[int], seed: int = 0) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [rng.normal(scale=0.01, size=length) for length in lengths]


def test_full_convolution_lengths_and_tail_trim() -> None:
    signal = np.ones(100) * 0.01
    rirs = _rirs([50, 80, 30])
    full = synthesis.render_source(signal, rirs, trim_reverb_tail=False)
    assert full.shape == (100 + 80 - 1, 3)
    np.testing.assert_allclose(full[:, 0], np.concatenate([np.convolve(signal, rirs[0]), np.zeros(30)]))
    trimmed = synthesis.render_source(signal, rirs, trim_reverb_tail=True)
    assert trimmed.shape == (100, 3)
    np.testing.assert_allclose(trimmed, full[:100])


def test_linear_mixing_two_sources() -> None:
    rng = np.random.default_rng(3)
    a, b = rng.normal(scale=0.01, size=500), rng.normal(scale=0.01, size=300)
    rirs_a, rirs_b = _rirs([200, 200], 1), _rirs([150, 220], 2)
    offset_a, offset_b = synthesis.source_offset_samples(0.001, FS), synthesis.source_offset_samples(0.0123, FS)
    ra = synthesis.render_source(a, rirs_a, trim_reverb_tail=False)
    rb = synthesis.render_source(b, rirs_b, trim_reverb_tail=False)
    together, guard = synthesis.mix_sources([ra, rb], [offset_a, offset_b], 2, None, FS)
    alone_a, _ = synthesis.mix_sources([ra], [offset_a], 2, None, FS)
    alone_b, _ = synthesis.mix_sources([rb], [offset_b], 2, None, FS)
    assert not guard["guard_applied"]
    summed = np.zeros_like(together)
    summed[: len(alone_a)] += alone_a
    summed[: len(alone_b)] += alone_b
    np.testing.assert_allclose(together, summed, atol=1e-6)
    assert len(together) == max(offset_a + len(ra), offset_b + len(rb))


@pytest.mark.parametrize("length", [10, 200000])
def test_target_duration_trims_or_pads(length: int) -> None:
    mix, _ = synthesis.mix_sources([np.full((length, 2), 0.1)], [0], 2, 2.0, FS)
    assert mix.shape == (88200, 2)
    assert synthesis.target_num_samples(1e-9, FS) == 1


def test_peak_guard_scales_to_limit() -> None:
    loud = np.array([[0.5, -2.0], [1.0, 0.1]])
    mix, guard = synthesis.mix_sources([loud], [0], 2, None, FS)
    assert guard == {"peak_before_guard": 2.0, "guard_applied": True}
    assert np.max(np.abs(mix)) == pytest.approx(0.999)
    np.testing.assert_allclose(mix, loud * 0.999 / 2.0)


# --- 4.4 background noise ----------------------------------------------------------

MATLAB_NOISE_KEYS = {"enabled", "applied", "skipped_reason", "signal_rms", "peak_before_guard", "peak_after_guard",
                     "guard_gain", "guard_limit", "guard_applied", "effective_snr_may_change", "layers"}
MATLAB_LAYER_KEYS = {"strategy_original", "strategy", "snr_db", "seed", "effective_seed", "source_fs", "resampled",
                     "path", "color", "source_num_samples", "source_num_channels", "duration_mode", "channel_mode",
                     "target_num_samples", "target_num_channels", "signal_rms", "rms_before_scale", "gain", "skipped",
                     "skipped_reason", "applied"}


def _mix(channels: int = 8, samples: int = 44100) -> np.ndarray:
    rng = np.random.default_rng(11)
    return rng.normal(scale=0.01, size=(samples, channels))


def test_pink_noise_at_10_db_pooled_snr() -> None:
    mix = _mix()
    config = {"enabled": True, "layers": [{"noise_id": "noise_0001", "strategy": "colored", "type": "pink",
                                            "color": "pink", "snr_db": 10.0}]}
    out, metadata = synthesis.apply_background_noise(mix, FS, config, 42)
    assert not metadata["guard_applied"]
    noise = out - mix
    snr = 20 * math.log10(math.sqrt(np.mean(mix ** 2)) / math.sqrt(np.mean(noise ** 2)))
    assert snr == pytest.approx(10.0, abs=0.01)
    layer = metadata["layers"][0]
    assert layer["channel_mode"] == "mono_replicated" and layer["effective_seed"] == 42
    # Pink: power falls ~3 dB/octave (energy per octave constant).
    spectrum = np.abs(np.fft.rfft(noise[:, 0])) ** 2
    freqs = np.fft.rfftfreq(len(noise), 1 / FS)
    low = spectrum[(freqs > 100) & (freqs < 200)].mean()
    high = spectrum[(freqs > 1600) & (freqs < 3200)].mean()
    assert 10 * math.log10(low / high) == pytest.approx(10 * math.log10(16), abs=1.5)


def test_noise_reproducible_and_seeded_per_layer() -> None:
    config = {"enabled": True, "layers": [
        {"strategy": "colored", "color": "white", "snr_db": 5.0},
        {"strategy": "colored", "color": "brown", "snr_db": 20.0, "seed": 7}]}
    first, meta_first = synthesis.apply_background_noise(_mix(3), FS, config, 100)
    second, meta_second = synthesis.apply_background_noise(_mix(3), FS, config, 100)
    np.testing.assert_array_equal(first, second)
    assert meta_first == meta_second
    assert [layer["effective_seed"] for layer in meta_first["layers"]] == [100.0, 7.0]
    _, unseeded = synthesis.apply_background_noise(_mix(3), FS, config, [])
    assert unseeded["layers"][0]["effective_seed"] == 1.0


def test_noise_metadata_keys_match_matlab_and_validator_accepts(tmp_path: Path) -> None:
    noise_path = write_tone_wav(tmp_path / "noise.wav", seconds=0.3, sample_rate_hz=22050)
    config = {"enabled": True, "layers": [
        {"strategy": "colored", "color": "pink", "snr_db": 10.0},
        {"strategy": "audio_folder", "type": "cafeteria", "path": noise_path.as_posix(), "snr_db": 15.0}]}
    mix = _mix(8, 4410)  # shorter than the resampled 0.3 s file → "trim"
    out, metadata = synthesis.apply_background_noise(mix, FS, config, 42)
    assert set(metadata) == MATLAB_NOISE_KEYS
    assert all(set(layer) == MATLAB_LAYER_KEYS for layer in metadata["layers"])
    audio_layer = metadata["layers"][1]
    assert audio_layer["strategy"] == "audio_file" and audio_layer["strategy_original"] == "audio_folder"
    assert audio_layer["resampled"] is True and audio_layer["duration_mode"] == "trim"
    result = RenderValidation(wav_info={"num_samples": out.shape[0], "num_channels": out.shape[1]},
                              metadata={"summary": {"background_noise": metadata}})
    validate_noise(result, {"render": {"seed": 42}, "background_noise": config})
    assert result.invalid == []


def test_disabled_and_silent_noise_metadata(tmp_path: Path) -> None:
    out, metadata = synthesis.apply_background_noise(_mix(2), FS, {"enabled": False, "layers": []}, 1)
    assert metadata["skipped_reason"] == "disabled" and metadata["applied"] is False
    config = {"enabled": True, "layers": [{"strategy": "colored", "color": "white", "snr_db": 0.0}]}
    _, silent = synthesis.apply_background_noise(np.zeros((100, 2)), FS, config, 1)
    assert silent["layers"][0]["skipped_reason"] == "silent_signal"
    assert silent["skipped_reason"] == "all_layers_skipped" and silent["applied"] is False
    quiet = write_tone_wav(tmp_path / "quiet.wav", seconds=0.01, amplitude=0.0)
    _, no_noise = synthesis.apply_background_noise(
        _mix(2), FS, {"enabled": True, "layers": [{"strategy": "audio_folder", "path": quiet.as_posix(), "snr_db": 3}]}, 1)
    assert no_noise["layers"][0]["skipped_reason"] == "silent_noise"
    result = RenderValidation(wav_info={"num_samples": 44100, "num_channels": 2},
                              metadata={"summary": {"background_noise": metadata}})
    validate_noise(result, {"render": {"seed": 1}, "background_noise": {"enabled": False, "layers": []}})
    assert result.invalid == []


def test_second_guard_recorded() -> None:
    mix = np.full((1000, 2), 0.9)
    config = {"enabled": True, "layers": [{"strategy": "colored", "color": "white", "snr_db": -6.0}]}
    out, metadata = synthesis.apply_background_noise(mix, FS, config, 5)
    assert metadata["guard_applied"] and metadata["effective_snr_may_change"]
    assert metadata["peak_after_guard"] == pytest.approx(0.999)
    assert metadata["guard_gain"] == pytest.approx(0.999 / metadata["peak_before_guard"])


def test_incompatible_noise_channels_rejected(tmp_path: Path) -> None:
    stereo = write_tone_wav(tmp_path / "stereo.wav", seconds=0.1, channels=2)
    layers = [{"strategy": "audio_folder", "path": stereo.as_posix(), "snr_db": 10.0}]
    with pytest.raises(InvalidRenderInput, match="channel mismatch: noise has 2 channel"):
        synthesis.check_noise_layers(layers, 8)
    with pytest.raises(InvalidRenderInput, match="incompatible"):
        synthesis.apply_background_noise(_mix(8, 1000), FS, {"enabled": True, "layers": layers}, 1)
    synthesis.check_noise_layers(layers, 2)


# --- 4.5 WAV writer -------------------------------------------------------------------

@pytest.mark.parametrize("channels", [3, 8])
def test_wav_writer_accepted_by_validator(tmp_path: Path, channels: int) -> None:
    audio = np.clip(_mix(channels, 4410) * 20, -0.999, 0.999)
    path = tmp_path / "nested" / "dir" / "out.wav"
    synthesis.write_wav(path, audio, FS)
    result = validate_wav(path, expected_channels=channels, sample_rate_hz=FS)
    assert result.artifacts_ok, result.invalid
    assert result.wav_info == {"num_channels": channels, "num_samples": 4410, "sample_rate_hz": FS, "sample_width_bytes": 2}
    info = soundfile.info(str(path))
    assert (info.format, info.subtype) == ("WAV", "PCM_16")
    decoded, _ = soundfile.read(str(path), dtype="float64", always_2d=True)
    np.testing.assert_allclose(decoded, audio, atol=1 / 32767)


def test_wav_writer_refuses_non_finite(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        synthesis.write_wav(tmp_path / "x.wav", np.full((10, 2), np.nan), FS)


# --- 4.6 T30 -----------------------------------------------------------------------------

def _synthetic_rir(t60: float, seed: int = 0, seconds: float | None = None, truncate_db: float | None = None) -> np.ndarray:
    seconds = seconds or max(1.6 * t60, 0.5)
    t = np.arange(int(seconds * FS)) / FS
    rir = np.random.default_rng(seed).standard_normal(len(t)) * 10 ** (-3 * t / t60)
    if truncate_db is not None:
        rir = rir[: int(-truncate_db / 60 * t60 * FS)]
    return rir


def _multisine_rir(t60: float) -> np.ndarray:
    """Every octave center decays with exactly ``t60``: a known envelope in every band."""
    t = np.arange(int(max(1.6 * t60, 0.5) * FS)) / FS
    tones = sum(np.cos(2 * np.pi * f * t + 0.3 * i) for i, f in enumerate(t30.OCTAVE_CENTER_FREQUENCIES_HZ))
    return tones * 10 ** (-3 * t / t60)


def _tolerance(frequency: float) -> float:
    return 0.10 if frequency < 100 else 0.05


@pytest.mark.parametrize("t60", [0.3, 0.6, 1.0])
def test_synthetic_decay_tolerances(t60: float) -> None:
    values = t30.pair_t30(_multisine_rir(t60), FS)
    for frequency, value in zip(t30.OCTAVE_CENTER_FREQUENCIES_HZ, values, strict=True):
        assert value is not None, frequency
        assert abs(value - t60) / t60 <= _tolerance(frequency), (frequency, value)


@pytest.mark.parametrize("t60", [0.3, 0.6, 1.0])
def test_noise_decay_unbiased_on_average(t60: float) -> None:
    """Single noise realizations scatter at low frequencies; the method itself is unbiased."""
    runs = np.array([t30.pair_t30(_synthetic_rir(t60, seed=seed), FS) for seed in range(20)], dtype=float)
    for frequency, mean in zip(t30.OCTAVE_CENTER_FREQUENCIES_HZ, runs.mean(axis=0), strict=True):
        assert abs(mean - t60) / t60 <= _tolerance(frequency), (frequency, mean)


def test_truncated_decay_is_null_and_unavailable() -> None:
    rir = _synthetic_rir(0.6, truncate_db=-20.0)
    descriptor, pairs = t30.measure_t30([[rir]], FS)
    assert all(value is None for value in pairs[0][0])
    assert descriptor["status"] == "unavailable" and descriptor["valid_band_count"] == 0
    assert descriptor["mean_t30_s"] == []
    result = RenderValidation(metadata={"summary": {"room": {"reverberation": descriptor}}})
    validate_t30(result, get_render_backend("pyroomacoustics"))
    assert result.acoustic_rejection["reason"] == "raven_t30_non_finite_or_non_positive"
    assert result.invalid == []


def test_band_null_if_any_pair_invalid_and_mean_over_pairs() -> None:
    good_a, good_b = _synthetic_rir(0.5, seed=1), _synthetic_rir(0.7, seed=2)
    descriptor, pairs = t30.measure_t30([[good_a, good_b]], FS)
    assert descriptor["status"] == "estimated" and descriptor["valid_band_count"] == 10
    expected = [(a + b) / 2 for a, b in zip(pairs[0][0], pairs[0][1], strict=True)]
    assert descriptor["t30_s"] == pytest.approx(expected)
    bad = _synthetic_rir(0.6, truncate_db=-20.0)
    mixed, _ = t30.measure_t30([[good_a], [bad]], FS)
    assert mixed["t30_s"] == [None] * 10


def test_descriptor_passes_backend_aware_gate_only_for_its_backend() -> None:
    descriptor, _ = t30.measure_t30([[_synthetic_rir(0.6)]], FS)
    assert descriptor["method"] == "schroeder_backward_integration_v1" and descriptor["estimated_from"] == "rir"
    accepted = RenderValidation(metadata={"summary": {"room": {"reverberation": descriptor}}})
    validate_t30(accepted, get_render_backend("pyroomacoustics"))
    assert accepted.acoustic_rejection is None and accepted.invalid == []
    rejected = RenderValidation(metadata={"summary": {"room": {"reverberation": descriptor}}})
    validate_t30(rejected, get_render_backend("raven"))
    assert "render_metadata_t30_method_inconsistent" in rejected.invalid
    assert "render_metadata_t30_estimated_from_inconsistent" in rejected.invalid


def test_band_edges_clip_at_nyquist() -> None:
    low, high = t30.octave_band_edges(16000.0, FS)
    assert low == pytest.approx(16000 / math.sqrt(2)) and high == pytest.approx(0.95 * FS / 2)
    assert t30.octave_band_edges(1000.0, FS)[1] == pytest.approx(1000 * math.sqrt(2))
