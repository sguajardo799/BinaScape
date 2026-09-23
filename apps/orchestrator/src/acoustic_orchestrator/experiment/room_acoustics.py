import math
from pathlib import Path

from acoustic_orchestrator.config.validator import load_material_absorption_coefficients
from acoustic_orchestrator.experiment.geometry import edge_lengths, polygon_area


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
RAVEN_OCTAVE_CENTER_FREQUENCIES_HZ = (
    31.5,
    63,
    125,
    250,
    500,
    1000,
    2000,
    4000,
    8000,
    16000,
)
RAVEN_OCTAVE_CENTER_THIRD_INDEXES = (2, 5, 8, 11, 14, 17, 20, 23, 26, 29)
RT30_GUARD_FREQUENCIES_HZ = RAVEN_OCTAVE_CENTER_FREQUENCIES_HZ


def estimate_room_rt30_s(room: dict) -> dict:
    geometry = room["geometry"]
    footprint = [tuple(vertex) for vertex in geometry["footprint_vertices_m"]]
    height_m = geometry["height_m"]
    floor_area_m2 = polygon_area(footprint)
    volume_m3 = floor_area_m2 * height_m
    surface_areas_m2 = {
        wall_id: length_m * height_m
        for wall_id, length_m in zip(
            geometry["wall_ids"],
            edge_lengths(footprint),
            strict=True,
        )
    }
    surface_areas_m2["floor"] = floor_area_m2
    surface_areas_m2["ceiling"] = floor_area_m2

    expected_surfaces = set(surface_areas_m2)
    actual_surfaces = set(room["material_files"])
    if actual_surfaces != expected_surfaces:
        missing = sorted(expected_surfaces - actual_surfaces)
        extra = sorted(actual_surfaces - expected_surfaces)
        raise ValueError(f"Superficies acústicas inconsistentes; faltantes={missing}; extra={extra}")

    absorption_by_surface = {
        surface_id: load_material_absorption_coefficients(Path(material["material_path"]))
        for surface_id, material in room["material_files"].items()
    }
    rt30_by_band_s: dict[int | float, float] = {}
    for frequency_hz, band_index in zip(
        RAVEN_OCTAVE_CENTER_FREQUENCIES_HZ,
        RAVEN_OCTAVE_CENTER_THIRD_INDEXES,
        strict=True,
    ):
        equivalent_absorption_m2 = sum(
            surface_areas_m2[surface_id] * coefficients[band_index]
            for surface_id, coefficients in absorption_by_surface.items()
        )
        if not math.isfinite(equivalent_absorption_m2) or equivalent_absorption_m2 <= 0:
            raise ValueError(
                f"AbsorciÃ³n equivalente no positiva o no finita para la banda de {frequency_hz} Hz"
            )
        rt30_s = SABINE_CONSTANT_M_S * volume_m3 / equivalent_absorption_m2
        if not math.isfinite(rt30_s) or rt30_s <= 0:
            raise ValueError(
                f"RT30 de Sabine no positivo o no finito para la banda de {frequency_hz} Hz"
            )
        rt30_by_band_s[frequency_hz] = rt30_s

    if len(rt30_by_band_s) != len(RAVEN_OCTAVE_CENTER_FREQUENCIES_HZ):
        raise ValueError("La estimación RT30 de Sabine no contiene las 10 bandas de octava RAVEN")

    estimated_rt30_s = sum(rt30_by_band_s.values()) / len(rt30_by_band_s)
    if not math.isfinite(estimated_rt30_s):
        raise ValueError("La estimaciÃ³n RT30 de Sabine no es finita")

    return {
        "estimated_rt30_s": estimated_rt30_s,
        "rt30_by_band_s": rt30_by_band_s,
        "floor_area_m2": floor_area_m2,
        "volume_m3": volume_m3,
        "surface_areas_m2": surface_areas_m2,
    }
