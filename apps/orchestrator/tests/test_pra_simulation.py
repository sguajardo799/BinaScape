"""[pra] Backend room construction and RIR computation (tasks 3.4, 3.5)."""

import numpy as np
import pytest

from pra_support import require_pyroomacoustics, uniform_surfaces

pra = require_pyroomacoustics()

from acoustic_orchestrator.backends.pyroomacoustics.frames import public_to_backend  # noqa: E402
from acoustic_orchestrator.backends.pyroomacoustics.materials import convert_surface_materials  # noqa: E402
from acoustic_orchestrator.backends.pyroomacoustics.room_geometry import build_room_description  # noqa: E402
from acoustic_orchestrator.backends.pyroomacoustics.simulation import (  # noqa: E402
    IsmParameters,
    build_room,
    compute_rirs,
    effective_max_order,
    runtime_timing,
    seed_package,
)
from acoustic_orchestrator.experiment.geometry import (  # noqa: E402
    build_l_shape_footprint,
    build_shoebox_footprint,
    build_trapezoid_footprint,
    canonical_wall_ids,
)

FS = 44100
FOOTPRINTS = {
    "shoebox": build_shoebox_footprint(4.3, 3.7),
    "trapezoid": build_trapezoid_footprint(4.5, 3.2, 3.6, 0.4),
    "l_shape": build_l_shape_footprint(5.5, 4.5, 1.5, 1.2, "north_east"),
}


def _room(geometry_type: str, *, distinct: bool = True) -> dict:
    footprint = [list(vertex) for vertex in FOOTPRINTS[geometry_type]]
    wall_ids = canonical_wall_ids(footprint)
    surfaces = uniform_surfaces(wall_ids, 0.2)
    if distinct:
        for number, surface in enumerate(surfaces.values()):
            surface["effective_absorption"] = [0.05 + 0.1 * number] * 31
            surface["effective_scattering"] = [0.01 * (number + 1)] * 31
    return {"geometry": {"type": geometry_type, "height_m": 2.7, "footprint_vertices_m": footprint,
                         "wall_ids": wall_ids}, "acoustic_surfaces": surfaces}


def _built(geometry_type: str, parameters: IsmParameters):
    room = _room(geometry_type)
    description = build_room_description(room)
    surfaces = [*description["wall_surface_ids"], "floor", "ceiling"]
    materials = convert_surface_materials(room, surfaces)
    return description, materials, build_room(description, materials, parameters, FS)


@pytest.mark.parametrize("geometry_type, walls", [("shoebox", 6), ("trapezoid", 6), ("l_shape", 8)])
def test_wall_count_and_material_identity(geometry_type: str, walls: int) -> None:
    description, materials, room = _built(geometry_type, IsmParameters(2, 1, False))
    assert len(room.walls) == walls
    centers = [float(c) for c in room.octave_bands.centers]
    assert centers == [125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0]
    if geometry_type == "shoebox":
        mapping = description["shoebox"]["wall_surface_ids"]
    else:
        mapping = {str(i): surface for i, surface in enumerate(description["wall_surface_ids"])}
        mapping.update(floor="floor", ceiling="ceiling")
        # Side wall i spans backend vertices i -> i+1 (no internal reordering happened).
        for i in range(len(description["vertices_xy"])):
            corners = next(w for w in room.walls if w.name == str(i)).corners
            expected = np.array(description["vertices_xy"])[[i, (i + 1) % len(description["vertices_xy"])]]
            np.testing.assert_allclose(corners[:2, :2].T, expected, atol=1e-6)
    for wall in room.walls:
        surface = mapping[wall.name]
        # Bank centers 125..16000 Hz equal our octave indexes 2..9 (float32 storage).
        np.testing.assert_allclose(wall.absorption, materials[surface]["absorption"][2:], atol=1e-6)
        np.testing.assert_allclose(wall.scatter, materials[surface]["scattering"][2:], atol=1e-6)


def test_parameters_applied() -> None:
    parameters = IsmParameters(max_order=5, max_order_non_shoebox=2, air_absorption=True,
                               temperature_c=25.0, humidity_pct=50.0)
    description, _, shoebox = _built("shoebox", parameters)
    _, _, l_room = _built("l_shape", parameters)
    assert shoebox.max_order == 5 == effective_max_order(description, parameters)
    assert l_room.max_order == 2
    for room in (shoebox, l_room):
        assert room.simulator_state["rt_needed"] is False
        assert room.simulator_state["random_ism_needed"] is False
        assert room.air_absorption is not None and len(room.air_absorption) == 8
        assert room.c == pytest.approx(347.02, abs=0.01)
    _, _, plain = _built("shoebox", IsmParameters(3, 3, False))
    assert plain.air_absorption is None and plain.c == pytest.approx(343.0)


def test_low_band_characterization() -> None:
    """31.5 and 63 Hz have no independent effect: the bank starts at 125 Hz."""
    room = _room("shoebox", distinct=False)
    for surface in room["acoustic_surfaces"].values():
        values = [0.3] * 31
        values[2], values[5] = 0.9, 0.7  # 31.5 Hz and 63 Hz octave centers
        surface["effective_absorption"] = values
    description = build_room_description(room)
    materials = convert_surface_materials(room, [*description["wall_surface_ids"], "floor", "ceiling"])
    built = build_room(description, materials, IsmParameters(1, 1, False), FS)
    for wall in built.walls:
        np.testing.assert_allclose(wall.absorption, [0.3] * 8, atol=1e-6)


def test_anechoic_rirs_have_single_dominant_peak_and_timing() -> None:
    room = _room("shoebox")
    description = build_room_description(room)
    materials = convert_surface_materials(room, [*description["wall_surface_ids"], "floor", "ceiling"])
    t = description["translation_m"]
    assert seed_package(42) == 42
    built = build_room(description, materials, IsmParameters(0, 0, False), FS)
    sources = [public_to_backend(p, t) for p in ([1.0, 1.5, 1.0], [3.0, 1.2, 2.5])]
    mics = [public_to_backend(p, t) for p in ([2.0, 1.5, 2.0], [2.05, 1.5, 2.0], [2.0, 1.5, 2.05])]
    rirs = compute_rirs(built, sources, mics)
    assert len(rirs) == 2 and all(len(row) == 3 for row in rirs)
    timing = runtime_timing(built)
    assert timing["global_delay_samples"] == timing["frac_delay_length"] // 2 == 40
    assert timing["high_pass_filter"]["enabled"] is True and timing["high_pass_filter"]["cutoff_hz"] == 10.0
    assert timing["speed_of_sound_m_s"] == pytest.approx(343.0)
    for s, row in enumerate(rirs):
        for m, rir in enumerate(row):
            energy = rir ** 2
            peak = int(np.argmax(np.abs(rir)))
            distance = float(np.linalg.norm(np.subtract(sources[s], mics[m])))
            assert abs(peak - (round(distance / built.c * FS) + timing["global_delay_samples"])) <= 1
            window = slice(max(0, peak - 40), peak + 41)
            assert energy[window].sum() / energy.sum() > 0.999  # Only the direct path.


def test_negative_seed_is_mapped_deterministically() -> None:
    assert seed_package(-1) == 2**64 - 1
    first = pra.random.get_rng().integers(1 << 30)
    seed_package(-1)
    assert pra.random.get_rng().integers(1 << 30) == first
