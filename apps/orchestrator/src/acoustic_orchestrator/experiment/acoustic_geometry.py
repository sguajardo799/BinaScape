"""Geometry-independent acoustic surface contract and polygon adapter."""

from __future__ import annotations

import math

from acoustic_orchestrator.experiment.geometry import edge_lengths, polygon_area


def build_polygon_acoustic_geometry(geometry: dict) -> dict:
    """Adapt the current polygon generator to the generic acoustic contract."""
    footprint = [tuple(vertex) for vertex in geometry["footprint_vertices_m"]]
    height_m = float(geometry["height_m"])
    floor_area_m2 = polygon_area(footprint)
    surfaces = {
        wall_id: {"surface_type": "wall", "area_m2": length_m * height_m}
        for wall_id, length_m in zip(
            geometry["wall_ids"], edge_lengths(footprint), strict=True
        )
    }
    surfaces["floor"] = {"surface_type": "floor", "area_m2": floor_area_m2}
    surfaces["ceiling"] = {"surface_type": "ceiling", "area_m2": floor_area_m2}
    return {
        "volume_m3": floor_area_m2 * height_m,
        "floor_area_m2": floor_area_m2,
        "surfaces": surfaces,
    }


def get_acoustic_geometry(room: dict) -> dict:
    """Return and validate the contract, adapting legacy polygon rooms if needed."""
    contract = room.get("acoustic_geometry")
    if contract is None:
        geometry = room.get("geometry")
        if not isinstance(geometry, dict):
            raise ValueError("La sala debe entregar acoustic_geometry o un geometry poligonal adaptable")
        contract = build_polygon_acoustic_geometry(geometry)
    validate_acoustic_geometry(contract)
    return contract


def validate_acoustic_geometry(contract: dict) -> None:
    volume_m3 = contract.get("volume_m3")
    floor_area_m2 = contract.get("floor_area_m2")
    surfaces = contract.get("surfaces")
    if not _positive_finite(volume_m3):
        raise ValueError("acoustic_geometry.volume_m3 debe ser finito y positivo")
    if not _positive_finite(floor_area_m2):
        raise ValueError("acoustic_geometry.floor_area_m2 debe ser finito y positivo")
    if not isinstance(surfaces, dict) or not surfaces:
        raise ValueError("acoustic_geometry.surfaces debe ser un mapa no vacío")
    for surface_id, surface in surfaces.items():
        if not isinstance(surface_id, str) or not surface_id or not isinstance(surface, dict):
            raise ValueError("Cada superficie acústica debe tener identidad y metadata")
        if surface.get("surface_type") not in {"wall", "floor", "ceiling"}:
            raise ValueError(f"Tipo acústico inválido para {surface_id}")
        if not _positive_finite(surface.get("area_m2")):
            raise ValueError(f"Área acústica inválida para {surface_id}")


def _positive_finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )
