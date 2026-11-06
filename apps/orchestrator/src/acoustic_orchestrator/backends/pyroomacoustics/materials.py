"""31-band manifest coefficients → the ten octave centers (no equivalence claim).

Uses the center-frequency mapping of the Sabine estimator
(``RAVEN_OCTAVE_CENTER_THIRD_INDEXES``), so simulation and RT30 stratification
see the same octave coefficients. Invalid values are rejected, never clamped.
"""

import math
from collections.abc import Sequence

from acoustic_orchestrator.backends.pyroomacoustics.errors import InvalidRenderInput
from acoustic_orchestrator.experiment.room_acoustics import RAVEN_OCTAVE_CENTER_THIRD_INDEXES
from acoustic_orchestrator.pipeline.render_validation import RAVEN_OCTAVE_FREQUENCIES_HZ

OCTAVE_CENTER_FREQUENCIES_HZ: tuple[float, ...] = tuple(float(value) for value in RAVEN_OCTAVE_FREQUENCIES_HZ)
THIRD_OCTAVE_BAND_COUNT = 31


def octave_coefficients(values: Sequence[float], label: str) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != THIRD_OCTAVE_BAND_COUNT:
        raise InvalidRenderInput(f"{label} must have {THIRD_OCTAVE_BAND_COUNT} third-octave values")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise InvalidRenderInput(f"{label} contains a non-finite value")
        if not 0.0 <= value <= 1.0:
            raise InvalidRenderInput(f"{label} contains a value outside [0, 1]: {value}")
    return [float(values[index]) for index in RAVEN_OCTAVE_CENTER_THIRD_INDEXES]


def convert_surface_materials(room: dict, surface_ids: Sequence[str]) -> dict[str, dict]:
    """Octave absorption/scattering per surface from ``acoustic_surfaces``."""
    surfaces = room.get("acoustic_surfaces")
    if not isinstance(surfaces, dict):
        raise InvalidRenderInput("room.acoustic_surfaces is missing")
    converted: dict[str, dict] = {}
    for surface_id in surface_ids:
        surface = surfaces.get(surface_id)
        if not isinstance(surface, dict):
            raise InvalidRenderInput(f"room.acoustic_surfaces.{surface_id} is missing")
        converted[surface_id] = {
            "center_frequencies_hz": list(OCTAVE_CENTER_FREQUENCIES_HZ),
            "absorption": octave_coefficients(
                surface.get("effective_absorption"), f"room.acoustic_surfaces.{surface_id}.effective_absorption"),
            "scattering": octave_coefficients(
                surface.get("effective_scattering"), f"room.acoustic_surfaces.{surface_id}.effective_scattering"),
            "scattering_applied": False,
        }
    if set(surfaces) != set(surface_ids):
        raise InvalidRenderInput("room.acoustic_surfaces does not match the room surfaces")
    return converted
