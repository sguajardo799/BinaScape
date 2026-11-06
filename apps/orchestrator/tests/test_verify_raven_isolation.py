from pathlib import Path

import pytest
from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.verify_raven_isolation import (
    FREQUENCIES,
    compare_t30,
    fingerprint,
    prepare_configs,
)
from test_generate_manifests import _build_workspace, _write_config


def test_harness_configs_are_resolved_and_keep_the_same_scene_sampling(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    source = _write_config(workspace, "harness_source")
    root = tmp_path / "evidence"
    root.mkdir()
    first, second = [load_config(path) for path in prepare_configs(source, root)]
    assert first.execution.num_simulations == second.execution.num_simulations == 2
    assert first.execution.num_workers == 1 and second.execution.num_workers == 4
    assert not first.hearing_degradation.enabled and not second.hearing_degradation.enabled
    assert first.room_sampling == second.room_sampling
    assert first.source_sampling == second.source_sampling
    assert first.receiver_sampling == second.receiver_sampling
    assert first.experiment.random_seed == second.experiment.random_seed
    assert first.outputs.run_name != second.outputs.run_name
    assert first.raven.base_rpf_file.is_absolute()
    assert not first.execution.overwrite_existing and not first.execution.resume_if_possible


def test_t30_comparison_uses_all_bands_and_rejects_bad_mapping() -> None:
    reference = {"band_frequencies_hz": FREQUENCIES, "t30_s": [0.5] * 10}
    assert compare_t30(reference, reference)["ok"]
    assert compare_t30(reference, reference | {"t30_s": [0.5] * 9 + [0.500001]})["ok"]
    assert not compare_t30(reference, reference | {"t30_s": [0.5] * 9 + [0.6]})["ok"]
    with pytest.raises(ValueError, match="frequency"):
        compare_t30(reference, reference | {"band_frequencies_hz": list(reversed(FREQUENCIES))})
    with pytest.raises(ValueError, match="finite positive"):
        compare_t30(reference, reference | {"t30_s": [float("nan")] * 10})


def test_global_fingerprint_detects_changed_added_and_removed_files(tmp_path: Path) -> None:
    first = tmp_path / "material.mat"
    first.write_text("original")
    original = fingerprint([tmp_path])
    first.write_text("modified")
    assert fingerprint([tmp_path]) != original
    first.write_text("original")
    second = tmp_path / "new.ac"
    second.write_text("new")
    assert fingerprint([tmp_path]) != original
    second.unlink()
    assert fingerprint([tmp_path]) == original
    first.unlink()
    assert fingerprint([tmp_path]) != original
