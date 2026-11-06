"""RAVEN compatibility regression against goldens captured before the backend change."""

from pathlib import Path

import pytest
import raven_golden

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


@pytest.mark.parametrize("path", [r"C:\workspace\assets\hrtf", "C:/workspace/assets/hrtf"])
def test_golden_path_placeholders_are_host_independent(path: str) -> None:
    assert raven_golden._placeholders(path, [("C:/workspace", "<REPO>")]) == "<REPO>/assets/hrtf"


def test_static_golden_ignores_edits_to_local_experiment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = normalized_example_config("static_example")
    editable = tmp_path / "configs" / "experiments" / "static_example.yml"
    editable.parent.mkdir(parents=True)
    editable.write_text("experiment: local-user-work\n", encoding="utf-8")
    monkeypatch.setattr(raven_golden, "REPOSITORY", tmp_path)
    assert normalized_example_config("static_example") == expected
    assert editable.read_text(encoding="utf-8") == "experiment: local-user-work\n"


def test_golden_normalization_does_not_mask_functional_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    historical, _ = raven_golden.example_input("static_example")
    path = tmp_path / "configs" / "experiments" / "static_example.yml"
    path.parent.mkdir(parents=True)
    path.write_text(historical.read_text(encoding="utf-8").replace("num_simulations: 100", "num_simulations: 101"), encoding="utf-8")
    monkeypatch.setattr(raven_golden, "example_input", lambda name: (path, tmp_path))
    expected = load_golden("static_example.normalized_config.json")
    assert normalized_example_config("static_example") != expected
    expected["execution"]["num_simulations"] = 101
    assert normalized_example_config("static_example") == expected
