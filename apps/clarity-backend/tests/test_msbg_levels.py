"""MSBG level handling, outer/middle ear reference, latency and pyclarity internals (design D4, D5, D10)."""

import importlib.metadata
import inspect
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from clarity.evaluator.msbg import cochlea as cochlea_module
from clarity.evaluator.msbg import msbg as pyclarity_msbg
from clarity.evaluator.msbg.msbg import Ear

from clarity_backend import msbg
from clarity_backend.cli import run_manifest
from test_cli import _build_job

FIXTURES = Path(__file__).parent / "fixtures"
BANDS = [str(f) for f in msbg.SUPPORTED_MSBG_FREQUENCIES_HZ]
NH_REFERENCE = {
    "hearing_profile_id": "nh_reference",
    "left_loss_db_by_band": {band: 0.0 for band in BANDS},
    "right_loss_db_by_band": {band: 0.0 for band in BANDS},
}
LEGACY_PROFILE = {
    "hearing_profile_id": "legacy_reference",
    "left_loss_db_by_band": {"250": 10.0, "500": 15.0, "1000": 20.0, "2000": 30.0, "4000": 40.0, "8000": 50.0},
    "right_loss_db_by_band": {"250": 20.0, "500": 25.0, "1000": 30.0, "2000": 40.0, "4000": 50.0, "8000": 60.0},
}


def _gated_speech_like(path: Path, *, duty: float, seconds: float = 0.8, amplitude: float = 0.05) -> Path:
    """Noise bursts with pauses: the active RMS is -10*log10(duty) dB above the full-file RMS."""
    fs = 44100
    t = np.arange(int(seconds * fs)) / fs
    gate = ((t % 0.2) < 0.2 * duty).astype(float)
    rng = np.random.default_rng(3)
    audio = gate[:, None] * amplitude * rng.standard_normal((t.size, 2))
    sf.write(path, audio, fs, subtype="FLOAT")
    return path


def _level_db(x: np.ndarray) -> np.ndarray:
    return 10 * np.log10(np.mean(np.square(x), axis=0))


def test_pyclarity_is_pinned() -> None:
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"pyclarity==0.8.0"' in pyproject
    assert importlib.metadata.version("pyclarity") == "0.8.0"


def test_sentinel_pyclarity_internals_used_by_true_level_and_eardrum() -> None:
    """Fails if pyclarity changes the internals that true_level and eardrum compensate."""
    assert list(inspect.signature(pyclarity_msbg.measure_rms).parameters)[:4] == [
        "signal", "sample_rate", "db_rel_rms", "percent_to_track",
    ]
    process_source = inspect.getsource(Ear.process)
    assert "equiv_0db_spl = self.equiv_0db_spl + self.ahr" in process_source
    assert "measure_rms(\n            signal[0], sample_rate, -12\n        )" in process_source
    assert "change_db = target_spl - (equiv_0db_spl + 20 * np.log10(calculated_rms))" in process_source
    assert "signal = signal * np.power(10, 0.05 * change_db)" in process_source
    assert "self.cochlea.simulate(x, equiv_0db_spl)" in process_source
    assert "Ear.src_to_cochlea_filt(signal, self.src_correction, sample_rate)" in process_source
    assert "correction = src_correction - MIDEAR" in inspect.getsource(Ear.src_to_cochlea_filt)
    assert inspect.signature(Ear).parameters["ahr"].default == msbg.MSBG_AHR_DB


def test_legacy_active_rescale_reproduces_previous_output_bit_exactly(tmp_path: Path) -> None:
    left, right, _ = msbg.build_audiograms(LEGACY_PROFILE)
    output = tmp_path / "out.wav"

    metadata = msbg.process_wav(
        FIXTURES / "msbg_legacy_input.wav", output, left, right,
        effective_scale_db_spl=90.0, outer_middle_ear="free_field", level_handling="legacy_active_rescale",
    )

    produced, _ = sf.read(output, dtype="int16")
    reference, _ = sf.read(FIXTURES / "msbg_legacy_output.wav", dtype="int16")
    assert np.array_equal(produced, reference)
    assert metadata["latency_compensated"] is False
    assert metadata["latency_compensation_samples"] == 0


def test_true_level_does_not_attenuate_by_active_level_unlike_legacy(tmp_path: Path) -> None:
    source = _gated_speech_like(tmp_path / "in.wav", duty=0.25)  # active RMS 6 dB above full RMS
    x, fs = sf.read(source)
    assert msbg.active_rescale_db(x[:, 0], fs) == pytest.approx(-6.0, abs=0.5)
    left, right, _ = msbg.build_audiograms(NH_REFERENCE)

    gains = {}
    for handling in ("true_level", "legacy_active_rescale"):
        msbg.process_wav(source, tmp_path / f"{handling}.wav", left, right, level_handling=handling)
        y, _ = sf.read(tmp_path / f"{handling}.wav")
        gains[handling] = _level_db(y) - _level_db(x)

    assert np.all(np.abs(gains["true_level"]) < 1.5)
    assert np.all(gains["legacy_active_rescale"] < -4.5)


def test_true_level_simulates_active_frames_at_their_real_spl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _gated_speech_like(tmp_path / "in.wav", duty=0.25)
    x, fs = sf.read(source)
    seen: list[tuple[np.ndarray, float]] = []
    original = cochlea_module.Cochlea.simulate

    def spy(self, coch_sig, equiv_0db_file_spl):
        seen.append((np.array(coch_sig), float(equiv_0db_file_spl)))
        return original(self, coch_sig, equiv_0db_file_spl)

    monkeypatch.setattr(cochlea_module.Cochlea, "simulate", spy)
    left, right, _ = msbg.build_audiograms(LEGACY_PROFILE)
    k = 95.0

    msbg.process_wav(source, tmp_path / "out.wav", left, right, effective_scale_db_spl=k)

    active_rms, *_ = pyclarity_msbg.measure_rms(x[:, 0], fs, -12)
    rescale = msbg.active_rescale_db(x[:, 0], fs)
    _, equiv = seen[0]
    # Ear rescales the signal by `rescale` dB; the active frames then sit at equiv + their rescaled RMS.
    simulated_active_spl = equiv + 20 * np.log10(active_rms) + rescale
    assert simulated_active_spl == pytest.approx(k + 20 * np.log10(active_rms), abs=1e-9)


@pytest.mark.parametrize("outer_middle_ear, src_pos", [("free_field", "ff"), ("diffuse_field", "df"), ("itu", "ITU")])
def test_outer_middle_ear_maps_to_native_msbg_corrections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outer_middle_ear: str, src_pos: str,
) -> None:
    corrections: list[np.ndarray] = []
    original = Ear.src_to_cochlea_filt

    def spy(signal, src_correction, sample_rate, backward=False):
        corrections.append(np.array(src_correction))
        return original(signal, src_correction, sample_rate, backward)

    monkeypatch.setattr(Ear, "src_to_cochlea_filt", staticmethod(spy))
    left, right, _ = msbg.build_audiograms(NH_REFERENCE)
    metadata = msbg.process_wav(
        _gated_speech_like(tmp_path / "in.wav", duty=1.0, seconds=0.2), tmp_path / "out.wav", left, right,
        outer_middle_ear=outer_middle_ear,
    )
    assert np.array_equal(corrections[0], Ear.get_src_correction(src_pos))
    assert metadata["outer_middle_ear"] == outer_middle_ear


def test_eardrum_applies_only_the_middle_ear_transfer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corrections: list[np.ndarray] = []
    original = Ear.src_to_cochlea_filt

    def spy(signal, src_correction, sample_rate, backward=False):
        corrections.append(np.array(src_correction))
        return original(signal, src_correction, sample_rate, backward)

    monkeypatch.setattr(Ear, "src_to_cochlea_filt", staticmethod(spy))
    left, right, _ = msbg.build_audiograms(NH_REFERENCE)
    metadata = msbg.process_wav(
        _gated_speech_like(tmp_path / "in.wav", duty=1.0, seconds=0.2), tmp_path / "out.wav", left, right,
        outer_middle_ear="eardrum",
    )
    assert corrections and all(not np.any(correction) for correction in corrections)
    assert metadata["src_pos"] == "eardrum (middle ear only)"


@pytest.mark.parametrize("outer_middle_ear", ["free_field", "itu", "eardrum"])
def test_impulse_output_is_aligned_with_input(tmp_path: Path, outer_middle_ear: str) -> None:
    fs = 44100
    impulse = np.zeros((fs // 2, 2))
    impulse[10000, :] = 0.5
    sf.write(tmp_path / "impulse.wav", impulse, fs, subtype="FLOAT")
    left, right, _ = msbg.build_audiograms(NH_REFERENCE)

    metadata = msbg.process_wav(
        tmp_path / "impulse.wav", tmp_path / "out.wav", left, right, outer_middle_ear=outer_middle_ear,
    )

    y, _ = sf.read(tmp_path / "out.wav")
    assert y.shape == impulse.shape
    assert np.all(np.abs(np.argmax(np.abs(y), axis=0) - 10000) <= 1)
    assert metadata["latency_compensated"] is True
    assert metadata["latency_compensation_samples"] == msbg.msbg_latency_samples(outer_middle_ear) > 0


def test_silent_channel_stays_silent(tmp_path: Path) -> None:
    fs = 44100
    audio = np.zeros((fs // 4, 2))
    audio[:, 0] = 0.05 * np.random.default_rng(1).standard_normal(fs // 4)
    sf.write(tmp_path / "in.wav", audio, fs, subtype="FLOAT")
    left, right, _ = msbg.build_audiograms(NH_REFERENCE)

    metadata = msbg.process_wav(tmp_path / "in.wav", tmp_path / "out.wav", left, right)

    y, _ = sf.read(tmp_path / "out.wav")
    assert not np.any(y[:, 1]) and np.any(y[:, 0])
    assert metadata["channel_levels"]["right"]["silent"] is True
    assert metadata["channel_levels"]["left"]["silent"] is False


def test_true_level_output_is_float32_and_keeps_values_above_full_scale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf.write(tmp_path / "in.wav", 0.1 * np.ones((4410, 2)), 44100, subtype="PCM_16")
    monkeypatch.setattr(msbg, "_process_channel", lambda channel, *args: (np.full(channel.shape, 1.05), -1.0))
    left, right, _ = msbg.build_audiograms(NH_REFERENCE)

    metadata = msbg.process_wav(tmp_path / "in.wav", tmp_path / "out.wav", left, right)

    y, _ = sf.read(tmp_path / "out.wav")
    assert sf.info(tmp_path / "out.wav").subtype == "FLOAT"
    assert metadata["output_subtype"] == "FLOAT" and metadata["output_peak"] == pytest.approx(1.05)
    assert np.allclose(y[:2000], 1.05, atol=1e-6)  # latency compensation pads the tail with zeros


def test_legacy_integer_output_above_full_scale_fails_instead_of_clipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf.write(tmp_path / "in.wav", 0.1 * np.ones((4410, 2)), 44100, subtype="PCM_16")
    monkeypatch.setattr(msbg, "_process_channel", lambda channel, *args: (np.full(channel.shape, 1.05), None))
    left, right, _ = msbg.build_audiograms(NH_REFERENCE)

    with pytest.raises(ValueError, match="Recorte"):
        msbg.process_wav(tmp_path / "in.wav", tmp_path / "out.wav", left, right, level_handling="legacy_active_rescale")
    assert not (tmp_path / "out.wav").exists()


def _extended_catalog(path: Path, *, extended: bool) -> Path:
    extra_band = "          125: 5\n" if extended else ""
    ch = "        compression_health: 0.5\n" if extended else ""
    bands = "".join(f"          {band}: {10 + i * 5}\n" for i, band in enumerate(BANDS))
    path.write_text(
        "profiles:\n  - hearing_profile_id: mild_loss\n    ears:\n"
        f"      left:\n        loss_db_by_band:\n{extra_band}{bands}{ch}"
        f"      right:\n        loss_db_by_band:\n{extra_band}{bands}{ch}",
        encoding="utf-8",
    )
    return path


def test_msbg_ignores_band_125_and_compression_health_traceably(tmp_path: Path) -> None:
    source = _gated_speech_like(tmp_path / "in.wav", duty=0.5, seconds=0.3)
    render = tmp_path / "render.json"
    render.write_text("{}", encoding="utf-8")
    outputs = {}
    for extended in (False, True):
        catalog = _extended_catalog(tmp_path / f"profiles_{extended}.yaml", extended=extended)
        job = _build_job(tmp_path, source, render, catalog, output_suffix=f"ext_{extended}")
        manifest = tmp_path / f"jobs_{extended}.jsonl"
        manifest.write_text(json.dumps(job), encoding="utf-8")
        run_manifest(manifest)
        outputs[extended] = (sf.read(job["expected_output_wav_path"])[0],
                             json.loads(Path(job["expected_output_metadata_path"]).read_text(encoding="utf-8")))

    assert np.array_equal(outputs[False][0], outputs[True][0])
    assert outputs[False][1]["ignored_profile_fields"] == {"left": [], "right": []}
    assert outputs[True][1]["ignored_profile_fields"] == {
        "left": ["loss_db_by_band.125", "compression_health"],
        "right": ["loss_db_by_band.125", "compression_health"],
    }
    assert "125" not in outputs[True][1]["degradation_applied"]["left"]["loss_db_by_band"]


def test_backend_uses_the_job_scale_and_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _gated_speech_like(tmp_path / "in.wav", duty=0.5, seconds=0.1)
    render = tmp_path / "render.json"
    render.write_text("{}", encoding="utf-8")
    job = _build_job(tmp_path, source, render, _extended_catalog(tmp_path / "p.yaml", extended=False))
    job["calibration"]["effective_scale_db_spl"] = 97.5
    job["outer_middle_ear"] = "itu"
    job["processor_config"] = {"level_handling": "legacy_active_rescale"}
    seen = {}

    def fake(input_wav_path, output_wav_path, left, right, **kwargs):
        seen.update(kwargs)
        output_wav_path.write_bytes(b"wav")
        return {"name": "msbg"}

    monkeypatch.setattr(msbg, "process_wav", fake)
    (tmp_path / "jobs.jsonl").write_text(json.dumps(job), encoding="utf-8")
    run_manifest(tmp_path / "jobs.jsonl")

    assert seen == {"effective_scale_db_spl": 97.5, "outer_middle_ear": "itu", "level_handling": "legacy_active_rescale"}
    sidecar = json.loads(Path(job["expected_output_metadata_path"]).read_text(encoding="utf-8"))
    assert sidecar["calibration"]["effective_scale_db_spl"] == 97.5
    assert sidecar["calibration"]["level_handling"] == "legacy_active_rescale"
    assert sidecar["outer_middle_ear"] == "itu"


def test_job_without_scale_fails(tmp_path: Path) -> None:
    source = _gated_speech_like(tmp_path / "in.wav", duty=0.5, seconds=0.1)
    render = tmp_path / "render.json"
    render.write_text("{}", encoding="utf-8")
    job = _build_job(tmp_path, source, render, _extended_catalog(tmp_path / "p.yaml", extended=False))
    job["calibration"]["effective_scale_db_spl"] = None
    (tmp_path / "jobs.jsonl").write_text(json.dumps(job), encoding="utf-8")

    with pytest.raises(SystemExit):
        run_manifest(tmp_path / "jobs.jsonl")

    sidecar = json.loads(Path(job["expected_output_metadata_path"]).read_text(encoding="utf-8"))
    assert sidecar["status"] == "failed"
    assert "effective_scale_db_spl" in sidecar["error"]["message"]
    assert sidecar["processor"] == {"name": "msbg"}
