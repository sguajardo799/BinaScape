from copy import deepcopy
from pathlib import Path

import pytest
from acoustic_orchestrator.pipeline.raven_resources import (
    derive_resources, describe_resources,
)


def logical_runtime() -> dict:
    return {"scene_id": "scene_static_0001", "project_name": "example", "base_rpf_file": "base.rpf",
            "room": {"acoustic_surfaces": {"wall_001": {"effective_absorption": [0.2] * 31}}},
            "receiver": {"position_m": [1, 1, 1], "hrtfs": [{"hrtf_id": "binaural_hrtf"}]},
            "sources": [], "render": {"active_output_type": "binaural_hrtf", "seed": 42, "sample_rate_hz": 44100,
                                       "output_wav_path": "out.wav", "output_metadata_path": "out.json"}}


def describe(manifest: dict, root: Path):
    return describe_resources(manifest, root, original_rpf_sha256="rpf", original_model_sha256="model")


def test_description_is_pure_and_stable(tmp_path: Path) -> None:
    manifest = logical_runtime()
    before = deepcopy(manifest)
    resources = describe(manifest, tmp_path / "run" / "metadata" / "raven_resources")
    manifest["reverberation_batch"] = {"observed": 30}
    manifest["sampling"] = {"scene_attempt": 8}
    assert resources == describe(manifest, tmp_path / "run" / "metadata" / "raven_resources")
    assert not resources.directory.exists()
    assert len(resources.project_name) < 50
    manifest.pop("sampling")
    manifest.pop("reverberation_batch")
    assert manifest == before


def test_runs_variants_realizations_and_input_bytes_are_distinct(tmp_path: Path) -> None:
    original = logical_runtime()
    first = describe(original, tmp_path / "run1")
    other = deepcopy(original)
    other["render"]["active_output_type"] = "bte_front_hartf"
    changed = deepcopy(original)
    changed["room"]["acoustic_surfaces"]["wall_001"]["effective_absorption"][0] = 0.9
    alternatives = [describe(original, tmp_path / "run2"), describe(other, tmp_path / "run1"),
                    describe(changed, tmp_path / "run1"),
                    describe_resources(original, tmp_path / "run1", original_rpf_sha256="new", original_model_sha256="model")]
    assert len({r.directory for r in [first, *alternatives]}) == 5
    assert len({r.project_name for r in [first, *alternatives]}) == 5


def test_ambiguous_model_prefix_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Ambiguous RavenModels"):
        describe(logical_runtime(), tmp_path / "RavenModels_backup")


@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_minimal_rpf_derivation_preserves_unrelated_bytes(tmp_path: Path, relative: bool, newline: str) -> None:
    base = tmp_path / "Installation with spaces"
    models = base / "RavenModels"
    models.mkdir(parents=True)
    model = models / "model.ac"
    model.write_bytes(b"AC3Db\nseed geometry")
    source = base / "RavenInput" / "Classroom" / "base.rpf"
    source.parent.mkdir(parents=True)
    value = r"..\RavenModels\model.ac" if relative else str(model)
    raw = newline.join(["# ac\u00fastica", "[Global]", "ProjectPath_MaterialDB = ..\\materials", "[Rooms]",
                        f"Model = {value}", "Volume = 123.456", ""]).encode("latin-1")
    source.write_bytes(raw)
    manifest = logical_runtime() | {"base_rpf_file": str(source)}
    preparation = derive_resources(manifest, tmp_path / "private resources")
    assert not preparation.resources.directory.exists()
    assert preparation.model_bytes == model.read_bytes()
    derived = preparation.rpf_bytes
    restored = derived.replace(preparation.resources.seed_path.as_posix().encode("latin-1"), value.encode("latin-1"))
    restored = restored.replace(preparation.resources.materials_dir.as_posix().encode("latin-1"), b"..\\materials")
    assert restored == raw
    assert source.read_bytes() == raw
