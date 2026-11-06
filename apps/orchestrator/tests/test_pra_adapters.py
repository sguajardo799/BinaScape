"""Pure-Python adapters of the Pyroomacoustics backend (no package import)."""

import math
import random

import pytest

from acoustic_orchestrator.backends.pyroomacoustics.errors import InvalidRenderInput
from acoustic_orchestrator.backends.pyroomacoustics.frames import (
    ROTATION,
    backend_to_public,
    determinant,
    footprint_translation,
    public_direction_to_backend,
    public_to_backend,
    transform_metadata,
)
from acoustic_orchestrator.backends.pyroomacoustics.materials import (
    OCTAVE_CENTER_FREQUENCIES_HZ,
    convert_surface_materials,
    octave_coefficients,
)
from acoustic_orchestrator.backends.pyroomacoustics.room_geometry import build_room_description, signed_area_xy
from acoustic_orchestrator.experiment.geometry import (
    build_l_shape_footprint,
    build_shoebox_footprint,
    build_trapezoid_footprint,
    canonical_wall_ids,
)

FOOTPRINTS = {
    "shoebox": build_shoebox_footprint(4.3, 3.7),
    "trapezoid": build_trapezoid_footprint(4.5, 3.2, 3.6, 0.4),
    "l_shape": build_l_shape_footprint(5.5, 4.5, 1.5, 1.2, "north_east"),
}


def _room(geometry_type: str, height: float = 2.7) -> dict:
    footprint = [list(vertex) for vertex in FOOTPRINTS[geometry_type]]
    return {"geometry": {"type": geometry_type, "height_m": height, "footprint_vertices_m": footprint,
                         "wall_ids": canonical_wall_ids(footprint)}}


# --- 3.1 frames -----------------------------------------------------------------

def test_rotation_is_proper() -> None:
    assert determinant(ROTATION) == 1.0
    for row in ROTATION:
        assert math.isclose(sum(value * value for value in row), 1.0)


def test_round_trip_property() -> None:
    rng = random.Random(1234)
    for _ in range(2000):
        point = tuple(rng.uniform(-1e3, 1e3) for _ in range(3))
        translation = (rng.uniform(-50, 50), rng.uniform(-50, 50), 0.0)
        back = backend_to_public(public_to_backend(point, translation), translation)
        assert max(abs(a - b) for a, b in zip(point, back)) <= 1e-9


def test_vertical_axis_and_floor() -> None:
    translation = footprint_translation(FOOTPRINTS["l_shape"])
    assert translation[2] == 0.0
    assert public_to_backend((2.0, 1.5, 1.0), translation)[2] == 1.5
    assert public_to_backend((2.0, 0.0, 1.0), translation)[2] == 0.0


def test_yaw_plus_90_points_to_backend_plus_y() -> None:
    yaw = math.radians(90.0)
    view = (math.cos(yaw), 0.0, -math.sin(yaw))  # public right-hand yaw about +Y from +X
    backend = public_direction_to_backend(view)
    assert backend == pytest.approx((0.0, 1.0, 0.0), abs=1e-12)
    zero = public_direction_to_backend((1.0, 0.0, 0.0))
    assert zero == pytest.approx((1.0, 0.0, 0.0))


def test_shoebox_anchored_at_backend_origin() -> None:
    length, width = 4.3, 3.7
    footprint = FOOTPRINTS["shoebox"]
    translation = footprint_translation(footprint)
    for x, z in footprint:
        X, Y, _ = public_to_backend((x, 0.0, z), translation)
        assert -1e-12 <= X <= length + 1e-12 and -1e-12 <= Y <= width + 1e-12
    rng = random.Random(7)
    for _ in range(200):
        x, y, z = rng.uniform(0, length), rng.uniform(0, 2.7), rng.uniform(0, width)
        X, Y, Z = public_to_backend((x, y, z), translation)
        assert 0 <= X <= length and 0 <= Y <= width and Z == y


def test_transform_metadata_records_matrix_and_translation() -> None:
    record = transform_metadata((0.0, 3.7, 0.0))
    assert record["rotation_matrix"] == [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]
    assert record["translation_m"] == [0.0, 3.7, 0.0]


# --- 3.2 room description -------------------------------------------------------

@pytest.mark.parametrize("geometry_type", list(FOOTPRINTS))
def test_area_preserved_and_ccw(geometry_type: str) -> None:
    room = _room(geometry_type)
    description = build_room_description(room)
    public_area = signed_area_xy(room["geometry"]["footprint_vertices_m"])
    backend_area = signed_area_xy(description["vertices_xy"])
    assert backend_area > 0
    assert abs(backend_area - public_area) <= 1e-9


@pytest.mark.parametrize("geometry_type", list(FOOTPRINTS))
def test_wall_identity_by_physical_edge(geometry_type: str) -> None:
    room = _room(geometry_type)
    description = build_room_description(room)
    footprint = room["geometry"]["footprint_vertices_m"]
    public_midpoints = {
        wall_id: ((footprint[i][0] + footprint[(i + 1) % len(footprint)][0]) / 2,
                  (footprint[i][1] + footprint[(i + 1) % len(footprint)][1]) / 2)
        for i, wall_id in enumerate(room["geometry"]["wall_ids"])
    }
    vertices = description["vertices_xy"]
    assert sorted(description["wall_surface_ids"]) == sorted(room["geometry"]["wall_ids"])
    for index, wall_id in enumerate(description["wall_surface_ids"]):
        (x0, y0), (x1, y1) = vertices[index], vertices[(index + 1) % len(vertices)]
        x, _, z = backend_to_public(((x0 + x1) / 2, (y0 + y1) / 2, 0.0), description["translation_m"])
        assert abs(x - public_midpoints[wall_id][0]) <= 1e-9
        assert abs(z - public_midpoints[wall_id][1]) <= 1e-9
    assert (description["floor_surface_id"], description["ceiling_surface_id"]) == ("floor", "ceiling")


def test_l_shape_has_six_walls_and_trapezoid_four() -> None:
    assert len(build_room_description(_room("l_shape"))["wall_surface_ids"]) == 6
    assert len(build_room_description(_room("trapezoid"))["wall_surface_ids"]) == 4


def test_shoebox_named_walls_match_outward_position() -> None:
    room = _room("shoebox")
    description = build_room_description(room)
    shoebox = description["shoebox"]
    assert shoebox["dimensions_m"] == pytest.approx([4.3, 3.7, 2.7])
    footprint = room["geometry"]["footprint_vertices_m"]
    walls = shoebox["wall_surface_ids"]
    for name, wall_id in walls.items():
        if name in ("floor", "ceiling"):
            assert wall_id == name
            continue
        i = room["geometry"]["wall_ids"].index(wall_id)
        (x0, z0), (x1, z1) = footprint[i], footprint[(i + 1) % 4]
        X, Y, _ = public_to_backend(((x0 + x1) / 2, 0.0, (z0 + z1) / 2), description["translation_m"])
        expected = {"west": X == pytest.approx(0.0), "east": X == pytest.approx(4.3),
                    "south": Y == pytest.approx(0.0), "north": Y == pytest.approx(3.7)}
        assert expected[name], (name, X, Y)
    assert set(walls) == {"west", "east", "south", "north", "floor", "ceiling"}


@pytest.mark.parametrize("mutation, message", [
    ({"type": "dome"}, "unsupported room.geometry.type"),
    ({"height_m": 0}, "height_m"),
    ({"wall_ids": ["a", "a", "b", "c"]}, "wall_ids"),
    ({"footprint_vertices_m": [[0, 0], [0, 3.7], [4.3, 3.7], [4.3, 0]]}, "counter-clockwise"),
])
def test_invalid_geometry_rejected(mutation: dict, message: str) -> None:
    room = _room("shoebox")
    room["geometry"].update(mutation)
    with pytest.raises(InvalidRenderInput, match=message):
        build_room_description(room)


def test_non_rectangular_shoebox_rejected() -> None:
    room = _room("shoebox")
    room["geometry"]["footprint_vertices_m"][2] = [4.0, 3.7]
    with pytest.raises(InvalidRenderInput, match="axis-aligned"):
        build_room_description(room)


# --- 3.3 materials ----------------------------------------------------------------

def test_center_frequency_mapping() -> None:
    values = [0.3] * 31
    values[17] = 0.12
    octave = octave_coefficients(values, "absorption")
    assert len(octave) == 10 == len(OCTAVE_CENTER_FREQUENCIES_HZ)
    assert octave[OCTAVE_CENTER_FREQUENCIES_HZ.index(1000.0)] == 0.12
    ramp = [index / 100 for index in range(31)]
    assert octave_coefficients(ramp, "x") == [0.02, 0.05, 0.08, 0.11, 0.14, 0.17, 0.2, 0.23, 0.26, 0.29]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.01, 1.01, True, "0.2"])
def test_invalid_coefficients_rejected_not_clamped(bad) -> None:
    values = [0.2] * 31
    values[17] = bad
    with pytest.raises(InvalidRenderInput):
        octave_coefficients(values, "absorption")


def test_wrong_band_count_rejected() -> None:
    with pytest.raises(InvalidRenderInput, match="31"):
        octave_coefficients([0.2] * 10, "absorption")


def test_surface_conversion_reports_scattering_not_applied() -> None:
    room = {"acoustic_surfaces": {
        surface: {"effective_absorption": [0.1 * (n + 1)] * 31, "effective_scattering": [0.05] * 31}
        for n, surface in enumerate(["wall_001", "floor", "ceiling"])}}
    converted = convert_surface_materials(room, ["wall_001", "floor", "ceiling"])
    assert converted["floor"]["absorption"] == [0.2] * 10
    assert converted["ceiling"]["scattering"] == [0.05] * 10
    assert all(item["scattering_applied"] is False for item in converted.values())
    with pytest.raises(InvalidRenderInput, match="does not match"):
        convert_surface_materials(room, ["wall_001", "floor"])
