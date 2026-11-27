import random
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from test_generate_manifests import _build_workspace, _make_receiver_orientation_compatible, _write_config

import acoustic_orchestrator.experiment.sampler as sampler
from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.models import CircularArrayOutputConfig, ReceiverOutputsConfig
from acoustic_orchestrator.config.validator import validate_config
from acoustic_orchestrator.experiment.scene_builder import build_static_manifest
from acoustic_orchestrator.pipeline.static_plan import normalized_static_config
from acoustic_orchestrator.receiver_ir_subjects import receiver_ir_subject_id

CATALOG_KINDS = {
    "binaural_hrtf": ("hrtf", "HRTF"),
    "bte_rear_hartf": ("hartf/rear", "HARTF_rear"),
    "bte_front_hartf": ("hartf/front", "HARTF_front"),
}


def _set_catalogs(workspace: Path, subjects: dict[str, list[str]]) -> None:
    for output_name, (subdir, kind) in CATALOG_KINDS.items():
        directory = workspace / "assets" / subdir
        for existing in directory.glob("*.daff"):
            existing.unlink()
        for subject in subjects.get(output_name, []):
            (directory / f"{subject}_{kind}.daff").write_text("daff", encoding="utf-8")


def _shared_config(tmp_path: Path, subjects: dict[str, list[str]] | None = None, *, pairing: str = "shared"):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "subject_pairing")
    _make_receiver_orientation_compatible(config_path)
    default_subjects = [f"id{index:02d}" for index in range(1, 6)]
    _set_catalogs(workspace, subjects or {name: default_subjects for name in CATALOG_KINDS})
    config = load_config(config_path)
    config.receiver_outputs.subject_pairing = pairing
    return workspace, config


# 1.1 setting ---------------------------------------------------------------

def test_subject_pairing_defaults_to_independent() -> None:
    assert ReceiverOutputsConfig().subject_pairing == "independent"


def test_subject_pairing_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError) as error:
        ReceiverOutputsConfig.model_validate({"subject_pairing": "paired"})
    assert "subject_pairing" in str(error.value)


# 1.2 subject id helper ------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("id22_HARTF_front.daff", "id22"),
        ("id22_HRTF.daff", "id22"),
        ("subject01.daff", None),
        ("_HRTF.daff", None),
    ],
)
def test_receiver_ir_subject_id(name: str, expected: str | None) -> None:
    assert receiver_ir_subject_id(Path("catalog") / name) == expected


# 1.3 shared mode validation -------------------------------------------------

def test_shared_mode_accepts_aligned_catalogs(tmp_path: Path) -> None:
    _, config = _shared_config(tmp_path)
    validate_config(config)


def test_shared_mode_rejects_multiple_irs_per_output(tmp_path: Path) -> None:
    _, config = _shared_config(tmp_path)
    config.receiver_outputs.bte_front_hartf.num_hrtfs = 2
    with pytest.raises(ValueError) as error:
        validate_config(config)
    assert "receiver_outputs.bte_front_hartf.num_hrtfs" in str(error.value)


def test_shared_mode_rejects_file_without_subject_id(tmp_path: Path) -> None:
    workspace, config = _shared_config(tmp_path)
    (workspace / "assets" / "hartf" / "rear" / "rear.daff").write_text("daff", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        validate_config(config)
    assert "receiver_outputs.bte_rear_hartf" in str(error.value)
    assert "rear.daff" in str(error.value)


def test_shared_mode_rejects_duplicate_subject_in_one_catalog(tmp_path: Path) -> None:
    workspace, config = _shared_config(tmp_path)
    (workspace / "assets" / "hrtf" / "id05_HRTF_v2.daff").write_text("daff", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        validate_config(config)
    assert "receiver_outputs.binaural_hrtf" in str(error.value)
    assert "id05" in str(error.value)


def test_shared_mode_rejects_disjoint_catalogs(tmp_path: Path) -> None:
    _, config = _shared_config(
        tmp_path,
        {"binaural_hrtf": ["id01"], "bte_rear_hartf": ["id02"], "bte_front_hartf": ["id03"]},
    )
    with pytest.raises(ValueError) as error:
        validate_config(config)
    assert "no comparten ningún sujeto" in str(error.value)


def test_independent_mode_ignores_unprefixed_catalog_files(tmp_path: Path) -> None:
    workspace, config = _shared_config(tmp_path, pairing="independent")
    (workspace / "assets" / "hartf" / "rear" / "rear.daff").write_text("daff", encoding="utf-8")
    validate_config(config)


# 1.4 plan identity ------------------------------------------------------------

def test_plan_identity_omits_independent_subject_pairing(tmp_path: Path) -> None:
    _, config = _shared_config(tmp_path, pairing="independent")
    assert "subject_pairing" not in normalized_static_config(config)["receiver_outputs"]
    config.receiver_outputs.subject_pairing = "shared"
    assert normalized_static_config(config)["receiver_outputs"]["subject_pairing"] == "shared"


# 2.x sampling -----------------------------------------------------------------

def _scene_subjects(manifest_or_scene: dict) -> set[str | None]:
    return {receiver_ir_subject_id(Path(item["hrtf_path"])) for item in manifest_or_scene["receiver"]["hrtfs"]}


def _namespace_config(root: Path, subjects: dict[str, list[str]], pairing: str) -> SimpleNamespace:
    outputs = {}
    for output_name, (_, kind) in CATALOG_KINDS.items():
        directory = root / output_name
        directory.mkdir()
        for subject in subjects[output_name]:
            (directory / f"{subject}_{kind}.daff").write_text("daff", encoding="utf-8")
        outputs[output_name] = SimpleNamespace(
            enabled=True, ir_catalog_path=directory, file_pattern="*.daff", num_hrtfs=1
        )
    return SimpleNamespace(receiver_outputs=SimpleNamespace(subject_pairing=pairing, **outputs))


def test_shared_mode_uses_one_subject_for_all_outputs(tmp_path: Path) -> None:
    subjects = [f"id{index:02d}" for index in range(1, 31)]
    config = _namespace_config(tmp_path, {name: subjects for name in CATALOG_KINDS}, "shared")
    rng = random.Random(102)
    chosen = []
    for _ in range(200):
        hrtfs = sampler._sample_hrtfs(config, rng)
        assert [item["hrtf_id"] for item in hrtfs] == ["binaural_hrtf", "bte_rear_hartf", "bte_front_hartf"]
        ids = {receiver_ir_subject_id(Path(item["hrtf_path"])) for item in hrtfs}
        assert len(ids) == 1
        assert {item["subject_id"] for item in hrtfs} == ids
        chosen.append(ids.pop())
    assert len(set(chosen)) > 1


def test_shared_mode_never_chooses_subject_missing_from_a_catalog(tmp_path: Path) -> None:
    full = [f"id{index:02d}" for index in range(1, 31)]
    config = _namespace_config(
        tmp_path,
        {"binaural_hrtf": full, "bte_front_hartf": full, "bte_rear_hartf": full[:-1]},
        "shared",
    )
    rng = random.Random(5)
    chosen = {sampler._sample_hrtfs(config, rng)[0]["subject_id"] for _ in range(500)}
    assert "id30" not in chosen
    assert chosen <= set(full[:-1])


def test_independent_mode_selection_matches_previous_implementation(tmp_path: Path) -> None:
    subjects = [f"id{index:02d}" for index in range(1, 6)]
    config = _namespace_config(tmp_path, {name: subjects for name in CATALOG_KINDS}, "independent")
    rng = random.Random(7)
    observed = [
        [(item["hrtf_id"], Path(item["hrtf_path"]).name, sorted(item)) for item in sampler._sample_hrtfs(config, rng)]
        for _ in range(4)
    ]
    keys = ["hrtf_id", "hrtf_path"]
    # Captured from the implementation before subject pairing existed (same seed and catalogs).
    assert observed == [
        [("binaural_hrtf", "id03_HRTF.daff", keys), ("bte_rear_hartf", "id02_HARTF_rear.daff", keys), ("bte_front_hartf", "id04_HARTF_front.daff", keys)],
        [("binaural_hrtf", "id01_HRTF.daff", keys), ("bte_rear_hartf", "id01_HARTF_rear.daff", keys), ("bte_front_hartf", "id05_HARTF_front.daff", keys)],
        [("binaural_hrtf", "id01_HRTF.daff", keys), ("bte_rear_hartf", "id03_HARTF_rear.daff", keys), ("bte_front_hartf", "id05_HARTF_front.daff", keys)],
        [("binaural_hrtf", "id01_HRTF.daff", keys), ("bte_rear_hartf", "id05_HARTF_rear.daff", keys), ("bte_front_hartf", "id02_HARTF_front.daff", keys)],
    ]
    assert rng.random() == 0.03749565844198488


def test_shared_mode_is_deterministic_per_seed(tmp_path: Path) -> None:
    _, config = _shared_config(tmp_path)
    validate_config(config)
    first = [_scene_subjects(sampler.sample_static_scene(config, None, scene_index=index)) for index in range(5)]
    second = [_scene_subjects(sampler.sample_static_scene(config, None, scene_index=index)) for index in range(5)]
    assert first == second
    assert all(len(subjects) == 1 for subjects in first)


# 2.4 manifest provenance ----------------------------------------------------------

@pytest.mark.parametrize("pairing", ["shared", "independent"])
def test_manifest_records_subject_id_only_in_shared_mode(tmp_path: Path, pairing: str) -> None:
    workspace, config = _shared_config(tmp_path, pairing=pairing)
    validate_config(config)
    scene = sampler.sample_static_scene(config, None, scene_index=0)
    manifest = build_static_manifest(config, scene, 0, workspace / "manifest.json")
    entries = manifest["receiver"]["hrtfs"]
    assert len(entries) == 3
    if pairing == "shared":
        (subject,) = _scene_subjects(manifest)
        assert all(sorted(entry) == ["hrtf_id", "hrtf_path", "subject_id"] for entry in entries)
        assert {entry["subject_id"] for entry in entries} == {subject}
    else:
        assert all(sorted(entry) == ["hrtf_id", "hrtf_path"] for entry in entries)


def test_render_validation_accepts_shared_manifest_entries(tmp_path: Path) -> None:
    from acoustic_orchestrator.pipeline.render_validation import RenderValidation, validate_receiver_and_seed

    workspace, config = _shared_config(tmp_path)
    validate_config(config)
    scene = sampler.sample_static_scene(config, None, scene_index=0)
    manifest = build_static_manifest(config, scene, 0, workspace / "manifest.json")
    entry = next(item for item in manifest["receiver"]["hrtfs"] if item["hrtf_id"] == "bte_front_hartf")
    manifest["render"]["active_output_type"] = "bte_front_hartf"
    result = RenderValidation()
    result.metadata = {"hrtf_id": "bte_front_hartf", "hrtf_path": entry["hrtf_path"]}
    validate_receiver_and_seed(result, manifest)
    assert not [issue for issue in result.invalid if issue.startswith(("runtime_receiver", "render_metadata_hrtf"))]


# 2.5 circular array -------------------------------------------------------------------

def test_shared_mode_leaves_circular_array_descriptor_unchanged(tmp_path: Path) -> None:
    _, config = _shared_config(tmp_path)
    config.receiver_outputs.circular_array = CircularArrayOutputConfig(enabled=True, num_microphones=8, radius_m=0.05)
    validate_config(config)
    scene = sampler.sample_static_scene(config, None, scene_index=0)
    receiver = scene["receiver"]
    expected = sampler.build_circular_array_descriptor(
        receiver["position_m"], receiver["orientation_deg"], config.receiver_outputs.circular_array
    )
    assert receiver["circular_array"] == expected
    assert [item["hrtf_id"] for item in receiver["hrtfs"]] == ["binaural_hrtf", "bte_rear_hartf", "bte_front_hartf"]
    assert len(_scene_subjects(scene)) == 1
