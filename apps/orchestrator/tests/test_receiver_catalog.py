from pathlib import Path

import pytest

from acoustic_orchestrator.experiment.receiver_catalog import (
    index_receiver_subjects,
    list_receiver_irs,
    receiver_subject_id,
    paired_receiver_catalogs,
)
from acoustic_orchestrator.config.models import ReceiverOutputConfig
from acoustic_orchestrator.config.validator import _validate_receiver_subject_pairing


@pytest.mark.parametrize("filename,expected", [
    ("id10_HRTF.daff", "id10"),
    ("id10_HARTF_front.daff", "id10"),
    ("id10_HARTF_rear.daff", "id10"),
    ("subject01.daff", "subject01"),
    ("id01_HRTF.daff", "id01"),
    ("id1_HRTF.daff", "id1"),
    ("ID01_HRTF.daff", "ID01"),
    ("id01_HRTF_extra.daff", "id01_HRTF_extra"),
])
def test_receiver_subject_id(filename: str, expected: str) -> None:
    assert receiver_subject_id(Path(filename)) == expected


def test_catalog_discovery_and_index_are_ordered(tmp_path: Path) -> None:
    for name in ("id02_HRTF.daff", "id01_HRTF.daff", "notes.json"):
        (tmp_path / name).touch()
    files = list_receiver_irs(tmp_path, "*.daff")
    assert [p.name for p in files] == ["id01_HRTF.daff", "id02_HRTF.daff"]
    assert list(index_receiver_subjects(list(reversed(files)))) == ["id01", "id02"]


def test_duplicate_subject_is_rejected() -> None:
    with pytest.raises(ValueError, match="ambigua.*id10.*id10.daff.*id10_HRTF.daff"):
        index_receiver_subjects([Path("id10_HRTF.daff"), Path("id10.daff")])


def test_empty_subject_is_rejected() -> None:
    with pytest.raises(ValueError, match="vacía.*_HRTF.daff"):
        index_receiver_subjects([Path("_HRTF.daff")])


def _catalogs(tmp_path: Path, subjects: tuple[list[str], ...]) -> dict:
    outputs = {}
    for name, ids in zip(("binaural_hrtf", "bte_front_hartf", "bte_rear_hartf"), subjects):
        directory = tmp_path / name
        directory.mkdir()
        for subject in ids:
            (directory / f"{subject}.daff").touch()
        outputs[name] = ReceiverOutputConfig(
            enabled=True, ir_catalog_path=directory, file_pattern="*.daff",
            num_hrtfs=1, output_subdir=name, num_channels=2,
        )
    return outputs


def test_cross_validation_accepts_partial_overlap(tmp_path: Path) -> None:
    outputs = _catalogs(tmp_path, (["a", "b", "c"], ["b", "c", "d"], ["b", "c", "e"]))
    outputs["binaural_hrtf"].num_hrtfs = 2
    errors = []
    _validate_receiver_subject_pairing(errors, outputs)
    assert errors == []
    _, common = paired_receiver_catalogs(
        {name: (output.ir_catalog_path, "*.daff") for name, output in outputs.items()}, 2,
    )
    assert common == ["b", "c"]


@pytest.mark.parametrize("subjects,count,available", [
    ((["a"], ["b"]), 1, 0),
    ((["a", "b"], ["a", "c"]), 2, 1),
])
def test_cross_validation_reports_common_capacity(tmp_path: Path, subjects, count, available) -> None:
    outputs = _catalogs(tmp_path, subjects)
    outputs["binaural_hrtf"].num_hrtfs = count
    errors = []
    _validate_receiver_subject_pairing(errors, outputs)
    assert len(errors) == 1
    assert f"{available} sujetos comunes" in errors[0]
    assert f"se requieren {count}" in errors[0]
    for name, output in outputs.items():
        assert name in errors[0]
        assert str(output.ir_catalog_path) in errors[0]


def test_cross_validation_labels_ambiguous_catalog(tmp_path: Path) -> None:
    outputs = _catalogs(tmp_path, (["id10", "id10_HRTF"], ["id10"]))
    errors = []
    _validate_receiver_subject_pairing(errors, outputs)
    assert "receiver_outputs.binaural_hrtf" in errors[0]
    assert "ambigua" in errors[0]


def test_single_catalog_does_not_validate_subjects(tmp_path: Path) -> None:
    outputs = _catalogs(tmp_path, (["id10", "id10_HRTF", "_HRTF"],))
    errors = []
    _validate_receiver_subject_pairing(errors, outputs)
    assert errors == []
