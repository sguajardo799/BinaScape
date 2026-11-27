from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.models import CircularArrayOutputConfig, ReceiverOutputsConfig


def test_array_output_defaults_and_derived_channel_count() -> None:
    disabled = CircularArrayOutputConfig()
    assert not disabled.enabled
    assert not disabled.required
    assert disabled.output_subdir == "circular_array"
    assert disabled.num_channels is None

    output = CircularArrayOutputConfig(enabled=True, num_microphones=8, radius_m=0.05)
    assert output.num_channels == 8
    assert "num_channels" not in output.model_dump()


@pytest.mark.parametrize("count", [0, 1, -2, 2.5, 8.0, "8", True])
def test_array_rejects_invalid_microphone_count(count: object) -> None:
    with pytest.raises(ValidationError, match="num_microphones"):
        CircularArrayOutputConfig(enabled=True, num_microphones=count, radius_m=0.05)


@pytest.mark.parametrize("radius", [0, -0.05, float("inf"), float("-inf"), float("nan")])
def test_array_rejects_invalid_radius(radius: float) -> None:
    with pytest.raises(ValidationError, match="radius_m"):
        CircularArrayOutputConfig(enabled=True, num_microphones=8, radius_m=radius)


@pytest.mark.parametrize("geometry", [{}, {"num_microphones": 8}, {"radius_m": 0.05}])
def test_enabled_array_requires_geometry(geometry: dict) -> None:
    with pytest.raises(ValidationError, match="requiere num_microphones y radius_m"):
        CircularArrayOutputConfig.model_validate({"enabled": True, **geometry})


@pytest.mark.parametrize("field", ["num_channels", "ir_catalog_path", "file_pattern", "num_hrtfs"])
def test_array_rejects_catalog_or_duplicate_channel_settings(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        CircularArrayOutputConfig.model_validate(
            {"enabled": True, "num_microphones": 8, "radius_m": 0.05, field: 8}
        )


def test_disabled_array_still_validates_supplied_geometry() -> None:
    with pytest.raises(ValidationError, match="radius_m"):
        CircularArrayOutputConfig(enabled=False, radius_m=-1)


def test_receiver_outputs_allow_omitted_binaural_and_array() -> None:
    outputs = ReceiverOutputsConfig()
    assert outputs.binaural_hrtf is None
    assert outputs.circular_array is None


def test_loader_array_only_needs_no_receiver_catalog(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[3] / "configs" / "experiments" / "static_example.yml"
    raw_config = yaml.safe_load(example.read_text(encoding="utf-8"))
    raw_config["receiver_outputs"] = {
        "circular_array": {"enabled": True, "num_microphones": 8, "radius_m": 0.05}
    }
    raw_config["hearing_degradation"]["enabled"] = False
    raw_config["hearing_degradation"]["input_targets"] = []
    config_path = tmp_path / "array.yml"
    config_path.write_text(yaml.safe_dump(raw_config), encoding="utf-8")

    config = load_config(config_path)

    assert config.receiver_outputs.binaural_hrtf is None
    assert config.receiver_outputs.circular_array.num_channels == 8
    assert config.raven.base_rpf_file.is_absolute()
    assert config.outputs.artifact_root.is_absolute()
