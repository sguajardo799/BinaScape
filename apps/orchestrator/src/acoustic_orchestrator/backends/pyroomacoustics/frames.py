"""Rigid transform between the public frame and the Pyroomacoustics frame.

Public frame: ``(x, y, z)`` in meters with ``y`` vertical (``public_xyz_y_up``).
Backend frame: ``(X, Y, Z)`` with ``Z`` vertical, obtained by the proper rotation
``ROTATION`` (``(x, -z, y)``) followed by a per-room translation
``t = (-min x, max z, 0)`` that anchors the footprint's bounding box at the
backend origin, as the backend's rectangular room primitive requires.
The translation has no vertical component, so the floor stays at ``Z = 0``.

Pure Python on purpose: importable without numpy or Pyroomacoustics.
"""

from collections.abc import Sequence

Vector3 = tuple[float, float, float]

ROTATION: tuple[Vector3, Vector3, Vector3] = ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))
NO_TRANSLATION: Vector3 = (0.0, 0.0, 0.0)


def _rotate(matrix: Sequence[Sequence[float]], point: Sequence[float]) -> Vector3:
    return tuple(sum(row[i] * float(point[i]) for i in range(3)) for row in matrix)  # type: ignore[return-value]


def _transpose(matrix: Sequence[Sequence[float]]) -> tuple[Vector3, Vector3, Vector3]:
    return tuple(tuple(float(matrix[r][c]) for r in range(3)) for c in range(3))  # type: ignore[return-value]


def determinant(matrix: Sequence[Sequence[float]] = ROTATION) -> float:
    (a, b, c), (d, e, f), (g, h, i) = matrix
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def footprint_translation(footprint_vertices_m: Sequence[Sequence[float]]) -> Vector3:
    """``t = (-min x, max z, 0)`` for a public footprint of ``[x, z]`` vertices."""
    if not footprint_vertices_m:
        raise ValueError("footprint must have vertices")
    xs = [float(vertex[0]) for vertex in footprint_vertices_m]
    zs = [float(vertex[1]) for vertex in footprint_vertices_m]
    return (-min(xs) + 0.0, max(zs) + 0.0, 0.0)


def public_to_backend(point: Sequence[float], translation: Sequence[float] = NO_TRANSLATION) -> Vector3:
    """Public point ``(x, y, z)`` → backend ``R·p + t``."""
    if len(point) != 3:
        raise ValueError("point must have three coordinates")
    rotated = _rotate(ROTATION, point)
    return (rotated[0] + float(translation[0]), rotated[1] + float(translation[1]), rotated[2] + float(translation[2]))


def backend_to_public(point: Sequence[float], translation: Sequence[float] = NO_TRANSLATION) -> Vector3:
    """Backend point ``(X, Y, Z)`` → public ``Rᵀ·(P - t)``."""
    if len(point) != 3:
        raise ValueError("point must have three coordinates")
    shifted = (float(point[0]) - float(translation[0]), float(point[1]) - float(translation[1]),
               float(point[2]) - float(translation[2]))
    return _rotate(_transpose(ROTATION), shifted)


def public_direction_to_backend(direction: Sequence[float]) -> Vector3:
    """Directions rotate but are never translated."""
    return _rotate(ROTATION, direction)


def transform_metadata(translation: Sequence[float]) -> dict:
    return {
        "from_frame": "public_xyz_y_up",
        "to_frame": "pyroomacoustics_xyz_z_up",
        "rotation_matrix": [list(row) for row in ROTATION],
        "translation_m": [float(value) for value in translation],
        "definition": "backend = rotation_matrix · public + translation_m",
    }
