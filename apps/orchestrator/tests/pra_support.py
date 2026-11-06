"""Shared helpers for tests that need the optional ``pyroomacoustics`` extra.

Locally a missing package skips the test; with
``BINASCAPE_REQUIRE_PYROOMACOUSTICS=1`` (strict mode) it is a failure instead.
"""

import json
import math
import os
import struct
import wave
from pathlib import Path

import pytest


def require_pyroomacoustics():
    if os.environ.get("BINASCAPE_REQUIRE_PYROOMACOUSTICS") == "1":
        import pyroomacoustics  # noqa: F401  - strict mode: fail loudly when the extra is missing

        return pyroomacoustics
    return pytest.importorskip("pyroomacoustics")


def require_backend_dependencies() -> None:
    """numpy/scipy/soundfile ship with the ``pyroomacoustics`` extra, not with RAVEN installs."""
    if os.environ.get("BINASCAPE_REQUIRE_PYROOMACOUSTICS") == "1":
        import scipy  # noqa: F401
        import soundfile  # noqa: F401
        return
    pytest.importorskip("scipy")
    pytest.importorskip("soundfile")


def uniform_surfaces(wall_ids: list[str], absorption: float, scattering: float = 0.1) -> dict:
    return {surface: {"effective_absorption": [absorption] * 31, "effective_scattering": [scattering] * 31}
            for surface in [*wall_ids, "floor", "ceiling"]}


def write_tone_wav(path: Path, *, seconds: float, sample_rate_hz: int = 44100, channels: int = 1,
                   frequency_hz: float = 440.0, amplitude: float = 0.5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(round(seconds * sample_rate_hz))
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate_hz)
        audio.writeframes(b"".join(
            struct.pack("<" + "h" * channels, *[
                int(32767 * amplitude * math.sin(2 * math.pi * frequency_hz * (c + 1) * n / sample_rate_hz))
                for c in range(channels)])
            for n in range(frames)))
    return path


def make_runtime_manifest(
    root: Path, *, num_microphones: int = 3, radius_m: float = 0.05, max_order: int = 8,
    absorption: float = 0.3, noise: dict | None = None, target_duration_s: float | None = 0.5,
    source_seconds: float = 0.2, seed: int = 42, room_size: tuple[float, float, float] = (4.0, 3.5, 2.7),
    sources: list[dict] | None = None, receiver_position: list[float] | None = None, receiver_yaw: float = 30.0,
    air_absorption: bool = False, scene_id: str = "scene_static_0001",
) -> tuple[Path, dict]:
    """Write a valid Pyroomacoustics array runtime manifest and its source WAVs."""
    from acoustic_orchestrator.config.models import CircularArrayOutputConfig
    from acoustic_orchestrator.experiment.geometry import build_shoebox_footprint, canonical_wall_ids
    from acoustic_orchestrator.experiment.receiver_geometry import build_circular_array_descriptor

    length, width, height = room_size
    footprint = [list(vertex) for vertex in build_shoebox_footprint(length, width)]
    wall_ids = canonical_wall_ids(footprint)
    center = receiver_position or [length / 2, 1.5, width / 2]
    descriptor = build_circular_array_descriptor(
        center, {"yaw": receiver_yaw, "pitch": 0.0, "roll": 0.0},
        CircularArrayOutputConfig(enabled=True, num_microphones=num_microphones, radius_m=radius_m))
    if sources is None:
        sources = [
            {"position_m": [1.0, 1.2, 1.0], "gain_db": -3.0, "start_time_s": 0.0},
            {"position_m": [3.0, 1.6, 2.6], "gain_db": 0.0, "start_time_s": 0.05},
        ]
    entries = []
    for number, spec in enumerate(sources, 1):
        audio = write_tone_wav(root / "audio" / f"src_{number:04d}.wav", seconds=spec.get("seconds", source_seconds),
                               frequency_hz=300.0 * number, channels=spec.get("channels", 1))
        entries.append({
            "source_id": f"src_{number:04d}", "event_type": "speech", "audio_path": audio.resolve().as_posix(),
            "identity_id": "", "audio_relpath": audio.name, "position_m": spec["position_m"],
            "orientation_deg": {"yaw": 10.0 * number, "pitch": 0.0, "roll": 0.0},
            "gain_db": spec.get("gain_db", 0.0), "start_time_s": spec.get("start_time_s", 0.0),
        })
    outputs = root / "outputs"
    render = {
        "sample_rate_hz": 44100, "seed": seed,
        "output_wav_path": (outputs / f"{scene_id}__circular_array.wav").resolve().as_posix(),
        "output_metadata_path": (outputs / f"{scene_id}__circular_array__render.json").resolve().as_posix(),
        "backend": "pyroomacoustics",
        "backend_parameters": {"max_order": max_order, "max_order_non_shoebox": max_order,
                               "air_absorption": air_absorption, "temperature_c": None, "humidity_pct": None},
        "active_output_type": "circular_array",
    }
    if target_duration_s is not None:
        render["target_duration_s"] = target_duration_s
    surfaces = {}
    for surface in [*wall_ids, "floor", "ceiling"]:
        surfaces[surface] = {"effective_absorption": [absorption] * 31, "effective_scattering": [0.1] * 31}
    manifest = {
        "schema_version": "3.0", "scene_type": "static", "base_rpf_file": None,
        "project_name": f"pra_test__{scene_id}__circular_array", "scene_id": scene_id, "job_id": "render_job_static_0001",
        "room": {"room_id": "room_test", "geometry": {"type": "shoebox", "height_m": height,
                                                      "footprint_vertices_m": footprint, "wall_ids": wall_ids},
                 "acoustic_surfaces": surfaces},
        "receiver": {"receiver_id": "rx_0001", "position_m": center,
                     "orientation_deg": {"yaw": receiver_yaw, "pitch": 0.0, "roll": 0.0},
                     "hrtfs": [], "circular_array": descriptor},
        "sources": entries,
        "background_noise": noise or {"enabled": False, "layers": []},
        "render": render,
    }
    path = root / "runtime" / f"{scene_id}__circular_array.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path, manifest


def variant_paths_for(manifest_path: Path, manifest: dict) -> dict:
    render = manifest["render"]
    return {"variant_id": manifest_path.stem, "scene_id": manifest["scene_id"], "output_type": "circular_array",
            "output_subdir": "circular_array", "runtime_manifest_path": manifest_path.resolve().as_posix(),
            "rendered_wav_path": render["output_wav_path"], "render_metadata_path": render["output_metadata_path"],
            "receiver_ir_path": None, "circular_array": manifest["receiver"]["circular_array"],
            "sample_rate_hz": render["sample_rate_hz"]}


def worker_command(attempt, events_path: Path | None = None) -> list[str]:
    import sys

    command = [sys.executable, "-m", "acoustic_orchestrator.backends.pyroomacoustics.worker",
               attempt.snapshot_path.as_posix(), "--attempt-id", attempt.attempt_id,
               "--variant-id", attempt.variant_id, "--manifest-sha256", attempt.manifest_sha256,
               "--completion-path", attempt.completion_path.as_posix(),
               "--failure-path", attempt.failure_path.as_posix()]
    if events_path is not None:
        command += ["--events-path", events_path.as_posix()]
    return command


# Untreated octave-band absorption (typical values; the licensed RAVEN material database is not
# part of the repository). Interpolated in log-frequency onto the 31 third-octave bands.
MATERIAL_SPECTRA = {
    "bricks": {125: 0.03, 250: 0.03, 500: 0.03, 1000: 0.04, 2000: 0.05, 4000: 0.07, 8000: 0.07},
    "wood": {125: 0.15, 250: 0.11, 500: 0.10, 1000: 0.07, 2000: 0.06, 4000: 0.07, 8000: 0.07},
    "plaster": {125: 0.013, 250: 0.015, 500: 0.02, 1000: 0.03, 2000: 0.04, 4000: 0.05, 8000: 0.05},
}


def third_octave_values(spectrum: dict[int, float]) -> list[float]:
    keys = sorted(spectrum)
    values = []
    for frequency in (20 * 2 ** (i / 3) for i in range(31)):
        if frequency <= keys[0] or frequency >= keys[-1]:
            values.append(spectrum[keys[0] if frequency <= keys[0] else keys[-1]])
            continue
        low = max(k for k in keys if k <= frequency)
        high = min(k for k in keys if k >= frequency)
        weight = 0 if high == low else math.log2(frequency / low) / math.log2(high / low)
        values.append(spectrum[low] + weight * (spectrum[high] - spectrum[low]))
    return values


def write_material_files(materials_root: Path) -> None:
    for material, spectrum in MATERIAL_SPECTRA.items():
        values = ", ".join(f"{value:.4f}" for value in third_octave_values(spectrum))
        target = materials_root / material / f"{material}.mat"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(["[Material]", f"name= {material}", "notes= test fixture", f"absorp= {values}",
                                     "scatter= " + ", ".join(["0.1"] * 31), "interpol= " + ", ".join(["1"] * 31)]),
                          encoding="utf-8")
