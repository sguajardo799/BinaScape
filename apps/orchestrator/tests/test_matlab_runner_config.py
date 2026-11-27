"""Configuration of the MATLAB execution mode (`execution.matlab_runner`)."""
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.models import ExecutionConfig, MatlabPoolConfig
from acoustic_orchestrator.config.validator import validate_config
from acoustic_orchestrator.pipeline.static_plan import normalized_static_config
from test_render_backend_config import pra_installed, write_pyroomacoustics_config  # noqa: F401
from test_validator import _write_config as _write_raven_config


def _with_execution(path: Path, **execution) -> Path:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["execution"].update(execution)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_runner_defaults_to_process() -> None:
    config = ExecutionConfig(num_simulations=1, num_workers=1)
    assert config.matlab_runner == "process"
    assert config.matlab_pool.model_dump() == {"startup_timeout_s": 600.0, "heartbeat_timeout_s": 300.0}


def test_existing_raven_configuration_keeps_process_mode(tmp_path: Path) -> None:
    config = load_config(_write_raven_config(tmp_path))
    validate_config(config)
    assert config.execution.matlab_runner == "process"


def test_pool_accepted_for_raven(tmp_path: Path) -> None:
    config = load_config(_with_execution(_write_raven_config(tmp_path), matlab_runner="pool"))
    validate_config(config)
    assert config.execution.matlab_runner == "pool"


def test_unknown_runner_names_field_and_values(tmp_path: Path) -> None:
    path = _with_execution(_write_raven_config(tmp_path), matlab_runner="threads")
    with pytest.raises(ValidationError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "execution.matlab_runner" in message and "'process'" in message and "'pool'" in message


def test_pool_rejected_for_non_raven_backend(tmp_path: Path, pra_installed: None) -> None:  # noqa: F811
    path = write_pyroomacoustics_config(tmp_path, **{"execution.matlab_runner": "pool"})
    with pytest.raises(ValueError) as excinfo:
        validate_config(load_config(path))
    message = str(excinfo.value)
    assert "execution.matlab_runner=pool" in message and "render.backend=pyroomacoustics" in message


def test_process_runner_still_valid_for_non_raven_backend(tmp_path: Path, pra_installed: None) -> None:  # noqa: F811
    validate_config(load_config(write_pyroomacoustics_config(tmp_path, **{"execution.matlab_runner": "process"})))


@pytest.mark.parametrize("field,value", [
    ("startup_timeout_s", 0), ("startup_timeout_s", -1), ("startup_timeout_s", True),
    ("startup_timeout_s", float("inf")), ("heartbeat_timeout_s", 0), ("heartbeat_timeout_s", "30"),
])
def test_pool_timeouts_must_be_positive_finite(field: str, value) -> None:
    with pytest.raises(ValidationError):
        MatlabPoolConfig(**{field: value})


def test_plan_identity_ignores_runner(tmp_path: Path) -> None:
    path = _write_raven_config(tmp_path)
    base = normalized_static_config(load_config(path))
    pooled = normalized_static_config(load_config(_with_execution(
        path, matlab_runner="pool", matlab_pool={"startup_timeout_s": 120, "heartbeat_timeout_s": 60})))
    assert pooled == base
