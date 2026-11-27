import math
import random

import pytest

from acoustic_orchestrator.experiment.geometry import (
    ABS_AREA_TOL_M2,
    build_l_shape_footprint,
    build_shoebox_footprint,
    build_trapezoid_footprint,
    canonical_wall_ids,
    canonicalize_footprint,
    distance_point_to_segment,
    edge_lengths,
    inward_normals,
    inset_polygon,
    is_simple_polygon,
    point_in_polygon,
    polygon_area,
    sample_uniform_point,
    signed_polygon_area,
    triangulate_polygon,
    wall_clearance,
)
from acoustic_orchestrator.experiment.geometry_visualization import plot_room_geometry


def test_canonicalizes_ccw_at_lexicographically_smallest_origin() -> None:
    footprint = canonicalize_footprint([(4, 3), (4, 1), (1, 1), (1, 3)])

    assert footprint == [(0.0, 0.0), (3.0, 0.0), (3.0, 2.0), (0.0, 2.0)]
    assert signed_polygon_area(footprint) > 0.0
    assert canonical_wall_ids(footprint) == ["wall_001", "wall_002", "wall_003", "wall_004"]


@pytest.mark.parametrize("offset", [-2.0, 0.0, 2.0])
def test_trapezoid_offsets_are_simple_normalized_and_keep_area(offset: float) -> None:
    footprint = build_trapezoid_footprint(6.0, 4.0, 3.0, offset)

    assert min(x for x, _ in footprint) == 0.0
    assert min(z for _, z in footprint) == 0.0
    assert is_simple_polygon(footprint)
    assert polygon_area(footprint) == pytest.approx(15.0)


def test_equal_trapezoid_bases_are_allowed() -> None:
    footprint = build_trapezoid_footprint(4.0, 4.0, 3.0, 1.5)

    assert len(footprint) == 4
    assert polygon_area(footprint) == pytest.approx(12.0)


@pytest.mark.parametrize(
    "corner",
    ["north_east", "north_west", "south_east", "south_west"],
)
def test_l_shapes_have_six_walls_and_parametric_area(corner: str) -> None:
    footprint = build_l_shape_footprint(7.0, 6.0, 2.5, 2.0, corner)

    assert len(footprint) == 6
    assert len(canonical_wall_ids(footprint)) == 6
    assert is_simple_polygon(footprint)
    assert polygon_area(footprint) == pytest.approx(7.0 * 6.0 - 2.5 * 2.0)


def test_rejects_self_intersections_and_duplicate_edges() -> None:
    with pytest.raises(ValueError, match="simple"):
        canonicalize_footprint([(0, 0), (2, 2), (0, 2), (2, 0)])
    with pytest.raises(ValueError, match="duplicados"):
        canonicalize_footprint([(0, 0), (2, 0), (2, 0), (0, 2)])


def test_inset_preserves_clearance_for_convex_and_concave_rooms() -> None:
    for footprint in (
        build_shoebox_footprint(5.0, 4.0),
        build_trapezoid_footprint(6.0, 4.0, 4.0, 1.0),
        build_l_shape_footprint(7.0, 6.0, 2.0, 2.0, "north_east"),
    ):
        inset = inset_polygon(footprint, 0.5)
        assert polygon_area(inset) > ABS_AREA_TOL_M2
        assert all(wall_clearance(vertex, footprint) >= 0.5 - 1e-6 for vertex in inset)


def test_ear_clipping_conserves_l_shape_area() -> None:
    footprint = build_l_shape_footprint(7.0, 6.0, 2.0, 2.5, "south_west")
    triangles = triangulate_polygon(footprint)

    assert len(triangles) == len(footprint) - 2
    assert sum(polygon_area(triangle) for triangle in triangles) == pytest.approx(
        polygon_area(footprint), abs=ABS_AREA_TOL_M2
    )


def test_uniform_samples_are_inside_triangulated_polygon() -> None:
    footprint = inset_polygon(
        build_l_shape_footprint(7.0, 6.0, 2.0, 2.0, "north_west"),
        0.5,
    )
    rng = random.Random(1234)

    points = [sample_uniform_point(footprint, rng) for _ in range(500)]

    assert all(point_in_polygon(point, footprint) for point in points)
    assert len({(round(x, 3), round(z, 3)) for x, z in points}) > 450


def test_distances_lengths_and_inward_normals_use_real_segments() -> None:
    footprint = build_shoebox_footprint(4.0, 3.0)

    assert edge_lengths(footprint) == pytest.approx([4.0, 3.0, 4.0, 3.0])
    assert inward_normals(footprint) == pytest.approx([(0.0, 1.0), (-1.0, 0.0), (0.0, -1.0), (1.0, 0.0)])
    assert distance_point_to_segment((2.0, 2.0), (0.0, 0.0), (4.0, 0.0)) == pytest.approx(2.0)
    assert wall_clearance((2.0, 1.5), footprint) == pytest.approx(1.5)
    assert math.isfinite(wall_clearance((2.0, 1.5), footprint))


def test_point_in_polygon_distinguishes_boundary_policy() -> None:
    footprint = build_shoebox_footprint(4.0, 3.0)

    assert point_in_polygon((2.0, 1.0), footprint)
    assert point_in_polygon((0.0, 1.0), footprint)
    assert not point_in_polygon((0.0, 1.0), footprint, include_boundary=False)
    assert not point_in_polygon((5.0, 1.0), footprint)


def test_visualization_helper_renders_manifest_geometry(tmp_path) -> None:
    output_path = tmp_path / "room.png"
    manifest = {
        "room": {
            "geometry": {
                "type": "l_shape",
                "footprint_vertices_m": build_l_shape_footprint(
                    7.0, 6.0, 2.0, 2.0, "north_east"
                ),
            }
        },
        "receiver": {"position_m": [1.0, 1.5, 1.0]},
        "sources": [{"source_id": "src_1", "position_m": [2.0, 1.0, 1.0]}],
    }

    figure = plot_room_geometry(manifest, output_path=output_path)

    assert output_path.is_file()
    assert figure.axes[0].get_title() == "l_shape"
    assert {text.get_text() for text in figure.axes[0].texts} >= {
        f"wall_{index:03d}" for index in range(1, 7)
    }
