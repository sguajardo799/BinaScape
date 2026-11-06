"""[pra][Linux] Acoustic and physical validation of the Pyroomacoustics backend (tasks 7.1–7.6)."""

import math
from pathlib import Path

import pytest

from pra_support import make_runtime_manifest, require_pyroomacoustics, uniform_surfaces, write_tone_wav

pra = require_pyroomacoustics()

import numpy as np  # noqa: E402
import soundfile  # noqa: E402

from acoustic_orchestrator.backends.pyroomacoustics import simulation, synthesis, t30  # noqa: E402
from acoustic_orchestrator.backends.pyroomacoustics.frames import backend_to_public, public_to_backend  # noqa: E402
from acoustic_orchestrator.backends.pyroomacoustics.job import load_render_job  # noqa: E402
from acoustic_orchestrator.backends.pyroomacoustics.materials import convert_surface_materials  # noqa: E402
from acoustic_orchestrator.backends.pyroomacoustics.room_geometry import build_room_description  # noqa: E402
from acoustic_orchestrator.backends.pyroomacoustics.worker import main as worker_main  # noqa: E402
from acoustic_orchestrator.config.models import CircularArrayOutputConfig  # noqa: E402
from acoustic_orchestrator.experiment.geometry import build_shoebox_footprint, canonical_wall_ids  # noqa: E402
from acoustic_orchestrator.experiment.receiver_geometry import build_circular_array_descriptor  # noqa: E402
from acoustic_orchestrator.experiment.sampler import _fixed_position_relative_to_receiver  # noqa: E402
from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt  # noqa: E402

FS = 44100


def _shoebox(length: float = 6.0, width: float = 5.0, height: float = 3.0, absorption: float = 0.2) -> tuple[dict, dict]:
    footprint = [list(v) for v in build_shoebox_footprint(length, width)]
    wall_ids = canonical_wall_ids(footprint)
    room = {"geometry": {"type": "shoebox", "height_m": height, "footprint_vertices_m": footprint, "wall_ids": wall_ids},
            "acoustic_surfaces": uniform_surfaces(wall_ids, absorption)}
    description = build_room_description(room)
    materials = convert_surface_materials(room, [*description["wall_surface_ids"], "floor", "ceiling"])
    return description, materials


def _rirs(sources_public, mics_public, *, max_order: int = 0, absorption: float = 0.2, size=(6.0, 5.0, 3.0)):
    description, materials = _shoebox(*size, absorption=absorption)
    simulation.seed_package(1)
    room = simulation.build_room(description, materials, simulation.IsmParameters(max_order, max_order, False), FS)
    t = description["translation_m"]
    rirs = simulation.compute_rirs(room, [public_to_backend(p, t) for p in sources_public],
                                   [public_to_backend(p, t) for p in mics_public])
    return rirs, simulation.runtime_timing(room), room, description


# --- 7.1 direct-path arrival -------------------------------------------------------------

def test_direct_path_arrival_at_two_meters() -> None:
    rirs, timing, _, _ = _rirs([[1.0, 1.5, 2.5]], [[3.0, 1.5, 2.5]])
    peak = int(np.argmax(np.abs(rirs[0][0])))
    expected = round(2.0 / timing["speed_of_sound_m_s"] * FS) + timing["global_delay_samples"]
    assert abs(peak - expected) <= 1, (peak, expected)


# --- 7.2 distance attenuation ---------------------------------------------------------------

def test_inverse_square_energy_ratio() -> None:
    rirs, timing, _, _ = _rirs([[2.0, 1.5, 2.5]], [[3.0, 1.5, 2.5], [4.0, 1.5, 2.5]])
    window = timing["global_delay_samples"]
    energies = []
    for rir in rirs[0]:
        peak = int(np.argmax(np.abs(rir)))
        energies.append(float(np.sum(rir[max(0, peak - window): peak + window + 1] ** 2)))
    ratio = energies[0] / energies[1]
    assert abs(ratio - 4.0) / 4.0 <= 0.05, ratio


# --- 7.3 circular array positions and channel order -------------------------------------------

def _array_manifest(tmp_path: Path, mics: int, k: int, radius: float = 0.05, yaw: float = 30.0) -> tuple[Path, dict]:
    center = [2.0, 1.5, 1.75]
    descriptor = build_circular_array_descriptor(center, {"yaw": yaw, "pitch": 0.0, "roll": 0.0},
                                                 CircularArrayOutputConfig(enabled=True, num_microphones=mics, radius_m=radius))
    mic = descriptor["microphones"][k]["position_m"]
    direction = np.subtract(mic, center) / np.linalg.norm(np.subtract(mic, center))
    source = (np.array(center) + direction).tolist()
    return make_runtime_manifest(tmp_path, num_microphones=mics, radius_m=radius, max_order=0, receiver_position=center,
                                 receiver_yaw=yaw, sources=[{"position_m": source}], target_duration_s=None)


@pytest.mark.parametrize("mics", [3, 8])
def test_microphones_rendered_at_descriptor_positions_and_channel_order(tmp_path: Path, mics: int) -> None:
    for k in range(mics):
        path, manifest = _array_manifest(tmp_path / f"m{mics}_{k}", mics, k)
        job = load_render_job(manifest)
        room = simulation.build_room(job.room_description, job.materials, simulation.IsmParameters(0, 0, False), FS)
        rirs = simulation.compute_rirs(room, [s.position_backend_m for s in job.sources], job.microphones_backend_m)
        rendered = room.mic_array.R.T
        translation = job.room_description["translation_m"]
        for index, item in enumerate(job.microphones):
            back = backend_to_public(rendered[index], translation)
            assert max(abs(a - b) for a, b in zip(back, item["position_m"])) <= 1e-9
        arrivals = [int(np.argmax(np.abs(rir))) for rir in rirs[0]]
        assert arrivals[k] == min(arrivals), (k, arrivals)
        # Strict: microphone k (WAV channel k+1) has the earliest sub-sample peak.
        onsets = [_fractional_peak(rir) for rir in rirs[0]]
        assert int(np.argmin(onsets)) == k, (k, onsets)


def _fractional_peak(rir: np.ndarray) -> float:
    peak = int(np.argmax(np.abs(rir)))
    left, centre, right = np.abs(rir[peak - 1: peak + 2])
    denominator = left - 2 * centre + right
    return peak + (0.5 * (left - right) / denominator if denominator else 0.0)


def test_wav_channel_order_end_to_end(tmp_path: Path) -> None:
    path, manifest = _array_manifest(tmp_path, 3, 1, radius=0.2)
    write_tone_wav(Path(manifest["sources"][0]["audio_path"]), seconds=0.05, frequency_hz=900.0)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    assert worker_main([attempt.snapshot_path.as_posix(), "--attempt-id", attempt.attempt_id, "--variant-id",
                        attempt.variant_id, "--manifest-sha256", attempt.manifest_sha256, "--completion-path",
                        attempt.completion_path.as_posix(), "--failure-path", attempt.failure_path.as_posix()]) == 0
    audio, rate = soundfile.read(manifest["render"]["output_wav_path"], dtype="float64", always_2d=True)
    assert rate == FS and audio.shape[1] == 3
    onsets = [int(np.flatnonzero(np.abs(audio[:, c]) > 0.05 * np.max(np.abs(audio)))[0]) for c in range(3)]
    assert int(np.argmin(onsets)) == 1, onsets


# --- 7.4 inter-microphone delay ---------------------------------------------------------------

@pytest.mark.parametrize("source", [[1.0, 1.5, 1.0], [4.5, 1.2, 3.5], [3.0, 2.2, 0.8]])
def test_inter_microphone_delay_by_cross_correlation(source: list[float]) -> None:
    center = [3.0, 1.5, 2.5]
    descriptor = build_circular_array_descriptor(center, {"yaw": 15.0, "pitch": 0.0, "roll": 0.0},
                                                 CircularArrayOutputConfig(enabled=True, num_microphones=4, radius_m=0.15))
    mics = [item["position_m"] for item in descriptor["microphones"]]
    rirs, timing, _, _ = _rirs([source], mics)
    signal = np.random.default_rng(5).standard_normal(8820)
    channels = synthesis.render_source(signal, rirs[0], trim_reverb_tail=False)
    a, b = channels[:, 0], channels[:, 2]
    correlation = np.correlate(a, b, mode="full")
    lag = int(np.argmax(correlation)) - (len(b) - 1)
    c = timing["speed_of_sound_m_s"]
    expected = (math.dist(source, mics[0]) - math.dist(source, mics[2])) / c * FS
    assert abs(lag - expected) <= 1, (lag, expected)


# --- 7.5 reverberation behaviour and source offsets ---------------------------------------------

def test_more_absorption_shorter_decay() -> None:
    size = (4.0, 3.5, 2.7)
    sources, mics = [[1.0, 1.2, 1.0]], [[2.6, 1.5, 2.2], [2.65, 1.5, 2.2]]
    measured = {}
    for absorption in (0.1, 0.4):
        rirs, _, _, _ = _rirs(sources, mics, max_order=40, absorption=absorption, size=size)
        measured[absorption] = t30.measure_t30(rirs, FS)[0]["t30_s"]
    valid = [(low, high) for low, high in zip(measured[0.1], measured[0.4]) if low is not None and high is not None]
    assert len(valid) >= 8, measured
    assert all(high < low for low, high in valid), measured


def test_two_sources_at_rounded_offsets(tmp_path: Path) -> None:
    starts = [0.0, 0.0123]
    sources = [[1.0, 1.5, 1.0], [3.2, 1.5, 2.8]]
    rirs, timing, _, _ = _rirs(sources, [[2.0, 1.5, 1.75]], size=(4.0, 3.5, 2.7))
    impulse = np.zeros(10)
    impulse[0] = 1.0
    rendered = [synthesis.render_source(impulse, row, trim_reverb_tail=False) for row in rirs]
    offsets = [synthesis.source_offset_samples(start, FS) for start in starts]
    assert offsets == [0, 542]
    mix, _ = synthesis.mix_sources(rendered, offsets, 1, None, FS)
    for row, offset in zip(rirs, offsets, strict=True):
        expected = offset + int(np.argmax(np.abs(row[0])))
        window = np.abs(mix[expected - 2: expected + 3, 0])
        assert window.max() == pytest.approx(np.max(np.abs(row[0])), rel=1e-6)


# --- 7.6 legacy angular convention (characterized, not corrected) -----------------------------

def test_legacy_angular_convention_characterized() -> None:
    radius = 0.05
    center = [2.0, 1.5, 2.0]
    descriptor = build_circular_array_descriptor(center, {"yaw": 90.0, "pitch": 0.0, "roll": 0.0},
                                                 CircularArrayOutputConfig(enabled=True, num_microphones=4, radius_m=radius))
    offset = np.subtract(descriptor["microphones"][0]["position_m"], center)
    np.testing.assert_allclose(offset, [0.0, 0.0, radius], atol=1e-12)  # microphone 0 toward public +Z
    receiver = {"position_m": center, "orientation_deg": {"yaw": 90.0, "pitch": 0.0, "roll": 0.0}}
    source = _fixed_position_relative_to_receiver(receiver, (0.0, 0.0, 1.0))
    np.testing.assert_allclose(np.subtract(source, center), [0.0, 0.0, -1.0], atol=1e-12)  # source toward public -Z
    # KNOWN RISK (spec "Legacy circular-array angular convention preserved"): the microphone ring
    # turns opposite to the yaw/azimuth convention used for view vectors and fixed_position
    # sources. The backend renders the descriptor positions unchanged; correcting the convention
    # needs a separate, authorized specification change before any DOA labelling.
    backend_mic = public_to_backend(descriptor["microphones"][0]["position_m"])
    assert backend_mic[1] == pytest.approx(-center[2] - radius)
