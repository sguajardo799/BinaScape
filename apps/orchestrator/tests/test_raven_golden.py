"""RAVEN compatibility regression against goldens captured before the backend change."""

from pathlib import Path

import pytest

from raven_golden import (
    EXAMPLES,
    capture_example_manifests,
    capture_matlab_command,
    load_golden,
    normalized_example_config,
)


@pytest.mark.parametrize("name", EXAMPLES)
def test_golden_fixtures_load(name: str) -> None:
    assert isinstance(load_golden(f"{name}.normalized_config.json"), dict)
    manifests = load_golden(f"{name}.manifests.json")
    assert manifests["scene_manifests"] and manifests["runtime_manifests"]


@pytest.mark.parametrize("name", EXAMPLES)
def test_normalized_config_matches_golden(name: str) -> None:
    assert normalized_example_config(name) == load_golden(f"{name}.normalized_config.json")


@pytest.mark.parametrize("name", EXAMPLES)
def test_raven_scene_and_runtime_manifests_match_golden(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert capture_example_manifests(tmp_path, name, monkeypatch) == load_golden(f"{name}.manifests.json")


def test_matlab_command_matches_golden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert capture_matlab_command(tmp_path, monkeypatch) == load_golden("matlab_command.json")
