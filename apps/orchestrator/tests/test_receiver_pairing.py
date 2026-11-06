from pathlib import Path
import json
import random

import pytest

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.validator import validate_config
from acoustic_orchestrator.experiment import sampler
from acoustic_orchestrator.experiment.receiver_catalog import receiver_subject_id
from acoustic_orchestrator.config.models import CircularArrayOutputConfig
from acoustic_orchestrator.experiment.scene_builder import build_static_manifest
from acoustic_orchestrator.pipeline.matlab_runner import write_single_receiver_render_manifest
from acoustic_orchestrator.pipeline.output_paths import get_receiver_output_config
from test_generate_manifests import _build_workspace, _write_config


def test_configuration_rejects_disjoint_receiver_catalogs(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config = load_config(_write_config(workspace, "disjoint_receivers"))
    rear = config.receiver_outputs.bte_rear_hartf.ir_catalog_path
    (rear / "subject01.daff").rename(rear / "other_subject.daff")
    with pytest.raises(ValueError, match="0 sujetos comunes.*se requieren 1"):
        validate_config(config)


def test_configuration_preserves_individual_catalog_errors(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    config = load_config(_write_config(workspace, "short_receiver_catalog"))
    config.receiver_outputs.binaural_hrtf.num_hrtfs = 2
    with pytest.raises(ValueError) as error:
        validate_config(config)
    assert "menos que num_hrtfs=2" in str(error.value)
    assert "1 sujetos comunes disponibles, se requieren 2" in str(error.value)


def _multi_subject_config(tmp_path: Path):
    workspace = _build_workspace(tmp_path)
    config = load_config(_write_config(workspace, "paired_receivers"))
    for output in sampler._enabled_outputs(config).values():
        for subject in ("subject02", "subject03", "subject04"):
            (output.ir_catalog_path / f"{subject}.daff").touch()
    return config


@pytest.mark.parametrize("counts", [(1, 1, 1), (2, 2, 2), (2, 1, 1), (1, 3, 2)])
def test_receiver_outputs_share_subject_sequence(tmp_path: Path, counts) -> None:
    config = _multi_subject_config(tmp_path)
    outputs = sampler._enabled_outputs(config)
    for output, count in zip(outputs.values(), counts):
        output.num_hrtfs = count
    validate_config(config)
    selected = sampler._sample_hrtfs(config, random.Random(42))
    subjects = []
    for name, output in outputs.items():
        records = [item for item in selected if item["hrtf_id"].split("__")[0] == name]
        assert len(records) == output.num_hrtfs
        assert [item["hrtf_id"] for item in records] == (
            [name] if output.num_hrtfs == 1 else [f"{name}__{i}" for i in range(1, output.num_hrtfs + 1)]
        )
        ids = [receiver_subject_id(item["hrtf_path"]) for item in records]
        assert len(set(ids)) == len(ids)
        subjects.append(ids)
    longest = max(subjects, key=len)
    assert all(ids == longest[:len(ids)] for ids in subjects)


def test_pairing_ignores_disabled_hrtf_and_array(tmp_path: Path) -> None:
    config = _multi_subject_config(tmp_path)
    config.receiver_outputs.binaural_hrtf.enabled = False
    config.receiver_outputs.binaural_hrtf.ir_catalog_path = tmp_path / "missing"
    config.receiver_outputs.circular_array = CircularArrayOutputConfig(enabled=True, num_microphones=4, radius_m=0.1)
    selected = sampler._sample_hrtfs(config, random.Random(42))
    assert {item["hrtf_id"] for item in selected} == {"bte_front_hartf", "bte_rear_hartf"}
    assert len({receiver_subject_id(item["hrtf_path"]) for item in selected}) == 1


def test_pairing_is_independent_of_filesystem_order(tmp_path: Path, monkeypatch) -> None:
    config = _multi_subject_config(tmp_path)
    expected = sampler._sample_hrtfs(config, random.Random(42))
    glob = Path.glob
    monkeypatch.setattr(Path, "glob", lambda self, pattern: iter(reversed(list(glob(self, pattern)))))
    assert sampler._sample_hrtfs(config, random.Random(42)) == expected


def test_sampler_defensively_rejects_changed_catalog(tmp_path: Path) -> None:
    config = _multi_subject_config(tmp_path)
    validate_config(config)
    for path in config.receiver_outputs.bte_front_hartf.ir_catalog_path.glob("*.daff"):
        path.unlink()
    with pytest.raises(ValueError, match="0 sujetos comunes"):
        sampler._sample_hrtfs(config, random.Random(42))


@pytest.mark.parametrize("seed", [0, 1, 42, 123, 99999])
@pytest.mark.parametrize("count", [1, 2, 4])
def test_pairing_preserves_legacy_rng_advancement(tmp_path: Path, seed: int, count: int) -> None:
    config = _multi_subject_config(tmp_path)
    config.receiver_outputs.binaural_hrtf.num_hrtfs = count
    # Unequal populations exercise choice/sample rejection draws as well.
    (config.receiver_outputs.binaural_hrtf.ir_catalog_path / "exclusive.daff").touch()
    expected = random.Random(seed)
    actual = random.Random(seed)
    for output in sampler._enabled_outputs(config).values():
        matches = sorted(output.ir_catalog_path.glob(output.file_pattern))
        if output.num_hrtfs == 1:
            expected.choice(matches)
        else:
            expected.sample(matches, output.num_hrtfs)
    sampler._sample_hrtfs(config, actual)
    assert actual.getstate() == expected.getstate()


@pytest.mark.parametrize("count", [1, 2, 3])
def test_single_output_keeps_legacy_file_selection(tmp_path: Path, count: int) -> None:
    config = _multi_subject_config(tmp_path)
    config.receiver_outputs.bte_front_hartf = None
    config.receiver_outputs.bte_rear_hartf = None
    output = config.receiver_outputs.binaural_hrtf
    output.num_hrtfs = count
    for name in ("arbitrary", "_HRTF", "arbitrary_HRTF"):
        (output.ir_catalog_path / f"{name}.daff").touch()
    validate_config(config)  # Ambiguous/empty identities are allowed in the single-output legacy path.
    expected = random.Random(42)
    matches = sorted(output.ir_catalog_path.glob(output.file_pattern))
    files = [expected.choice(matches)] if count == 1 else expected.sample(matches, count)
    actual = random.Random(42)
    assert [item["hrtf_path"] for item in sampler._sample_hrtfs(config, actual)] == files
    assert actual.getstate() == expected.getstate()


def test_array_only_does_not_discover_cephalic_catalogs(tmp_path: Path, monkeypatch) -> None:
    config = _multi_subject_config(tmp_path)
    for name in tuple(sampler._enabled_outputs(config)):
        setattr(config.receiver_outputs, name, None)
    config.receiver_outputs.circular_array = CircularArrayOutputConfig(enabled=True, num_microphones=4, radius_m=0.1)

    def unexpected_discovery(*args):
        pytest.fail("Array-only must not discover receiver IRs")

    monkeypatch.setattr(sampler, "list_receiver_irs", unexpected_discovery)
    rng = random.Random(42)
    state = rng.getstate()
    assert sampler._sample_hrtfs(config, rng) == []
    assert rng.getstate() == state


@pytest.mark.parametrize("count", [1, 2])
def test_runtime_preserves_paired_ir_and_scene_contract(tmp_path: Path, count: int) -> None:
    config = _multi_subject_config(tmp_path)
    for output in sampler._enabled_outputs(config).values():
        output.num_hrtfs = count
    validate_config(config)
    scene = sampler.sample_static_scene(config, None, 0)
    manifest = build_static_manifest(config, scene, 0, tmp_path / "scene.json")
    by_ordinal = {}
    for record in manifest["receiver"]["hrtfs"]:
        output = get_receiver_output_config(config, record["hrtf_id"])
        path = write_single_receiver_render_manifest(manifest, record, tmp_path / "runtime", config.outputs, output)
        runtime = json.loads(path.read_text(encoding="utf-8"))
        assert runtime["receiver"]["hrtfs"] == [record]
        assert set(record) == {"hrtf_id", "hrtf_path"}
        assert runtime["schema_version"] == manifest["schema_version"] == "3.0"
        assert runtime["room"] == manifest["room"]
        assert runtime["sources"] == manifest["sources"]
        for key in ("receiver_id", "position_m", "orientation_deg"):
            assert runtime["receiver"][key] == manifest["receiver"][key]
        output_type = record["hrtf_id"]
        assert runtime["render"]["active_output_type"] == output_type
        assert Path(runtime["render"]["output_wav_path"]).name == f"{manifest['scene_id']}__{output_type}.wav"
        assert Path(runtime["render"]["output_metadata_path"]).name == f"{manifest['scene_id']}__{output_type}__render.json"
        assert Path(runtime["render"]["output_wav_path"]).parent.name == output.output_subdir
        ordinal = output_type.split("__")[-1] if count > 1 else "1"
        by_ordinal.setdefault(ordinal, []).append(receiver_subject_id(Path(record["hrtf_path"])))
    assert len(by_ordinal) == count
    assert all(len(subjects) == 3 and len(set(subjects)) == 1 for subjects in by_ordinal.values())
