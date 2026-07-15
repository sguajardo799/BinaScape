import math
from pathlib import Path

import pytest

from acoustic_orchestrator.experiment.room_acoustics import (
    RAVEN_OCTAVE_CENTER_FREQUENCIES_HZ,
    RAVEN_OCTAVE_CENTER_THIRD_INDEXES,
    RT30_GUARD_FREQUENCIES_HZ,
    SABINE_CONSTANT_M_S,
    estimate_room_rt30_s,
)


def test_raven_octave_centers_use_the_center_third_octave_coefficients() -> None:
    assert RAVEN_OCTAVE_CENTER_FREQUENCIES_HZ == (
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
    assert RAVEN_OCTAVE_CENTER_THIRD_INDEXES == (2, 5, 8, 11, 14, 17, 20, 23, 26, 29)
    assert RT30_GUARD_FREQUENCIES_HZ == RAVEN_OCTAVE_CENTER_FREQUENCIES_HZ


def test_estimate_room_rt30_uses_all_ten_raven_octave_bands(tmp_path: Path) -> None:
    selected_absorptions = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    absorption_values = [0.99] * 31
    for index, absorption in zip(
        RAVEN_OCTAVE_CENTER_THIRD_INDEXES,
        selected_absorptions,
        strict=True,
    ):
        absorption_values[index] = absorption
    material_path = tmp_path / "material.mat"
    material_path.write_text(_material_file_text(absorption_values), encoding="utf-8")
    room = {
        "dimensions": {"length": 5.0, "width": 4.0, "height": 3.0},
        "material_files": {
            surface_id: {"material_path": material_path}
            for surface_id in (
                "north_wall",
                "south_wall",
                "east_wall",
                "west_wall",
                "floor",
                "ceiling",
            )
        },
    }

    estimate = estimate_room_rt30_s(room)

    total_surface_area_m2 = 2 * (5.0 * 3.0) + 2 * (4.0 * 3.0) + 2 * (5.0 * 4.0)
    expected_by_band = {
        frequency: SABINE_CONSTANT_M_S * 60.0 / (total_surface_area_m2 * absorption)
        for frequency, absorption in zip(RT30_GUARD_FREQUENCIES_HZ, selected_absorptions, strict=True)
    }
    assert tuple(estimate["rt30_by_band_s"]) == RT30_GUARD_FREQUENCIES_HZ
    for frequency, expected_rt30_s in expected_by_band.items():
        assert math.isclose(estimate["rt30_by_band_s"][frequency], expected_rt30_s)
    assert math.isclose(
        estimate["estimated_rt30_s"],
        sum(expected_by_band.values()) / len(expected_by_band),
    )


def test_estimate_room_rt30_weights_each_surface_with_its_physical_area(tmp_path: Path) -> None:
    absorption_by_surface = {
        "north_wall": 0.10,
        "south_wall": 0.20,
        "east_wall": 0.30,
        "west_wall": 0.40,
        "floor": 0.50,
        "ceiling": 0.60,
    }
    material_files = {}
    for surface_id, absorption in absorption_by_surface.items():
        material_path = tmp_path / f"{surface_id}.mat"
        material_path.write_text(_material_file_text([absorption] * 31), encoding="utf-8")
        material_files[surface_id] = {"material_path": material_path}

    estimate = estimate_room_rt30_s(
        {
            "dimensions": {"length": 5.0, "width": 4.0, "height": 3.0},
            "material_files": material_files,
        }
    )

    equivalent_absorption_m2 = (
        15.0 * absorption_by_surface["north_wall"]
        + 15.0 * absorption_by_surface["south_wall"]
        + 12.0 * absorption_by_surface["east_wall"]
        + 12.0 * absorption_by_surface["west_wall"]
        + 20.0 * absorption_by_surface["floor"]
        + 20.0 * absorption_by_surface["ceiling"]
    )
    expected_rt30_s = SABINE_CONSTANT_M_S * 60.0 / equivalent_absorption_m2

    assert all(
        math.isclose(rt30_s, expected_rt30_s)
        for rt30_s in estimate["rt30_by_band_s"].values()
    )
    assert math.isclose(estimate["estimated_rt30_s"], expected_rt30_s)


def test_estimate_room_rt30_rejects_a_non_positive_selected_band(tmp_path: Path) -> None:
    absorption_values = [0.2] * 31
    absorption_values[RAVEN_OCTAVE_CENTER_THIRD_INDEXES[-1]] = 0.0
    material_path = tmp_path / "material.mat"
    material_path.write_text(_material_file_text(absorption_values), encoding="utf-8")

    room = {
        "dimensions": {"length": 5.0, "width": 4.0, "height": 3.0},
        "material_files": {
            surface_id: {"material_path": material_path}
            for surface_id in (
                "north_wall",
                "south_wall",
                "east_wall",
                "west_wall",
                "floor",
                "ceiling",
            )
        },
    }

    with pytest.raises(ValueError, match="16000 Hz"):
        estimate_room_rt30_s(room)


def _material_file_text(absorption_values: list[float]) -> str:
    absorp = ", ".join(str(value) for value in absorption_values)
    scatter = ", ".join(["0.1"] * 31)
    return f"[Material]\nname=test\nabsorp={absorp}\nscatter={scatter}\n"
