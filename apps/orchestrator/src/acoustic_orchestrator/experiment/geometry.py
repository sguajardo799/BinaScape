import math
import random
from collections.abc import Iterable, Sequence


ABS_COORD_TOL_M = 1e-6
REL_GEOM_TOL = 1e-9
ABS_AREA_TOL_M2 = 1e-8
VECTOR_NORM_TOL = 1e-12
COORD_DECIMALS = 6

Point2D = tuple[float, float]
Triangle2D = tuple[Point2D, Point2D, Point2D]


def build_shoebox_footprint(length_m: float, width_m: float) -> list[Point2D]:
    return canonicalize_footprint(
        [(0.0, 0.0), (length_m, 0.0), (length_m, width_m), (0.0, width_m)]
    )


def build_trapezoid_footprint(
    base_a_m: float,
    base_b_m: float,
    depth_m: float,
    top_offset_m: float,
) -> list[Point2D]:
    return canonicalize_footprint(
        [
            (0.0, 0.0),
            (base_a_m, 0.0),
            (top_offset_m + base_b_m, depth_m),
            (top_offset_m, depth_m),
        ]
    )


def build_l_shape_footprint(
    outer_length_m: float,
    outer_width_m: float,
    cutout_length_m: float,
    cutout_width_m: float,
    removed_corner: str,
) -> list[Point2D]:
    length = outer_length_m
    width = outer_width_m
    cut_length = cutout_length_m
    cut_width = cutout_width_m
    footprints = {
        "north_east": [
            (0.0, 0.0),
            (length, 0.0),
            (length, width - cut_width),
            (length - cut_length, width - cut_width),
            (length - cut_length, width),
            (0.0, width),
        ],
        "north_west": [
            (0.0, 0.0),
            (length, 0.0),
            (length, width),
            (cut_length, width),
            (cut_length, width - cut_width),
            (0.0, width - cut_width),
        ],
        "south_east": [
            (0.0, 0.0),
            (length - cut_length, 0.0),
            (length - cut_length, cut_width),
            (length, cut_width),
            (length, width),
            (0.0, width),
        ],
        "south_west": [
            (cut_length, 0.0),
            (length, 0.0),
            (length, width),
            (0.0, width),
            (0.0, cut_width),
            (cut_length, cut_width),
        ],
    }
    try:
        vertices = footprints[removed_corner]
    except KeyError as exc:
        raise ValueError(f"Esquina L desconocida: {removed_corner}") from exc
    return canonicalize_footprint(vertices)


def canonicalize_footprint(vertices: Iterable[Sequence[float]]) -> list[Point2D]:
    points = _coerce_points(vertices)
    if not points:
        raise ValueError("La huella debe contener vértices")
    min_x = min(point[0] for point in points)
    min_z = min(point[1] for point in points)
    translated = [
        (_quantize(point[0] - min_x), _quantize(point[1] - min_z))
        for point in points
    ]
    return _validate_and_order(translated)


def validate_footprint(vertices: Iterable[Sequence[float]]) -> None:
    _validate_and_order(_coerce_points(vertices))


def signed_polygon_area(vertices: Sequence[Point2D]) -> float:
    return 0.5 * sum(
        start[0] * end[1] - end[0] * start[1]
        for start, end in polygon_edges(vertices)
    )


def polygon_area(vertices: Sequence[Point2D]) -> float:
    return abs(signed_polygon_area(vertices))


def polygon_edges(vertices: Sequence[Point2D]) -> list[tuple[Point2D, Point2D]]:
    return [
        (vertices[index], vertices[(index + 1) % len(vertices)])
        for index in range(len(vertices))
    ]


def edge_lengths(vertices: Sequence[Point2D]) -> list[float]:
    return [_distance(start, end) for start, end in polygon_edges(vertices)]


def canonical_wall_ids(vertices: Sequence[Point2D]) -> list[str]:
    return [f"wall_{index:03d}" for index in range(1, len(vertices) + 1)]


def inward_normals(vertices: Sequence[Point2D]) -> list[Point2D]:
    if signed_polygon_area(vertices) <= 0.0:
        raise ValueError("Las normales interiores requieren winding CCW")
    normals: list[Point2D] = []
    for start, end in polygon_edges(vertices):
        dx = end[0] - start[0]
        dz = end[1] - start[1]
        length = math.hypot(dx, dz)
        if length <= VECTOR_NORM_TOL:
            raise ValueError("No se puede calcular la normal de una arista degenerada")
        normals.append((-dz / length, dx / length))
    return normals


def is_simple_polygon(vertices: Sequence[Point2D]) -> bool:
    edges = polygon_edges(vertices)
    edge_count = len(edges)
    for first_index, first in enumerate(edges):
        for second_index in range(first_index + 1, edge_count):
            if second_index in {
                first_index,
                (first_index + 1) % edge_count,
                (first_index - 1) % edge_count,
            }:
                continue
            if _segments_intersect(first[0], first[1], edges[second_index][0], edges[second_index][1]):
                return False
    return True


def point_in_polygon(
    point: Sequence[float],
    vertices: Sequence[Point2D],
    *,
    include_boundary: bool = True,
) -> bool:
    candidate = (float(point[0]), float(point[1]))
    if any(point_on_segment(candidate, start, end) for start, end in polygon_edges(vertices)):
        return include_boundary

    inside = False
    x, z = candidate
    for start, end in polygon_edges(vertices):
        if (start[1] > z) == (end[1] > z):
            continue
        crossing_x = start[0] + (z - start[1]) * (end[0] - start[0]) / (end[1] - start[1])
        if x < crossing_x:
            inside = not inside
    return inside


def point_on_segment(point: Point2D, start: Point2D, end: Point2D) -> bool:
    if distance_point_to_segment(point, start, end) > ABS_COORD_TOL_M:
        return False
    return (
        min(start[0], end[0]) - ABS_COORD_TOL_M
        <= point[0]
        <= max(start[0], end[0]) + ABS_COORD_TOL_M
        and min(start[1], end[1]) - ABS_COORD_TOL_M
        <= point[1]
        <= max(start[1], end[1]) + ABS_COORD_TOL_M
    )


def distance_point_to_segment(point: Point2D, start: Point2D, end: Point2D) -> float:
    dx = end[0] - start[0]
    dz = end[1] - start[1]
    squared_length = dx * dx + dz * dz
    if squared_length <= VECTOR_NORM_TOL:
        return _distance(point, start)
    projection = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dz) / squared_length
    projection = min(1.0, max(0.0, projection))
    nearest = (start[0] + projection * dx, start[1] + projection * dz)
    return _distance(point, nearest)


def wall_clearance(point: Point2D, vertices: Sequence[Point2D]) -> float:
    return min(
        distance_point_to_segment(point, start, end)
        for start, end in polygon_edges(vertices)
    )


def inset_polygon(vertices: Sequence[Point2D], clearance_m: float) -> list[Point2D]:
    if not math.isfinite(clearance_m) or clearance_m < 0.0:
        raise ValueError("El margen debe ser finito y >= 0")
    ordered = _validate_and_order(_coerce_points(vertices))
    if clearance_m == 0.0:
        return ordered

    shifted_lines: list[tuple[Point2D, Point2D]] = []
    for (start, end), normal in zip(polygon_edges(ordered), inward_normals(ordered), strict=True):
        offset = (normal[0] * clearance_m, normal[1] * clearance_m)
        shifted_lines.append(
            (
                (start[0] + offset[0], start[1] + offset[1]),
                (end[0] + offset[0], end[1] + offset[1]),
            )
        )

    inset: list[Point2D] = []
    for index, current_line in enumerate(shifted_lines):
        previous_line = shifted_lines[index - 1]
        intersection = _line_intersection(previous_line, current_line)
        if intersection is None:
            raise ValueError("El margen colapsa una huella con aristas paralelas consecutivas")
        inset.append((_quantize(intersection[0]), _quantize(intersection[1])))

    inset = _validate_and_order(inset)
    if any(wall_clearance(point, ordered) + ABS_COORD_TOL_M < clearance_m for point in inset):
        raise ValueError("El margen solicitado no deja una región útil")
    return inset


def triangulate_polygon(vertices: Sequence[Point2D]) -> list[Triangle2D]:
    ordered = _validate_and_order(_coerce_points(vertices))
    remaining = list(range(len(ordered)))
    triangles: list[Triangle2D] = []
    guard = 0
    while len(remaining) > 3:
        ear_found = False
        for offset, current_index in enumerate(remaining):
            previous_index = remaining[offset - 1]
            next_index = remaining[(offset + 1) % len(remaining)]
            triangle = (
                ordered[previous_index],
                ordered[current_index],
                ordered[next_index],
            )
            if _cross(triangle[0], triangle[1], triangle[2]) <= ABS_AREA_TOL_M2:
                continue
            if any(
                index not in {previous_index, current_index, next_index}
                and _point_in_triangle(ordered[index], triangle, include_boundary=True)
                for index in remaining
            ):
                continue
            triangles.append(triangle)
            del remaining[offset]
            ear_found = True
            break
        guard += 1
        if not ear_found or guard > len(ordered) * len(ordered):
            raise ValueError("No se pudo triangular la huella")

    triangles.append(tuple(ordered[index] for index in remaining))  # type: ignore[arg-type]
    if not math.isclose(
        sum(polygon_area(triangle) for triangle in triangles),
        polygon_area(ordered),
        rel_tol=REL_GEOM_TOL,
        abs_tol=ABS_AREA_TOL_M2,
    ):
        raise ValueError("La triangulación no conserva el área")
    return triangles


def sample_uniform_point(vertices: Sequence[Point2D], rng: random.Random) -> Point2D:
    triangles = triangulate_polygon(vertices)
    areas = [polygon_area(triangle) for triangle in triangles]
    threshold = rng.random() * sum(areas)
    cumulative = 0.0
    selected = triangles[-1]
    for triangle, area in zip(triangles, areas, strict=True):
        cumulative += area
        if threshold <= cumulative:
            selected = triangle
            break

    root = math.sqrt(rng.random())
    second = rng.random()
    weight_a = 1.0 - root
    weight_b = root * (1.0 - second)
    weight_c = root * second
    return (
        selected[0][0] * weight_a + selected[1][0] * weight_b + selected[2][0] * weight_c,
        selected[0][1] * weight_a + selected[1][1] * weight_b + selected[2][1] * weight_c,
    )


def _validate_and_order(points: list[Point2D]) -> list[Point2D]:
    if len(points) < 3:
        raise ValueError("La huella debe tener al menos tres vértices")
    if any(not math.isfinite(value) for point in points for value in point):
        raise ValueError("La huella debe contener solo coordenadas finitas")
    if any(_distance(start, end) <= ABS_COORD_TOL_M for start, end in polygon_edges(points)):
        raise ValueError("La huella contiene vértices consecutivos duplicados")
    if not is_simple_polygon(points):
        raise ValueError("La huella debe ser un polígono simple")

    area = signed_polygon_area(points)
    if abs(area) <= ABS_AREA_TOL_M2:
        raise ValueError("La huella debe tener área positiva")
    if area < 0.0:
        points = list(reversed(points))
    start_index = min(range(len(points)), key=lambda index: (points[index][0], points[index][1]))
    return points[start_index:] + points[:start_index]


def _coerce_points(vertices: Iterable[Sequence[float]]) -> list[Point2D]:
    points: list[Point2D] = []
    for vertex in vertices:
        if len(vertex) != 2:
            raise ValueError("Cada vértice de huella debe contener [x, z]")
        points.append((float(vertex[0]), float(vertex[1])))
    return points


def _segments_intersect(first_a: Point2D, first_b: Point2D, second_a: Point2D, second_b: Point2D) -> bool:
    orientations = (
        _cross(first_a, first_b, second_a),
        _cross(first_a, first_b, second_b),
        _cross(second_a, second_b, first_a),
        _cross(second_a, second_b, first_b),
    )
    if orientations[0] * orientations[1] < 0.0 and orientations[2] * orientations[3] < 0.0:
        return True
    return (
        (abs(orientations[0]) <= ABS_AREA_TOL_M2 and point_on_segment(second_a, first_a, first_b))
        or (abs(orientations[1]) <= ABS_AREA_TOL_M2 and point_on_segment(second_b, first_a, first_b))
        or (abs(orientations[2]) <= ABS_AREA_TOL_M2 and point_on_segment(first_a, second_a, second_b))
        or (abs(orientations[3]) <= ABS_AREA_TOL_M2 and point_on_segment(first_b, second_a, second_b))
    )


def _line_intersection(
    first: tuple[Point2D, Point2D],
    second: tuple[Point2D, Point2D],
) -> Point2D | None:
    first_direction = (first[1][0] - first[0][0], first[1][1] - first[0][1])
    second_direction = (second[1][0] - second[0][0], second[1][1] - second[0][1])
    denominator = first_direction[0] * second_direction[1] - first_direction[1] * second_direction[0]
    if abs(denominator) <= VECTOR_NORM_TOL:
        return None
    delta = (second[0][0] - first[0][0], second[0][1] - first[0][1])
    scale = (delta[0] * second_direction[1] - delta[1] * second_direction[0]) / denominator
    return (
        first[0][0] + scale * first_direction[0],
        first[0][1] + scale * first_direction[1],
    )


def _point_in_triangle(point: Point2D, triangle: Triangle2D, *, include_boundary: bool) -> bool:
    crosses = [
        _cross(triangle[index], triangle[(index + 1) % 3], point)
        for index in range(3)
    ]
    if include_boundary:
        return all(value >= -ABS_AREA_TOL_M2 for value in crosses)
    return all(value > ABS_AREA_TOL_M2 for value in crosses)


def _cross(origin: Point2D, first: Point2D, second: Point2D) -> float:
    return (first[0] - origin[0]) * (second[1] - origin[1]) - (
        first[1] - origin[1]
    ) * (second[0] - origin[0])


def _distance(first: Point2D, second: Point2D) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _quantize(value: float) -> float:
    rounded = round(value, COORD_DECIMALS)
    return 0.0 if abs(rounded) <= ABS_COORD_TOL_M else rounded
