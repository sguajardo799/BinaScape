"""Development-only plotting helpers for sampled room footprints."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


def plot_room_geometry(
    geometry: Mapping[str, Any],
    *,
    output_path: str | Path | None = None,
    show: bool = False,
) -> Any:
    """Plot a room footprint and return the matplotlib figure.

    Importing matplotlib is deferred so production manifest generation does not
    require the optional development dependency.
    """
    import matplotlib.pyplot as plt

    manifest = geometry
    if "room" in geometry:
        geometry = geometry["room"]["geometry"]
    vertices = geometry["footprint_vertices_m"]
    points = [(float(vertex[0]), float(vertex[1])) for vertex in vertices]
    closed = [*points, points[0]]
    figure, axis = plt.subplots()
    axis.plot([point[0] for point in closed], [point[1] for point in closed], "o-")
    for index, point in enumerate(points, start=1):
        axis.annotate(f"wall_{index:03d}", point)
    axis.set_aspect("equal", adjustable="box")
    axis.set_xlabel("x (m)")
    axis.set_ylabel("z (m)")
    axis.set_title(str(geometry.get("type", "room")))
    receiver = manifest.get("receiver") if isinstance(manifest, Mapping) else None
    if isinstance(receiver, Mapping) and receiver.get("position_m"):
        point = receiver["position_m"]
        axis.scatter([point[0]], [point[2]], marker="x", label="receiver")
    sources = manifest.get("sources", []) if isinstance(manifest, Mapping) else []
    for source in sources:
        if isinstance(source, Mapping) and source.get("position_m"):
            point = source["position_m"]
            axis.scatter([point[0]], [point[2]], marker=".", label=str(source.get("source_id", "source")))
    if receiver or sources:
        axis.legend()
    figure.tight_layout()
    if output_path is not None:
        figure.savefig(output_path, dpi=150)
    if show:
        plt.show()
    return figure
