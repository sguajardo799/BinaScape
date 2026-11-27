import math
from pathlib import Path

import pytest

from acoustic_orchestrator.experiment.room_acoustics import (
    RAVEN_OCTAVE_CENTER_FREQUENCIES_HZ,
    RAVEN_OCTAVE_CENTER_THIRD_INDEXES,
    RT30_GUARD_FREQUENCIES_HZ,
    RT30_MEAN_FREQUENCIES_HZ,
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
    room = _shoebox_room({surface: material_path for surface in _surface_ids()})

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
        sum(expected_by_band[frequency] for frequency in RT30_MEAN_FREQUENCIES_HZ)
        / len(RT30_MEAN_FREQUENCIES_HZ),
    )


def test_estimate_room_rt30_weights_each_surface_with_its_physical_area(tmp_path: Path) -> None:
    absorption_by_surface = {
        "wall_001": 0.10,
        "wall_002": 0.20,
        "wall_003": 0.30,
        "wall_004": 0.40,
        "floor": 0.50,
        "ceiling": 0.60,
    }
    material_files = {}
    for surface_id, absorption in absorption_by_surface.items():
        material_path = tmp_path / f"{surface_id}.mat"
        material_path.write_text(_material_file_text([absorption] * 31), encoding="utf-8")
        material_files[surface_id] = {"material_path": material_path}

    estimate = estimate_room_rt30_s(
        _shoebox_room({surface: ref["material_path"] for surface, ref in material_files.items()})
    )

    equivalent_absorption_m2 = (
        15.0 * absorption_by_surface["wall_001"]
        + 12.0 * absorption_by_surface["wall_002"]
        + 15.0 * absorption_by_surface["wall_003"]
        + 12.0 * absorption_by_surface["wall_004"]
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

    room = _shoebox_room({surface: material_path for surface in _surface_ids()})

    with pytest.raises(ValueError, match="16000 Hz"):
        estimate_room_rt30_s(room)


def test_estimate_room_rt30_uses_real_l_shape_area_volume_and_edges(tmp_path: Path) -> None:
    material_path = tmp_path / "material.mat"
    material_path.write_text(_material_file_text([0.5] * 31), encoding="utf-8")
    vertices = [[0.0, 0.0], [7.0, 0.0], [7.0, 4.0], [5.0, 4.0], [5.0, 6.0], [0.0, 6.0]]
    wall_ids = [f"wall_{index:03d}" for index in range(1, 7)]
    room = {
        "geometry": {
            "type": "l_shape",
            "height_m": 3.0,
            "footprint_vertices_m": vertices,
            "wall_ids": wall_ids,
            "generated_from": {},
        },
        "material_files": {
            surface: {"material_path": material_path}
            for surface in [*wall_ids, "floor", "ceiling"]
        },
    }

    estimate = estimate_room_rt30_s(room)

    assert estimate["floor_area_m2"] == pytest.approx(38.0)
    assert estimate["volume_m3"] == pytest.approx(114.0)
    assert list(estimate["surface_areas_m2"].values()) == pytest.approx(
        [21.0, 12.0, 6.0, 6.0, 15.0, 18.0, 38.0, 38.0]
    )


def test_estimator_accepts_geometry_generator_surface_contract_without_polygon(tmp_path: Path) -> None:
    material_path = tmp_path / "material.mat"
    material_path.write_text(_material_file_text([0.5] * 31), encoding="utf-8")
    surface_areas = {
        "curved_shell_a": ("wall", 18.0),
        "curved_shell_b": ("wall", 22.0),
        "lower_deck": ("floor", 20.0),
        "upper_deck": ("ceiling", 20.0),
    }
    room = {
        "acoustic_geometry": {
            "floor_area_m2": 20.0,
            "volume_m3": 60.0,
            "surfaces": {
                surface_id: {"surface_type": surface_type, "area_m2": area_m2}
                for surface_id, (surface_type, area_m2) in surface_areas.items()
            },
        },
        "material_files": {
            surface_id: {"material_path": material_path}
            for surface_id in surface_areas
        },
    }

    estimate = estimate_room_rt30_s(room)

    assert estimate["volume_m3"] == 60.0
    assert estimate["surface_areas_m2"] == {
        surface_id: area_m2 for surface_id, (_, area_m2) in surface_areas.items()
    }
    assert estimate["estimated_rt30_s"] == pytest.approx(
        SABINE_CONSTANT_M_S * 60.0 / (80.0 * 0.5)
    )


def _surface_ids() -> list[str]:
    return ["wall_001", "wall_002", "wall_003", "wall_004", "floor", "ceiling"]


def _shoebox_room(material_paths: dict[str, Path]) -> dict:
    return {
        "geometry": {
            "type": "shoebox",
            "height_m": 3.0,
            "footprint_vertices_m": [[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0]],
            "wall_ids": ["wall_001", "wall_002", "wall_003", "wall_004"],
            "generated_from": {"length_m": 5.0, "width_m": 4.0},
        },
        "material_files": {
            surface: {"material_path": path}
            for surface, path in material_paths.items()
        },
    }


def _material_file_text(absorption_values: list[float]) -> str:
    absorp = ", ".join(str(value) for value in absorption_values)
    scatter = ", ".join(["0.1"] * 31)
    return f"[Material]\nname=test\nabsorp={absorp}\nscatter={scatter}\n"
