import math
from pathlib import Path

from acoustic_orchestrator.config.validator import load_material_absorption_coefficients


SABINE_CONSTANT_M_S = 0.161
RAVEN_THIRD_OCTAVE_FREQUENCIES_HZ = (
    20,
    25,
    31.5,
    40,
    50,
    63,
    80,
    100,
    125,
    160,
    200,
    250,
    315,
    400,
    500,
    630,
    800,
    1000,
    1250,
    1600,
    2000,
    2500,
    3150,
    4000,
    5000,
    6300,
    8000,
    10000,
    12500,
    16000,
    20000,
)
RT30_GUARD_FREQUENCIES_HZ = tuple(
    frequency
    for frequency in RAVEN_THIRD_OCTAVE_FREQUENCIES_HZ
    if 500 <= frequency <= 2000
)


def estimate_room_rt30_s(room: dict) -> dict:
    dimensions = room["dimensions"]
    length = dimensions["length"]
    width = dimensions["width"]
    height = dimensions["height"]
    volume_m3 = length * width * height
    surface_areas_m2 = {
        "north_wall": length * height,
        "south_wall": length * height,
        "east_wall": width * height,
        "west_wall": width * height,
        "floor": length * width,
        "ceiling": length * width,
    }

    absorption_by_surface = {
        surface_id: load_material_absorption_coefficients(Path(material["material_path"]))
        for surface_id, material in room["material_files"].items()
    }
    selected_band_indexes = [
        index
        for index, frequency in enumerate(RAVEN_THIRD_OCTAVE_FREQUENCIES_HZ)
        if frequency in RT30_GUARD_FREQUENCIES_HZ
    ]

    rt30_by_band_s: dict[int | float, float] = {}
    for band_index in selected_band_indexes:
        frequency_hz = RAVEN_THIRD_OCTAVE_FREQUENCIES_HZ[band_index]
        equivalent_absorption_m2 = sum(
            surface_areas_m2[surface_id] * coefficients[band_index]
            for surface_id, coefficients in absorption_by_surface.items()
        )
        if not math.isfinite(equivalent_absorption_m2) or equivalent_absorption_m2 <= 0:
            raise ValueError(
                f"AbsorciÃ³n equivalente no positiva o no finita para la banda de {frequency_hz} Hz"
            )
        rt30_by_band_s[frequency_hz] = SABINE_CONSTANT_M_S * volume_m3 / equivalent_absorption_m2

    estimated_rt30_s = sum(rt30_by_band_s.values()) / len(rt30_by_band_s)
    if not math.isfinite(estimated_rt30_s):
        raise ValueError("La estimaciÃ³n RT30 de Sabine no es finita")

    return {
        "estimated_rt30_s": estimated_rt30_s,
        "rt30_by_band_s": rt30_by_band_s,
    }
