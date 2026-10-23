import pytest
from pydantic import ValidationError

from acoustic_orchestrator.config.models import ExecutionConfig, MatlabProcessRetryConfig


def test_old_execution_defaults():
    config = ExecutionConfig(num_simulations=1, num_workers=1)
    assert config.matlab_process_retry.model_dump() == {
        "max_attempts": 3, "initial_delay_s": 2.0, "max_delay_s": 30.0, "max_consecutive_failures": 5,
    }
    assert MatlabProcessRetryConfig(max_attempts=1).max_attempts == 1


@pytest.mark.parametrize("field,value", [
    ("max_attempts", True), ("max_attempts", 0), ("max_attempts", -1),
    ("max_attempts", 1.0), ("max_attempts", "3"),
    ("max_consecutive_failures", False), ("max_consecutive_failures", 0),
    ("initial_delay_s", True), ("initial_delay_s", -1), ("initial_delay_s", "2"),
    ("initial_delay_s", float("nan")), ("initial_delay_s", float("inf")),
    ("max_delay_s", float("-inf")), ("max_delay_s", -1), ("max_delay_s", 1),
])
def test_strict_retry_config(field, value):
    with pytest.raises(ValidationError):
        MatlabProcessRetryConfig(**{field: value})


def test_zero_delay_and_integer_seconds():
    assert MatlabProcessRetryConfig(initial_delay_s=0, max_delay_s=0).initial_delay_s == 0.0
