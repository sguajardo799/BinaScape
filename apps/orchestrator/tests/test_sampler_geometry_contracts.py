import random
from collections import Counter
from types import SimpleNamespace

import pytest

from acoustic_orchestrator.experiment import sampler


def test_shape_selection_tracks_configured_distribution() -> None:
    shapes = [
        SimpleNamespace(type="shoebox", probability=0.2),
        SimpleNamespace(type="trapezoid", probability=0.3),
        SimpleNamespace(type="l_shape", probability=0.5),
    ]
    config = SimpleNamespace(
        room_sampling=SimpleNamespace(geometry=SimpleNamespace(shape_mix=shapes))
    )
    rng = random.Random(20260922)

    counts = Counter(
        sampler._sample_room_shape(config, rng).type  # type: ignore[arg-type]
        for _ in range(20_000)
    )

    assert counts["shoebox"] / 20_000 == pytest.approx(0.2, abs=0.015)
    assert counts["trapezoid"] / 20_000 == pytest.approx(0.3, abs=0.015)
    assert counts["l_shape"] / 20_000 == pytest.approx(0.5, abs=0.015)


def test_wall_selection_is_weighted_by_physical_edge_length(monkeypatch: pytest.MonkeyPatch) -> None:
    room = {
        "geometry": {
            "height_m": 3.0,
            "footprint_vertices_m": [(0.0, 0.0), (8.0, 0.0), (8.0, 2.0), (0.0, 2.0)],
            "wall_ids": ["wall_001", "wall_002", "wall_003", "wall_004"],
        }
    }
    monkeypatch.setattr(sampler, "_inside_room", lambda position, sampled_room: True)
    monkeypatch.setattr(sampler, "_source_wall_clearance", lambda position, sampled_room: 0.5)
    rng = random.Random(8042)

    counts = Counter(
        sampler._sample_wall_position(room, rng)[1]  # type: ignore[index]
        for _ in range(20_000)
    )

    long_edges = counts["wall_001"] + counts["wall_003"]
    short_edges = counts["wall_002"] + counts["wall_004"]
    assert long_edges / short_edges == pytest.approx(4.0, rel=0.08)


def test_each_surface_draws_its_material_independently() -> None:
    config = SimpleNamespace(
        room_sampling=SimpleNamespace(
            materials=SimpleNamespace(
                walls=["wall_a", "wall_b"],
                floor=["floor_a"],
                ceiling=["ceiling_a"],
            )
        )
    )

    class RecordingRandom:
        def __init__(self) -> None:
            self.pools: list[tuple[str, ...]] = []

        def choice(self, values: list[str]) -> str:
            self.pools.append(tuple(values))
            return values[len(self.pools) % len(values)]

    rng = RecordingRandom()
    wall_ids = ["wall_001", "wall_002", "wall_003", "wall_004"]

    materials = sampler._sample_surface_materials(  # type: ignore[arg-type]
        config, wall_ids, rng
    )

    assert list(materials) == [*wall_ids, "floor", "ceiling"]
    assert rng.pools == [
        ("wall_a", "wall_b"),
        ("wall_a", "wall_b"),
        ("wall_a", "wall_b"),
        ("wall_a", "wall_b"),
        ("floor_a",),
        ("ceiling_a",),
    ]
