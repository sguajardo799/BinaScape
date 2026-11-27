"""Image-source RIRs with Pyroomacoustics (the only module importing the package).

Builds the room from the pure-data description (``room_geometry``) and the
converted materials (``materials``), places omnidirectional microphones at the
descriptor positions in ``microphone_index`` order and computes one RIR per
(source, microphone) pair. Ray tracing and randomized image sources stay off.
Timing facts (global delay, high-pass filter, speed of sound) are read from the
library at runtime, never hardcoded.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pyroomacoustics as pra

from acoustic_orchestrator.backends.pyroomacoustics.materials import OCTAVE_CENTER_FREQUENCIES_HZ

SEED_MASK = 2**64 - 1


@dataclass(frozen=True)
class IsmParameters:
    max_order: int
    max_order_non_shoebox: int
    air_absorption: bool
    temperature_c: float | None = None
    humidity_pct: float | None = None

    @classmethod
    def from_manifest(cls, parameters: dict) -> "IsmParameters":
        return cls(
            max_order=parameters["max_order"],
            max_order_non_shoebox=parameters["max_order_non_shoebox"],
            air_absorption=parameters["air_absorption"],
            temperature_c=parameters.get("temperature_c"),
            humidity_pct=parameters.get("humidity_pct"),
        )


def package_seed(seed: int) -> int:
    """Map any integer seed to the unsigned 64-bit range the package accepts."""
    return int(seed) & SEED_MASK


def seed_package(seed: int) -> int:
    applied = package_seed(seed)
    pra.random.seed(numpy=applied, libroom=applied)
    return applied


def _material(converted: dict) -> pra.Material:
    # A fresh Material per surface: the package resamples materials in place.
    centers = list(OCTAVE_CENTER_FREQUENCIES_HZ)
    return pra.Material({"coeffs": list(converted["absorption"]), "center_freqs": centers},
                        {"coeffs": list(converted["scattering"]), "center_freqs": centers})


def build_room(description: dict, materials: dict[str, dict], parameters: IsmParameters, fs: int) -> pra.Room:
    kwargs: dict = {"fs": fs, "air_absorption": parameters.air_absorption, "ray_tracing": False, "use_rand_ism": False}
    if parameters.temperature_c is not None:
        kwargs.update(temperature=parameters.temperature_c, humidity=parameters.humidity_pct)
    if description["geometry_type"] == "shoebox":
        shoebox = description["shoebox"]
        return pra.ShoeBox(
            shoebox["dimensions_m"],
            materials={name: _material(materials[surface]) for name, surface in shoebox["wall_surface_ids"].items()},
            max_order=parameters.max_order, **kwargs,
        )
    room = pra.Room.from_corners(
        np.array(description["vertices_xy"], dtype=float).T,
        materials=[_material(materials[surface]) for surface in description["wall_surface_ids"]],
        max_order=parameters.max_order_non_shoebox, **kwargs,
    )
    room.extrude(description["height_m"], materials={
        "floor": _material(materials[description["floor_surface_id"]]),
        "ceiling": _material(materials[description["ceiling_surface_id"]]),
    })
    return room


def effective_max_order(description: dict, parameters: IsmParameters) -> int:
    return parameters.max_order if description["geometry_type"] == "shoebox" else parameters.max_order_non_shoebox


def runtime_timing(room: pra.Room) -> dict:
    """Library facts that shape every RIR, read at runtime."""
    hpf_kwargs = dict(pra.constants.get("rir_hpf_kwargs"))
    return {
        "frac_delay_length": int(pra.constants.get("frac_delay_length")),
        "global_delay_samples": int(pra.constants.get("frac_delay_length")) // 2,
        "high_pass_filter": {
            "enabled": bool(pra.constants.get("rir_hpf_enable")),
            "cutoff_hz": float(pra.constants.get("rir_hpf_fc")),
            "design": {key: value if isinstance(value, (str, bool)) else float(value) for key, value in hpf_kwargs.items()},
        },
        "speed_of_sound_m_s": float(room.c),
    }


def compute_rirs(
    room: pra.Room, sources_m: Sequence[Sequence[float]], microphones_m: Sequence[Sequence[float]],
) -> list[list[np.ndarray]]:
    """Backend-frame positions in; ``rirs[source][microphone]`` out (float64)."""
    for position in sources_m:
        room.add_source(np.asarray(position, dtype=float))
    room.add_microphone_array(np.asarray(microphones_m, dtype=float).T)
    room.compute_rir()
    return [[np.asarray(room.rir[mic][src], dtype=np.float64) for mic in range(len(microphones_m))]
            for src in range(len(sources_m))]


def room_summary(room: pra.Room) -> dict:
    """Backend-side room facts for metadata (wall count, per-wall band-0 absorption)."""
    return {"n_walls": len(room.walls), "wall_names": [wall.name for wall in room.walls],
            "octave_band_centers_hz": [float(c) for c in room.octave_bands.centers]}
