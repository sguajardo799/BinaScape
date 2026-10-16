import math
import json
import random
from pathlib import Path

import pytest

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.models import CircularArrayOutputConfig
from acoustic_orchestrator.experiment.receiver_geometry import build_circular_array_descriptor
from acoustic_orchestrator.experiment.sampler import _sample_receiver
from acoustic_orchestrator.experiment import sampler


@pytest.mark.parametrize("count", [4, 8])
@pytest.mark.parametrize("yaw", [0, 90])
def test_circular_array_positions_and_channel_order(count: int, yaw: float) -> None:
    center = [2.0, 1.4, 3.0]
    output = CircularArrayOutputConfig(enabled=True, num_microphones=count, radius_m=0.2)
    descriptor = build_circular_array_descriptor(center, {"yaw": yaw, "pitch": 30, "roll": 40}, output)
    assert descriptor["center_m"] == center
    assert descriptor["num_microphones"] == count
    assert descriptor["yaw_deg"] == yaw
    assert len(descriptor["microphones"]) == count
    for k, microphone in enumerate(descriptor["microphones"]):
        assert microphone["microphone_index"] == k
        assert microphone["channel_index"] == k + 1
        angle = math.radians(yaw + 360 * k / count)
        assert microphone["position_m"] == pytest.approx(
            [center[0] + 0.2 * math.cos(angle), center[1], center[2] + 0.2 * math.sin(angle)]
        )
        assert math.dist(center, microphone["position_m"]) == pytest.approx(0.2)


def test_circular_array_ignores_pitch_and_roll() -> None:
    output = CircularArrayOutputConfig(enabled=True, num_microphones=4, radius_m=0.05)
    center = [1.0, 1.5, 2.0]
    assert build_circular_array_descriptor(center, {"yaw": 45, "pitch": 0, "roll": 0}, output) == (
        build_circular_array_descriptor(center, {"yaw": 45, "pitch": 70, "roll": -20}, output)
    )


def test_receiver_sampling_adds_only_enabled_array_descriptor() -> None:
    example = Path(__file__).resolve().parents[3] / "configs" / "experiments" / "static_example.yml"
    config = load_config(example)
    room = {"geometry": {"height_m": 3.0, "footprint_vertices_m": [[0, 0], [5, 0], [5, 4], [0, 4]]}}
    plain = _sample_receiver(config, room, random.Random(42))
    assert "circular_array" not in plain
    config.receiver_outputs.circular_array = CircularArrayOutputConfig(
        enabled=True, num_microphones=8, radius_m=0.05
    )
    array = _sample_receiver(config, room, random.Random(42))
    assert array["position_m"] == plain["position_m"]
    assert array["orientation_deg"] == plain["orientation_deg"]
    assert array["circular_array"]["center_m"] == array["position_m"]
    config.receiver_outputs.circular_array.enabled = False
    assert _sample_receiver(config, room, random.Random(42)) == plain


def _array_validation_fixture(footprint: list, center: list, radius: float) -> tuple:
    example = Path(__file__).resolve().parents[3] / "configs" / "experiments" / "static_example.yml"
    config = load_config(example)
    output = CircularArrayOutputConfig(enabled=True, num_microphones=4, radius_m=radius)
    config.receiver_outputs.circular_array = output
    config.scene_validation.min_distance_source_to_receiver_m = 0.5
    room = {"geometry": {"height_m": 3, "footprint_vertices_m": footprint, "generated_from": {}}}
    receiver = {
        "position_m": center,
        "circular_array": build_circular_array_descriptor(center, {"yaw": 0}, output),
    }
    return config, room, receiver


@pytest.mark.parametrize(
    "footprint, center, source",
    [
        ([[0, 0], [5, 0], [5, 5], [0, 5]], [0.1, 1.5, 2], [3, 1.5, 3]),
        ([[0, 0], [5, 0], [4, 5], [1, 5]], [0.5, 1.5, 2], [3, 1.5, 2]),
        ([[0, 0], [5, 0], [5, 2], [2, 2], [2, 5], [0, 5]], [1.9, 1.5, 3], [1, 1.5, 1]),
    ],
)
def test_microphone_outside_room_rejects_valid_center(footprint: list, center: list, source: list) -> None:
    config, room, receiver = _array_validation_fixture(footprint, center, 0.2)
    assert sampler._inside_room(center, room)
    assert not sampler._is_valid_scene(config, room, receiver, [{"position_m": source}])


def test_source_distance_checks_outer_microphone() -> None:
    config, room, receiver = _array_validation_fixture(
        [[0, 0], [5, 0], [5, 5], [0, 5]], [2, 1.5, 2], 0.2
    )
    source = {"position_m": [2.6, 1.5, 2]}
    assert sampler._is_valid_scene(config, room, {"position_m": receiver["position_m"]}, [source])
    assert not sampler._is_valid_scene(config, room, receiver, [source])
    source["position_m"] = [3, 1.5, 2]
    assert sampler._is_valid_scene(config, room, receiver, [source])


def test_invalid_array_exhausts_existing_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    config, room, receiver = _array_validation_fixture(
        [[0, 0], [5, 0], [5, 5], [0, 5]], [0.1, 1.5, 2], 0.2
    )
    config.scene_validation.max_scene_attempts = 2
    config.scene_validation.max_receiver_attempts = 3
    monkeypatch.setattr(sampler, "_sample_room", lambda *args: room)
    monkeypatch.setattr(sampler, "_apply_virtual_treatments", lambda *args: None)
    monkeypatch.setattr(sampler, "estimate_room_rt30_s", lambda *args: {"estimated_rt30_s": 0.5})
    monkeypatch.setattr(sampler, "_sample_receiver", lambda *args: receiver)
    monkeypatch.setattr(sampler, "_sample_sources", lambda *args: [{"position_m": [3, 1.5, 3]}])
    with pytest.raises(sampler.SceneSamplingFailure) as exc:
        sampler.sample_static_scene(config, None, 0)
    assert exc.value.diagnostics["scene_attempts"] == 2
    assert exc.value.diagnostics["rejections_by_reason"]["sources"] == 6


def test_documented_scene_receiver_matches_geometry_contract() -> None:
    document = (Path(__file__).resolve().parents[1] / "examples" / "circular_array_scenes.md").read_text(encoding="utf-8")
    receiver = json.loads(document.split("```json\n", 1)[1].split("```", 1)[0])
    output = CircularArrayOutputConfig(enabled=True, num_microphones=4, radius_m=0.05)
    expected = build_circular_array_descriptor(receiver["position_m"], receiver["orientation_deg"], output)
    assert receiver["hrtfs"] == []
    for actual, planned in zip(receiver["circular_array"]["microphones"], expected["microphones"], strict=True):
        assert actual["position_m"] == pytest.approx(planned["position_m"])
        assert actual["channel_index"] == planned["channel_index"]
