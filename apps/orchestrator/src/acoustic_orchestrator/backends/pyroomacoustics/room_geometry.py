"""Pure-data room description in the backend frame (no Pyroomacoustics import).

Walls are bound to acoustic surfaces by the two physical vertices they connect,
never by list position, because the frame change mirrors the footprint and its
winding must be reversed.
"""

import math
from collections.abc import Sequence

from acoustic_orchestrator.backends.pyroomacoustics.errors import InvalidRenderInput
from acoustic_orchestrator.backends.pyroomacoustics.frames import footprint_translation, public_to_backend

SUPPORTED_GEOMETRY_TYPES = ("shoebox", "trapezoid", "l_shape")
_SHOEBOX_TOLERANCE_M = 1e-9


def signed_area_xy(vertices: Sequence[Sequence[float]]) -> float:
    """Shoelace area; positive for counter-clockwise vertices."""
    total = 0.0
    for index, (x0, y0) in enumerate(vertices):
        x1, y1 = vertices[(index + 1) % len(vertices)]
        total += x0 * y1 - x1 * y0
    return total / 2.0


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def build_room_description(room: dict) -> dict:
    """Map ``room.geometry`` to backend polygon vertices and surface identities."""
    geometry = room.get("geometry") if isinstance(room, dict) else None
    if not isinstance(geometry, dict):
        raise InvalidRenderInput("room.geometry is missing")
    geometry_type = geometry.get("type")
    if geometry_type not in SUPPORTED_GEOMETRY_TYPES:
        raise InvalidRenderInput(
            f"unsupported room.geometry.type {geometry_type!r}; supported: {', '.join(SUPPORTED_GEOMETRY_TYPES)}")
    height = geometry.get("height_m")
    footprint = geometry.get("footprint_vertices_m")
    wall_ids = geometry.get("wall_ids")
    if not _finite_number(height) or height <= 0:
        raise InvalidRenderInput("room.geometry.height_m must be finite and positive")
    if (not isinstance(footprint, list) or len(footprint) < 3
            or any(not isinstance(v, list) or len(v) != 2 or not all(_finite_number(c) for c in v) for v in footprint)):
        raise InvalidRenderInput("room.geometry.footprint_vertices_m must be a list of [x, z] pairs")
    if (not isinstance(wall_ids, list) or len(wall_ids) != len(footprint)
            or len(set(wall_ids)) != len(wall_ids) or not all(isinstance(w, str) and w for w in wall_ids)):
        raise InvalidRenderInput("room.geometry.wall_ids must name each footprint edge once")
    if signed_area_xy(footprint) <= 0:
        raise InvalidRenderInput("room.geometry.footprint_vertices_m must be counter-clockwise")

    translation = footprint_translation(footprint)
    # Public [x, z] → backend [X, Y]; height does not affect the footprint.
    projected = [public_to_backend((x, 0.0, z), translation)[:2] for x, z in footprint]
    count = len(projected)
    # Y = -z mirrors the footprint: reverse to restore counter-clockwise order.
    order = [0, *range(count - 1, 0, -1)]
    vertices = [projected[index] for index in order]
    if signed_area_xy(vertices) <= 0:
        raise InvalidRenderInput("backend footprint winding could not be normalized")
    wall_surface_ids = []
    for position in range(count):
        start, end = order[position], order[(position + 1) % count]
        if end == (start + 1) % count:
            public_edge = start
        elif start == (end + 1) % count:
            public_edge = end
        else:  # pragma: no cover - impossible for a reversed cycle
            raise InvalidRenderInput("backend edge does not match a public wall")
        wall_surface_ids.append(wall_ids[public_edge])

    description = {
        "geometry_type": geometry_type,
        "height_m": float(height),
        "translation_m": list(translation),
        "vertices_xy": [list(vertex) for vertex in vertices],
        "wall_surface_ids": wall_surface_ids,
        "floor_surface_id": "floor",
        "ceiling_surface_id": "ceiling",
    }
    if geometry_type == "shoebox":
        description["shoebox"] = _shoebox(vertices, wall_surface_ids, float(height))
    return description


def _shoebox(vertices: list, wall_surface_ids: list[str], height: float) -> dict:
    """Axis-aligned box anchored at the origin, with walls named as the backend names them."""
    if len(vertices) != 4:
        raise InvalidRenderInput("shoebox footprint must have four vertices")
    length = max(x for x, _ in vertices)
    width = max(y for _, y in vertices)
    corners = {(0.0, 0.0), (length, 0.0), (length, width), (0.0, width)}
    for x, y in vertices:
        if not any(abs(x - cx) <= _SHOEBOX_TOLERANCE_M and abs(y - cy) <= _SHOEBOX_TOLERANCE_M for cx, cy in corners):
            raise InvalidRenderInput("shoebox footprint must be an axis-aligned rectangle")
    walls: dict[str, str] = {}
    for index, surface_id in enumerate(wall_surface_ids):
        (x0, y0), (x1, y1) = vertices[index], vertices[(index + 1) % 4]
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if abs(x0 - x1) <= _SHOEBOX_TOLERANCE_M:
            name = "west" if abs(mx) <= _SHOEBOX_TOLERANCE_M else "east"
        else:
            name = "south" if abs(my) <= _SHOEBOX_TOLERANCE_M else "north"
        if name in walls:
            raise InvalidRenderInput("shoebox walls are not distinct")
        walls[name] = surface_id
    walls["floor"] = "floor"
    walls["ceiling"] = "ceiling"
    return {"dimensions_m": [length, width, height], "wall_surface_ids": walls}


def contains_backend_point(description: dict, point: Sequence[float], *, margin_m: float = 1e-9) -> bool:
    """Strictly inside the extruded footprint (ray casting in XY, 0 < Z < height)."""
    x, y, z = (float(value) for value in point)
    if not margin_m < z < description["height_m"] - margin_m:
        return False
    vertices = description["vertices_xy"]
    inside = False
    for index, (x0, y0) in enumerate(vertices):
        x1, y1 = vertices[(index + 1) % len(vertices)]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside
