from copy import deepcopy
from pathlib import Path

import pytest
from acoustic_orchestrator.pipeline.raven_resources import (
    derive_resources, describe_resources,
    materialize_resources, verify_resources,
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
    materialize_resources(preparation)
    assert preparation.resources.seed_path.read_bytes() == model.read_bytes()
    derived = preparation.resources.rpf_path.read_bytes()
    restored = derived.replace(preparation.resources.seed_path.as_posix().encode("latin-1"), value.encode("latin-1"))
    restored = restored.replace(preparation.resources.materials_dir.as_posix().encode("latin-1"), b"..\\materials")
    assert restored == raw
    assert source.read_bytes() == raw


def preparation_fixture(tmp_path: Path):
    model = tmp_path / "model.ac"
    model.write_bytes(b"AC3Db\n")
    rpf = tmp_path / "base.rpf"
    rpf.write_bytes(f"[Global]\r\nProjectPath_MaterialDB=unused\r\n[Rooms]\r\nModel={model}\r\n".encode())
    manifest = logical_runtime() | {"base_rpf_file": str(rpf)}
    return derive_resources(manifest, tmp_path / "resources")


@pytest.mark.parametrize("target", ["base", "seed", "provenance", "missing"])
def test_protected_resources_are_not_repaired(tmp_path: Path, target: str) -> None:
    preparation = preparation_fixture(tmp_path)
    materialize_resources(preparation)
    resources = preparation.resources
    path = {"base": resources.rpf_path, "seed": resources.seed_path,
            "provenance": resources.provenance_path, "missing": resources.seed_path}[target]
    if target == "missing":
        path.unlink()
    else:
        path.write_bytes(b"tampered")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in resources.directory.rglob("*") if p.is_file()}
    with pytest.raises((ValueError, FileNotFoundError)):
        materialize_resources(preparation)
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in resources.directory.rglob("*") if p.is_file()}


def test_verification_is_read_only(tmp_path: Path) -> None:
    preparation = preparation_fixture(tmp_path)
    materialize_resources(preparation)
    files = [p for p in preparation.resources.directory.rglob("*") if p.is_file()]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in files]
    verify_resources(preparation)
    materialize_resources(preparation)
    assert before == [(p.read_bytes(), p.stat().st_mtime_ns) for p in files]


@pytest.mark.parametrize("corruption", ["duplicate_key", "duplicate_section", "missing_model", "unresolvable"])
def test_ambiguous_or_unreadable_original_fails_without_writes(tmp_path: Path, corruption: str) -> None:
    preparation = preparation_fixture(tmp_path)
    rpf = preparation.original_rpf
    if corruption == "duplicate_key":
        rpf.write_bytes(rpf.read_bytes() + b"Model=other.ac\n")
    elif corruption == "duplicate_section":
        rpf.write_bytes(rpf.read_bytes() + b"[Rooms]\n")
    elif corruption == "missing_model":
        preparation.original_model.unlink()
    else:
        rpf.write_bytes(b"[Global]\nProjectPath_MaterialDB=unused\n[Rooms]\nModel=..\\RavenModels\\seed.ac\n")
    with pytest.raises((ValueError, FileNotFoundError)):
        derive_resources(logical_runtime() | {"base_rpf_file": str(rpf)}, tmp_path / "resources")
    assert not (tmp_path / "resources").exists()


def test_verified_reconciliation_archives_only_its_own_writer_lock(tmp_path):
    import json

    from acoustic_orchestrator.pipeline.raven_resources import (
        archive_reconciled_writer,
        isolated_runtime,
        standalone_writer,
    )
    from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt

    preparation = preparation_fixture(tmp_path)
    logical = logical_runtime() | {"base_rpf_file": preparation.original_rpf.as_posix()}
    logical["render"].update(output_wav_path=(tmp_path / "out.wav").as_posix(),
                             output_metadata_path=(tmp_path / "out.json").as_posix())
    runtime = isolated_runtime(logical, tmp_path / "raven_resources", materialize=True)
    path = tmp_path / "scene_static_0001__binaural_hrtf.json"
    path.write_text(json.dumps(runtime))
    attempt = RenderAttempt(path)
    resources = derive_resources(logical, tmp_path / "raven_resources").resources
    with standalone_writer(resources, attempt.attempt_id) as lease:
        lease["release"] = False
    lock = resources.directory / "writer.lock"
    archive_reconciled_writer(attempt.record)
    assert lock.exists()  # Unconfirmed termination must retain the owner.
    attempt.record["retry_classification"] = "confirmed_interruption"
    original = lock.read_bytes()
    lock.write_bytes(b"foreign-owner")
    with pytest.raises(ValueError, match="ownership conflict"):
        archive_reconciled_writer(attempt.record)
    assert lock.read_bytes() == b"foreign-owner"
    lock.write_bytes(original)
    archive_reconciled_writer(attempt.record)
    assert not lock.exists()
    assert (attempt.directory / "raven_writer.lock").read_bytes() == original
