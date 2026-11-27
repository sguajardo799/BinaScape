from pathlib import Path

import pytest
import yaml

from acoustic_orchestrator.config.loader import load_config


EXAMPLE_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "experiments" / "static_example.yml"


def _write_config(tmp_path: Path, experiment_id: str, run_name: str | None = None, seed: int = 7) -> Path:
    raw = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    raw["experiment"]["experiment_id"] = experiment_id
    raw["experiment"]["random_seed"] = seed
    if run_name is None:
        raw["outputs"].pop("run_name", None)
    else:
        raw["outputs"]["run_name"] = run_name
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_seed_placeholder_resolves_in_experiment_id_and_default_run_name(tmp_path):
    config = load_config(_write_config(tmp_path, "exp_s{seed}", seed=42))
    assert config.experiment.experiment_id == "exp_s42"
    assert config.outputs.run_name == "exp_s42"
    assert config.hearing_degradation.output_dir.parent.parent.name == "exp_s42"


def test_seed_placeholder_resolves_in_explicit_run_name(tmp_path):
    config = load_config(_write_config(tmp_path, "exp", run_name="run_{seed}_a", seed=3))
    assert config.experiment.experiment_id == "exp"
    assert config.outputs.run_name == "run_3_a"


def test_id_without_braces_is_unchanged(tmp_path):
    config = load_config(_write_config(tmp_path, "plain_id", seed=42))
    assert config.experiment.experiment_id == "plain_id"
    assert config.outputs.run_name == "plain_id"


@pytest.mark.parametrize("bad_id", ["exp_{foo}", "exp_{seed", "exp_seed}", "exp_{}", "exp_{0}", "exp_{seed.real}", "exp_{seed:03d}"])
def test_invalid_placeholder_in_experiment_id_is_rejected(tmp_path, bad_id):
    with pytest.raises(ValueError, match="experiment.experiment_id"):
        load_config(_write_config(tmp_path, bad_id))


def test_invalid_placeholder_in_run_name_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="outputs.run_name"):
        load_config(_write_config(tmp_path, "exp", run_name="run_{bar}"))
