"""WHIS processor without MATLAB: band mapping, resampling, session protocol and sidecar."""

import json
import re
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from clarity_backend import whis
from clarity_backend.cli import run_manifest
from test_cli import _build_job

FAKE_DELAY = 37  # samples at 48 kHz added by the fake MATLAB, to check latency compensation
BANDS7 = [125, 250, 500, 1000, 2000, 4000, 8000]


# --- Unit: mapping, resampling, input validation ---------------------------------------------

def test_band_mapping_copies_250_into_missing_125_and_records_it() -> None:
    loss = {"250": 10.0, "500": 20.0, "1000": 30.0, "2000": 40.0, "4000": 50.0, "8000": 60.0}
    levels, health, extrapolated = whis.map_whis_bands(loss, 0.5, ear="left")
    assert levels == [10.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    assert health == [0.5] * 7
    assert extrapolated == ["125"]


def test_band_mapping_uses_declared_125_and_per_band_health() -> None:
    loss = {"125": 5.0, "250": 10.0, "500": 20.0, "1000": 30.0, "2000": 40.0, "4000": 50.0, "8000": 60.0}
    health = {band: value for band, value in zip(loss, (1, 1, 0.9, 0.8, 0.5, 0.3, 0.1))}
    levels, mapped, extrapolated = whis.map_whis_bands(loss, health, ear="right")
    assert levels[0] == 5.0 and extrapolated == []
    assert mapped == [1.0, 1.0, 0.9, 0.8, 0.5, 0.3, 0.1]


@pytest.mark.parametrize(
    "loss, health, message",
    [
        ({"250": 10.0}, 0.5, "requiere las bandas 500"),
        ({str(b): 0.0 for b in BANDS7} | {"3000": 0.0}, 0.5, "no soporta las bandas 3000"),
        ({str(b): (-5.0 if b == 1000 else 0.0) for b in BANDS7}, 0.5, "umbrales negativos .*1000"),
        ({str(b): 0.0 for b in BANDS7}, None, "requiere compression_health"),
    ],
)
def test_band_mapping_rejects_invalid_profiles(loss: dict, health, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        whis.map_whis_bands(loss, health, ear="left")


def test_resampling_roundtrip_keeps_exact_length_and_content() -> None:
    fs = 44100
    t = np.arange(441000) / fs
    x = 0.3 * np.sin(2 * np.pi * 1000 * t)
    up = whis.to_processing_rate(x, fs)
    assert up.shape[0] == 480000
    back = whis.from_processing_rate(up, fs, x.shape[0])
    assert back.shape == x.shape
    assert np.max(np.abs(back[1000:-1000] - x[1000:-1000])) < 1e-3
    assert np.array_equal(whis.to_processing_rate(x, 48000), x)


def test_unsupported_sample_rate_names_supported_ones(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="32000 Hz.*44100, 48000"):
        whis.validate_input(np.zeros((10, 2)), 32000, tmp_path / "x.wav")
    with pytest.raises(ValueError, match="2 canales"):
        whis.validate_input(np.zeros((10, 1)), 48000, tmp_path / "x.wav")


def test_matlab_command_quotes_paths(tmp_path: Path) -> None:
    command = whis.build_matlab_command("matlab-not-on-path", tmp_path / "it's" / "tasks.txt", tmp_path / "whis")
    assert command[0] == "matlab-not-on-path" and command[1] == "-batch"
    assert "it''s/tasks.txt" in command[2]
    assert "binascape_whis_run(" in command[2] and "addpath(" in command[2]


# --- Fake MATLAB session ---------------------------------------------------------------------

class FakeMatlab:
    """Emulates binascape_whis_run: output = input delayed by FAKE_DELAY, optional failures/death.

    fail_health makes every task with that compression health fail (severe_loss uses 0.2).
    """

    def __init__(self, *, fail_health: float | None = None, die_after: int | None = None, gain: float = 1.0):
        self.fail_health = fail_health
        self.die_after = die_after
        self.gain = gain
        self.commands: list[list[str]] = []
        self.returncode: int | None = None

    def __call__(self, command: list[str], log_path: Path):
        self.commands.append(command)
        task_list = Path(re.search(r"binascape_whis_run\('([^']+)'", command[2]).group(1))
        tasks = [line for line in task_list.read_text(encoding="utf-8").splitlines() if line.strip()]
        for index, task_path in enumerate(tasks):
            if self.die_after is not None and index >= self.die_after:
                self.returncode = 1
                return self
            task = json.loads(Path(task_path).read_text(encoding="utf-8"))
            if self.fail_health is not None and task["compression_health"][0] == self.fail_health:
                Path(task["error_json"]).write_text(json.dumps({"message": "boom in WHISv302"}), encoding="utf-8")
                continue
            signal, fs = sf.read(task["input_wav"])
            assert fs == 48000
            delayed = np.concatenate((np.zeros(FAKE_DELAY), signal))[: signal.shape[0]] * self.gain
            sf.write(task["output_wav"], delayed, 48000, subtype="FLOAT")
            Path(task["result_json"]).write_text(json.dumps({
                "whis_version": "WHISv302dtvf",
                "matlab_release": "2026b",
                "compression_health_requested": task["compression_health"],
                "compression_health_applied": [min(1.0, value + 0.1) for value in task["compression_health"]],
                "pin_loss_db_act": [level / 2 for level in task["hearing_level_db"]],
                "pin_loss_db_pas": [level / 2 for level in task["hearing_level_db"]],
                "out_mid_crct": task["out_mid_crct"],
                "synth_method": task["synth_method"],
            }), encoding="utf-8")
        self.returncode = 0
        return self

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode


def _profiles(path: Path) -> Path:
    bands = "{250: 10, 500: 15, 1000: 20, 2000: 30, 4000: 40, 8000: 50}"
    path.write_text(
        "profiles:\n"
        f"  - hearing_profile_id: mild_loss\n    ears:\n"
        f"      left:\n        loss_db_by_band: {bands}\n        compression_health: 0.5\n"
        f"      right:\n        loss_db_by_band: {bands}\n        compression_health: 0.5\n"
        f"  - hearing_profile_id: severe_loss\n    ears:\n"
        f"      left:\n        loss_db_by_band: {bands}\n        compression_health: 0.2\n"
        f"      right:\n        loss_db_by_band: {bands}\n        compression_health: 0.2\n"
        f"  - hearing_profile_id: other_loss\n    ears:\n"
        f"      left:\n        loss_db_by_band: {bands}\n        compression_health: 1\n"
        f"      right:\n        loss_db_by_band: {bands}\n        compression_health: 1\n",
        encoding="utf-8",
    )
    return path


def _input(path: Path, *, fs: int = 44100, seconds: float = 0.2, subtype: str = "PCM_16", right_gain: float = 0.5) -> Path:
    n = int(fs * seconds)
    audio = np.zeros((n, 2))
    audio[n // 3, :] = [0.5, 0.5 * right_gain]  # impulse to check alignment
    audio += 0.01 * np.random.default_rng(0).standard_normal((n, 2)) * [1, right_gain]
    sf.write(path, audio, fs, subtype=subtype)
    return path


def _whis_job(tmp_path: Path, input_wav: Path, profile: str = "mild_loss") -> dict:
    render = tmp_path / "render.json"
    render.write_text("{}", encoding="utf-8")
    job = _build_job(tmp_path, input_wav, render, _profiles(tmp_path / "profiles.yaml"), hearing_profile_id=profile)
    job["job_id"] = job["job_id"].replace("__msbg", "__whis")
    out_dir = tmp_path / "degraded" / "whis" / "binaural_hrtf" / profile
    job.update(
        processor="whis",
        output_dir=str(out_dir),
        expected_output_wav_path=str(out_dir / input_wav.name),
        expected_output_metadata_path=str(out_dir / f"{input_wav.stem}.json"),
        outer_middle_ear="itu",
        processor_config={"install_root": str(tmp_path / "whis_root"), "matlab_executable": "matlab", "synth_method": "dtvf"},
    )
    job["processing_signature"] = {**job["processing_signature"], "processor": "whis", "outer_middle_ear": "itu",
                                   "whis": {"synth_method": "dtvf"}}
    job["processing_signature"].pop("msbg")
    job["calibration"] = {**job["calibration"], "effective_scale_db_spl": 100.0}
    return job


def _run(tmp_path: Path, jobs: list[dict], fake: FakeMatlab, monkeypatch: pytest.MonkeyPatch) -> int | None:
    manifest = tmp_path / "jobs.jsonl"
    manifest.write_text("".join(json.dumps(job) + "\n" for job in jobs), encoding="utf-8")
    original = whis.run_whis_jobs
    monkeypatch.setattr(whis, "run_whis_jobs", lambda *a, **k: original(*a, **{**k, "launch": fake, "poll_interval_s": 0}))
    try:
        run_manifest(manifest)
    except SystemExit as exc:
        return exc.code
    return None


def _sidecar(job: dict) -> dict:
    return json.loads(Path(job["expected_output_metadata_path"]).read_text(encoding="utf-8"))


def test_whis_job_completes_with_aligned_output_and_full_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _input(tmp_path / "scene_static_0001__binaural_hrtf.wav")
    job = _whis_job(tmp_path, source)

    assert _run(tmp_path, [job], FakeMatlab(), monkeypatch) is None

    x, fs = sf.read(source)
    y, out_fs = sf.read(job["expected_output_wav_path"])
    assert out_fs == fs == 44100 and y.shape == x.shape
    assert sf.info(job["expected_output_wav_path"]).subtype == "FLOAT"
    assert np.all(np.abs(np.argmax(np.abs(y), axis=0) - x.shape[0] // 3) <= 1)

    sidecar = _sidecar(job)
    # Fields the orchestrator validates for a completed result (task 2.4).
    assert sidecar["status"] == "completed"
    assert sidecar["output_wav_path"] == Path(job["expected_output_wav_path"]).resolve().as_posix()
    assert sidecar["processor"]["name"] == job["processor"]
    assert sidecar["processing_signature"] == job["processing_signature"]
    assert sidecar["calibration"]["effective_scale_db_spl"] == job["calibration"]["effective_scale_db_spl"]
    assert isinstance(sidecar["degradation_applied"]["left"], dict)
    assert isinstance(sidecar["degradation_applied"]["right"], dict)

    processor = sidecar["processor"]
    assert processor["whis_version"] == "WHISv302" and processor["gcfb_version"] == "GCFBv234"
    assert processor["whis_commit"] is None and processor["gcfb_commit"] is None
    assert processor["matlab_release"] == "2026b"
    assert processor["synth_method"] == "dtvf" and processor["outer_middle_ear"] == "itu"
    assert processor["out_mid_crct"] == "ITU"
    assert processor["processing_sample_rate_hz"] == 48000 and processor["resampled"] is True
    assert processor["latency_compensation_samples"] == FAKE_DELAY
    assert processor["output_peak"] == pytest.approx(float(np.max(np.abs(y))), abs=1e-6)
    assert processor["output_subtype"] == "FLOAT"
    assert sidecar["outer_middle_ear"] == "itu"
    assert sidecar["ignored_profile_fields"] == {"left": [], "right": []}

    left = sidecar["degradation_applied"]["left"]
    assert left["extrapolated_bands"] == ["125"]
    assert left["loss_db_by_band"]["125"] == left["loss_db_by_band"]["250"] == 10.0
    assert left["compression_health_requested"]["1000"] == 0.5
    assert left["compression_health_applied"]["1000"] == pytest.approx(0.6)
    assert left["pin_loss_db_act"]["8000"] == 25.0 and left["pin_loss_db_pas"]["8000"] == 25.0
    assert [path.name for path in (tmp_path / "whis_work").iterdir() if path.is_dir()] == ["lat_itu_DTVF"]


def test_each_channel_is_simulated_at_its_own_spl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _input(tmp_path / "in.wav", right_gain=10 ** (-10 / 20), subtype="FLOAT")
    job = _whis_job(tmp_path, source)
    seen: list[dict] = []

    class Spy(FakeMatlab):
        def __call__(self, command, log_path):
            task_list = Path(re.search(r"binascape_whis_run\('([^']+)'", command[2]).group(1))
            for line in task_list.read_text(encoding="utf-8").splitlines():
                seen.append(json.loads(Path(line).read_text(encoding="utf-8")))
            return super().__call__(command, log_path)

    _run(tmp_path, [job], Spy(), monkeypatch)

    tasks = {Path(task["input_wav"]).stem: task for task in seen}
    assert tasks["in_left"]["srcsnd_spl_db"] - tasks["in_right"]["srcsnd_spl_db"] == pytest.approx(10, abs=0.05)
    assert tasks["in_left"]["calib_tone_spl_db"] == 74.0
    assert tasks["in_left"]["out_mid_crct"] == "ITU" and tasks["in_left"]["synth_method"] == "DTVF"
    x, _ = sf.read(source)
    expected_left = 100 + 20 * np.log10(np.sqrt(np.mean(whis.to_processing_rate(x[:, 0], 44100) ** 2)))
    assert tasks["in_left"]["srcsnd_spl_db"] == pytest.approx(expected_left)
    assert any(Path(task["input_wav"]).stem == "in_impulse" for task in seen)  # latency probe


def test_one_failing_task_fails_only_its_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _input(tmp_path / "in.wav")
    jobs = [_whis_job(tmp_path, source, profile) for profile in ("mild_loss", "severe_loss", "other_loss")]

    code = _run(tmp_path, jobs, FakeMatlab(fail_health=0.2), monkeypatch)

    assert code == 1
    statuses = {job["hearing_profile_id"]: _sidecar(job)["status"] for job in jobs}
    assert statuses == {"mild_loss": "completed", "severe_loss": "failed", "other_loss": "completed"}
    failed = _sidecar(jobs[1])
    assert "boom in WHISv302" in failed["error"]["message"]
    assert failed["processor"] == {"name": "whis"}
    assert failed["output_wav_path"] is None


def test_session_death_fails_every_job_without_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _input(tmp_path / "in.wav")
    jobs = [_whis_job(tmp_path, source, profile) for profile in ("mild_loss", "severe_loss", "other_loss")]

    # probe + 2 channels of the first job, then MATLAB dies.
    code = _run(tmp_path, jobs, FakeMatlab(die_after=3), monkeypatch)

    assert code == 1
    assert _sidecar(jobs[0])["status"] == "completed"
    for job in jobs[1:]:
        sidecar = _sidecar(job)
        assert sidecar["status"] == "failed"
        assert sidecar["error"]["type"] == "WhisSessionError"
        assert "exit code 1" in sidecar["error"]["message"]


def test_mixed_manifest_processes_msbg_and_whis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _input(tmp_path / "in.wav", seconds=0.3)
    render = tmp_path / "render.json"
    render.write_text("{}", encoding="utf-8")
    msbg_job = _build_job(tmp_path, source, render, _profiles(tmp_path / "profiles.yaml"))
    whis_job = _whis_job(tmp_path, source)
    fake = FakeMatlab()

    assert _run(tmp_path, [msbg_job, whis_job], fake, monkeypatch) is None

    assert len(fake.commands) == 1
    assert _sidecar(msbg_job)["processor"]["name"] == "msbg"
    assert _sidecar(whis_job)["processor"]["name"] == "whis"
    assert Path(msbg_job["expected_output_wav_path"]).parent != Path(whis_job["expected_output_wav_path"]).parent


def test_whis_output_above_full_scale_is_kept_in_float32(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _input(tmp_path / "in.wav")  # PCM_16 input
    job = _whis_job(tmp_path, source)

    assert _run(tmp_path, [job], FakeMatlab(gain=3.0), monkeypatch) is None

    y, _ = sf.read(job["expected_output_wav_path"])
    sidecar = _sidecar(job)
    assert sidecar["status"] == "completed"
    assert sidecar["processor"]["output_peak"] > 1.0
    assert np.max(np.abs(y)) == pytest.approx(sidecar["processor"]["output_peak"], rel=1e-6)


def test_silent_channel_is_not_sent_to_matlab_and_stays_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "in.wav"
    audio = np.zeros((9600, 2))
    audio[3000, 0] = 0.5
    sf.write(source, audio, 48000, subtype="FLOAT")
    job = _whis_job(tmp_path, source)

    _run(tmp_path, [job], FakeMatlab(), monkeypatch)

    y, _ = sf.read(job["expected_output_wav_path"])
    assert not np.any(y[:, 1]) and np.argmax(np.abs(y[:, 0])) == 3000
    sidecar = _sidecar(job)
    assert sidecar["processor"]["resampled"] is False
    assert sidecar["processor"]["channel_levels"]["right"] == {"silent": True, "srcsnd_spl_db": None}
    assert sidecar["degradation_applied"]["right"]["compression_health_applied"] is None


def test_unsupported_rate_fails_before_matlab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _input(tmp_path / "in.wav", fs=32000)
    job = _whis_job(tmp_path, source)
    fake = FakeMatlab()

    assert _run(tmp_path, [job], fake, monkeypatch) == 1

    assert not fake.commands
    assert "32000 Hz" in _sidecar(job)["error"]["message"]
