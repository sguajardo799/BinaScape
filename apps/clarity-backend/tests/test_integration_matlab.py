"""Integration checks with real MATLAB + WHIS (tasks 8.1-8.3). Opt-in, slow.

Run with:
    BINASCAPE_WHIS_INSTALL_ROOT=C:/Tesis/whis pytest apps/clarity-backend/tests/test_integration_matlab.py

Skipped unless the variable points at an install_root with WHIS/WHISv302 and
gammachirp-filterbank/GCFBv234 and `matlab` is on PATH. Tolerances were fixed after
observing WHISv302/GCFBv234 on MATLAB R2026b and pyclarity 0.8.0 (see the simulator
validation guide).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import resample_poly, welch

from clarity_backend import msbg, whis
from clarity_backend.cli import run_manifest
from test_cli import _build_job

INSTALL_ROOT = os.environ.get("BINASCAPE_WHIS_INSTALL_ROOT")
pytestmark = pytest.mark.skipif(
    not INSTALL_ROOT
    or not (Path(INSTALL_ROOT) / "WHIS" / "WHISv302").is_dir()
    or not (Path(INSTALL_ROOT) / "gammachirp-filterbank" / "GCFBv234").is_dir()
    or shutil.which("matlab") is None,
    reason="requires BINASCAPE_WHIS_INSTALL_ROOT with WHIS/GCFB and MATLAB on PATH",
)

FS = 44100
NH = {str(band): 0 for band in (125, 250, 500, 1000, 2000, 4000, 8000)}
SLOPING = {"250": 10, "500": 15, "1000": 20, "2000": 35, "4000": 50, "8000": 60}
# Tolerances fixed from observations (MATLAB R2026b, WHISv302/GCFBv234, pyclarity 0.8.0, K = 100 dB):
# - WHIS nh_reference on speech: SNR 23.5 dB, broadband gain +0.002 dB, cross-correlation lag 0.
# - wrapper vs WHISv30_Batch at equal SrcSnd.SPLdB: max error below -60 dB of the peak.
# - white noise, nh_reference: broadband gain MSBG +0.22 dB, WHIS -0.61 dB;
#   -3 dB band MSBG 75-18077 Hz, WHIS 0-19811 Hz.
# - MSBG nh_reference is not waveform-identical: its gammatone resynthesis advances low
#   frequencies (100-300 Hz: -281 samples, 300-1000 Hz: -73) relative to the impulse-aligned
#   high frequencies, so speech cross-correlation peaks at -293 samples (see the guides).
WHIS_NH_IDENTITY_MIN_SNR_DB = 20.0
WHIS_BATCH_EQUIVALENCE_MAX_ERROR_DB = -60.0
NH_BROADBAND_GAIN_MAX_DB = 1.0
ALIGNMENT_TOLERANCE_SAMPLES = 1


def _speech(seconds: float = 1.5) -> np.ndarray:
    speech, fs = sf.read(Path(INSTALL_ROOT) / "WHIS" / "WHISv302" / "Snd_Hello123.wav")
    speech = speech if speech.ndim == 1 else speech[:, 0]
    return resample_poly(speech, 147, 160)[: int(seconds * FS)] if fs == 48000 else speech[: int(seconds * FS)]


def _catalog(path: Path) -> Path:
    def ear(loss, ch):
        return f"{{loss_db_by_band: {json.dumps(loss).replace('\"', '')}, compression_health: {ch}}}"

    path.write_text(
        "profiles:\n"
        f"  - hearing_profile_id: nh_reference\n    ears:\n      left: {ear(NH, 1)}\n      right: {ear(NH, 1)}\n"
        f"  - hearing_profile_id: sloping\n    ears:\n      left: {ear(SLOPING, 0.5)}\n      right: {ear(SLOPING, 0.5)}\n",
        encoding="utf-8",
    )
    return path


def _process(tmp_path: Path, audio: np.ndarray, *, processors=("msbg", "whis"), profile="nh_reference",
             k=100.0, tag="run") -> dict[str, np.ndarray]:
    work = tmp_path / tag
    work.mkdir()
    source = work / "scene__binaural_hrtf.wav"
    sf.write(source, audio, FS, subtype="FLOAT")
    render = work / "render.json"
    render.write_text("{}", encoding="utf-8")
    catalog = _catalog(work / "profiles.yaml")
    jobs = []
    for processor in processors:
        job = _build_job(work, source, render, catalog, hearing_profile_id=profile)
        out = work / "degraded" / processor
        job.update(
            job_id=f"clarity__scene__{profile}__{processor}", processor=processor, output_dir=str(out),
            expected_output_wav_path=str(out / source.name), expected_output_metadata_path=str(out / "scene.json"),
            outer_middle_ear="itu",
            processor_config=(
                {"level_handling": "true_level"} if processor == "msbg"
                else {"install_root": INSTALL_ROOT, "matlab_executable": "matlab", "synth_method": "dtvf"}
            ),
        )
        job["calibration"] = {**job["calibration"], "effective_scale_db_spl": k}
        jobs.append(job)
    manifest = work / "jobs.jsonl"
    manifest.write_text("".join(json.dumps(job) + "\n" for job in jobs), encoding="utf-8")
    run_manifest(manifest)
    return {job["processor"]: sf.read(job["expected_output_wav_path"])[0] for job in jobs}


def _level_db(x: np.ndarray) -> np.ndarray:
    return 10 * np.log10(np.mean(np.square(x), axis=0))


def test_8_1_whis_nh_reference_is_identity(tmp_path: Path) -> None:
    speech = _speech()
    audio = np.column_stack((speech, 0.5 * speech))
    y = _process(tmp_path, audio, processors=("whis",))["whis"]
    snr_db = 10 * np.log10(np.sum(audio**2, axis=0) / np.sum((y - audio) ** 2, axis=0))
    lag = int(np.argmax(np.correlate(y[:, 0], audio[:, 0], "full"))) - (len(audio) - 1)
    assert y.shape == audio.shape
    assert np.all(snr_db > WHIS_NH_IDENTITY_MIN_SNR_DB), snr_db
    assert np.all(np.abs(_level_db(y) - _level_db(audio)) < 0.1)
    assert lag == 0


def test_8_1_wrapper_matches_whis_batch_at_equal_srcsnd_level(tmp_path: Path) -> None:
    speech48, _ = sf.read(Path(INSTALL_ROOT) / "WHIS" / "WHISv302" / "Snd_Hello123.wav")
    k = 100.0
    src_spl = k + 20 * np.log10(np.sqrt(np.mean(speech48**2)))
    hl = [10, 4, 10, 13, 48, 58, 79]
    out = tmp_path / "batch.wav"
    snd = (Path(INSTALL_ROOT) / "WHIS" / "WHISv302" / "Snd_Hello123.wav").as_posix()
    statement = (
        f"addpath('{(Path(INSTALL_ROOT) / 'WHIS' / 'WHISv302').as_posix()}'); StartupWHIS; "
        f"[snd,fs]=audioread('{snd}'); P=struct(); P.fs=48000; P.HLoss.Type='HL0'; "
        f"P.HLoss.HearingLeveldB={hl}; P.HLoss.CompressionHealth=0.5*ones(1,7); "
        f"P.CalibTone.SPLdB={k - 26}; P.SrcSnd.SPLdB={src_spl}; P.GCparam.OutMidCrct='ITU'; "
        f"[y,src]=WHISv30_Batch(snd(:)',P); audiowrite('{out.as_posix()}',[y(:) src(:)],48000,'BitsPerSample',32);"
    )
    subprocess.run(["matlab", "-batch", statement], check=True, capture_output=True, timeout=900)
    batch = sf.read(out)[0]

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    paths = whis._write_task(task_dir, "mono", speech48, src_spl, k, [float(v) for v in hl], [0.5] * 7, "DTVF", "ITU")
    (tmp_path / "tasks.txt").write_text(paths["task"].as_posix() + "\n", encoding="utf-8")
    subprocess.run(whis.build_matlab_command("matlab", tmp_path / "tasks.txt", Path(INSTALL_ROOT)),
                   check=True, capture_output=True, timeout=900)
    wrapper = sf.read(paths["output"])[0]

    # Batch normalises its input to the calibration tone; express both in that domain.
    scale = np.sqrt(np.mean(batch[:, 1] ** 2)) / np.sqrt(np.mean(speech48**2))
    error_db = 20 * np.log10(np.max(np.abs(wrapper * scale - batch[:, 0])) / np.max(np.abs(batch[:, 0])))
    assert abs(20 * np.log10(scale)) < 0.1
    assert error_db < WHIS_BATCH_EQUIVALENCE_MAX_ERROR_DB, error_db


def test_8_2_processors_are_aligned_and_same_length_with_nh_reference(tmp_path: Path) -> None:
    impulse = np.zeros((FS, 2))
    impulse[FS // 3, :] = 0.5
    rng = np.random.default_rng(0)
    noise = 0.05 * rng.standard_normal((2 * FS, 2))
    outputs = _process(tmp_path, impulse, tag="impulse")
    noise_out = _process(tmp_path, noise, tag="noise")

    report = {}
    for processor, y in outputs.items():
        assert y.shape == impulse.shape
        assert np.all(np.abs(np.argmax(np.abs(y), axis=0) - FS // 3) <= ALIGNMENT_TOLERANCE_SAMPLES), processor
        f, p_in = welch(noise[:, 0], FS, nperseg=4096)
        _, p_out = welch(noise_out[processor][:, 0], FS, nperseg=4096)
        response = 10 * np.log10(p_out / p_in)
        passband = np.median(response[(f > 500) & (f < 4000)])
        inside = f[(response > passband - 3)]
        report[processor] = {
            "broadband_gain_db": float(np.mean(_level_db(noise_out[processor]) - _level_db(noise))),
            "bandwidth_3db_hz": [float(inside.min()), float(inside.max())],
        }
    print(json.dumps(report))
    for processor, values in report.items():
        assert abs(values["broadband_gain_db"]) < NH_BROADBAND_GAIN_MAX_DB, processor
        assert values["bandwidth_3db_hz"][1] > 15000, processor


def test_8_3_ild_order_and_level_dependence(tmp_path: Path) -> None:
    speech = _speech()
    ild = np.column_stack((speech, speech * 10 ** (-10 / 20)))
    outputs = _process(tmp_path, ild, profile="sloping", tag="ild")
    for processor, y in outputs.items():
        levels = _level_db(y)
        assert levels[0] > levels[1], processor

    low = _process(tmp_path, ild, profile="sloping", k=80.0, tag="k80")
    high = _process(tmp_path, ild, profile="sloping", k=100.0, tag="k100")
    for processor in ("msbg", "whis"):
        gain_low = _level_db(low[processor]) - _level_db(ild)
        gain_high = _level_db(high[processor]) - _level_db(ild)
        assert np.all(np.abs(gain_high - gain_low) > 1.0), (processor, gain_low, gain_high)
