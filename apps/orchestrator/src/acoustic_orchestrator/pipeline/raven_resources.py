"""Private RAVEN resources, described before any writes or process reservation."""

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from acoustic_orchestrator.pipeline.render_attempt import (
    RenderAttempt,
    read_json_object,
    sha256_file,
)

RESOURCE_VERSION = 1


@contextmanager
def standalone_writer(resources: "RavenResources", owner: str | None = None) -> Iterator[dict[str, bool]]:
    """An atomic resource owner shared by standalone and coordinated runners."""
    path = resources.directory / "writer.lock"
    token = (owner or uuid4().hex).encode("ascii")
    with path.open("xb") as stream:
        stream.write(token)
    state = {"release": True}
    try:
        yield state
    finally:
        if state["release"] and path.read_bytes() == token:
            path.unlink()


def archive_reconciled_writer(outcome: dict[str, Any]) -> None:
    """Called only after the chain verifies and archives a confirmed interruption."""
    if outcome.get("retry_classification") != "confirmed_interruption":
        return
    runtime = read_json_object(Path(outcome["manifest_snapshot_path"]))
    if runtime.get("render", {}).get("backend", "raven") != "raven":
        return
    resources = verify_private_runtime(runtime).resources
    lock = resources.directory / "writer.lock"
    if not lock.exists():
        return
    token = outcome["attempt_id"].encode("ascii")
    if lock.read_bytes() != token:
        raise ValueError(f"RAVEN writer ownership conflict: {lock}")
    archive = Path(outcome["manifest_snapshot_path"]).parent / "raven_writer.lock"
    if archive.exists():
        if archive.read_bytes() != token:
            raise ValueError(f"RAVEN writer archive conflict: {archive}")
    else:
        with archive.open("xb") as stream:
            stream.write(token)
    lock.unlink()


def _digest(payload: Any) -> str:
    def numbers(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [numbers(item) for item in value]
        if type(value) is float and value.is_integer():
            return int(value)
        return value

    return hashlib.sha256(json.dumps(numbers(payload), sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RavenResources:
    directory: Path
    realization_key: str
    project_name: str

    @property
    def rpf_path(self) -> Path:
        return self.directory / "base.rpf"

    @property
    def seed_path(self) -> Path:
        return self.directory / "RavenModels" / "seed.ac"

    @property
    def materials_dir(self) -> Path:
        return self.directory / "materials"

    @property
    def provenance_path(self) -> Path:
        return self.directory / "provenance.json"


def describe_resources(manifest: dict[str, Any], resources_root: Path, *,
                       original_rpf_sha256: str, original_model_sha256: str) -> RavenResources:
    """Pure description; neither a directory nor a timestamp is created here.

    The input is the logical runtime, before private paths are substituted.
    Process attempt UUIDs deliberately have no place in this API.
    """
    logical = deepcopy(manifest)
    for key in ("sampling", "reverberation_sampling", "reverberation_batch"):
        logical.pop(key, None)
    logical.pop("project_name", None)
    logical["base_rpf_file"] = Path(logical["base_rpf_file"]).resolve().as_posix()
    render = logical["render"]
    for key in ("output_wav_path", "output_metadata_path", "outputs", "receiver_variants"):
        render.pop(key, None)
    variant_id = f"{manifest['scene_id']}__{render['active_output_type']}"
    if re.fullmatch(r"[A-Za-z0-9_.-]+", variant_id) is None:
        raise ValueError(f"Unsafe RAVEN variant identity: {variant_id}")
    realization = _digest({"version": RESOURCE_VERSION, "runtime": logical,
                           "rpf_sha256": original_rpf_sha256, "model_sha256": original_model_sha256})[:24]
    directory = (resources_root / variant_id / realization).resolve()
    seed_path = directory / "RavenModels" / "seed.ac"
    # The installed toolbox searches a literal substring, not path components.
    if str(seed_path).count("RavenModels") != 1:
        raise ValueError(f"Ambiguous RavenModels prefix: {seed_path}")
    project = "rv_" + _digest({"directory": directory.as_posix()})[:32]
    return RavenResources(directory, realization, project)


@dataclass(frozen=True)
class RavenPreparation:
    resources: RavenResources
    original_rpf: Path
    original_model: Path
    rpf_bytes: bytes
    model_bytes: bytes
    provenance: dict[str, Any]


def _rpf_fields(raw: bytes, path: Path) -> dict[tuple[str, str], tuple[str, int, int]]:
    """Byte-preserving offsets; RAVEN's text RPFs are not MATLAB MAT files."""
    text = raw.decode("latin-1")
    section = ""
    sections: set[str] = set()
    fields: dict[tuple[str, str], tuple[str, int, int]] = {}
    offset = 0
    for line in text.splitlines(keepends=True):
        header = re.fullmatch(r"\s*\[([^\]\r\n]+)\]\s*", line)
        if header:
            section = header[1].lower()
            if section in sections:
                raise ValueError(f"Duplicate RPF section {section}: {path}")
            sections.add(section)
        elif not line.lstrip().startswith(("#", ";")):
            entry = re.match(r"[ \t]*([^=\r\n]+?)[ \t]*=[ \t]*([^\r\n]*?)[ \t]*(?:\r?\n)?$", line)
            if entry:
                key = (section, entry[1].lower())
                if key in fields:
                    raise ValueError(f"Duplicate RPF key {key}: {path}")
                fields[key] = (entry[2], offset + entry.start(2), offset + entry.end(2))
        offset += len(line)
    return fields


def derive_resources(manifest: dict[str, Any], resources_root: Path, *,
                     raven_base: Path | None = None) -> RavenPreparation:
    """Read original dependencies and describe exact derived bytes, without writes.

    The toolbox resolves '..' against the parent of its executable directory.
    A conventional RavenInput ancestor identifies that base unambiguously;
    callers with a nonstandard layout must supply an explicitly resolved base.
    """
    original = Path(manifest["base_rpf_file"]).resolve()
    raw = original.read_bytes()
    fields = _rpf_fields(raw, original)
    required = [("rooms", "model"), ("global", "projectpath_materialdb")]
    for key in required:
        if key not in fields:
            raise ValueError(f"Missing RPF key {key}: {original}")
    model_value = fields[required[0]][0]
    if not model_value or "," in model_value:
        raise ValueError(f"Expected one unambiguous RPF model: {original}")
    model = Path(model_value.replace("\\", "/"))
    if not model.is_absolute():
        if not model_value.startswith(".."):
            raise ValueError(f"Unsupported relative RAVEN model {model_value}: {original}")
        if raven_base is None:
            input_dir = next((p for p in original.parents if p.name.lower() == "raveninput"), None)
            if input_dir is None:
                raise ValueError(f"Cannot resolve RAVEN installation for relative model: {original}")
            raven_base = input_dir.parent
        model = raven_base / model_value[2:].lstrip("/\\").replace("\\", "/")
    model = model.resolve()
    model_bytes = model.read_bytes()
    raw_hash = hashlib.sha256(raw).hexdigest()
    model_hash = hashlib.sha256(model_bytes).hexdigest()
    resources = describe_resources(manifest, resources_root, original_rpf_sha256=raw_hash,
                                   original_model_sha256=model_hash)
    replacements = [(fields[required[0]], resources.seed_path.as_posix()),
                    (fields[required[1]], resources.materials_dir.as_posix())]
    derived = raw
    for (_, start, end), value in sorted(replacements, key=lambda item: item[0][1], reverse=True):
        try:
            derived = derived[:start] + value.encode("latin-1") + derived[end:]
        except UnicodeEncodeError as exc:
            raise ValueError(f"RPF path cannot be represented in its encoding: {value}") from exc
    provenance = {
        "schema_version": RESOURCE_VERSION,
        "original_rpf": original.as_posix(), "original_rpf_sha256": raw_hash,
        "original_model": model.as_posix(), "original_model_sha256": model_hash,
        "rpf_path": resources.rpf_path.as_posix(), "rpf_sha256": hashlib.sha256(derived).hexdigest(),
        "seed_path": resources.seed_path.as_posix(), "seed_sha256": model_hash,
        "materials_dir": resources.materials_dir.as_posix(),
        "realization_key": resources.realization_key, "project_name": resources.project_name,
        "scene_id": manifest["scene_id"], "output_type": manifest["render"]["active_output_type"],
        "path_resolution": "raven_executable_base_v1",
    }
    return RavenPreparation(resources, original, model, derived, model_bytes, provenance)


def verify_resources(preparation: RavenPreparation) -> None:
    resources = preparation.resources
    if read_json_object(resources.provenance_path) != preparation.provenance:
        raise ValueError(f"RAVEN provenance conflict: {resources.provenance_path}")
    for path, expected in ((resources.rpf_path, preparation.provenance["rpf_sha256"]),
                           (resources.seed_path, preparation.provenance["seed_sha256"])):
        if path.is_symlink() or sha256_file(path) != expected:
            raise ValueError(f"RAVEN resource conflict: {path}")
    if not resources.materials_dir.is_dir() or resources.materials_dir.is_symlink():
        raise ValueError(f"RAVEN material directory conflict: {resources.materials_dir}")


def materialize_resources(preparation: RavenPreparation) -> None:
    """Exclusive publication; an interrupted preparation is a conflict, not repair."""
    resources = preparation.resources
    if resources.directory.exists():
        verify_resources(preparation)
        return
    resources.directory.mkdir(parents=True, exist_ok=False)
    resources.seed_path.parent.mkdir()
    resources.materials_dir.mkdir()
    for path, payload in ((resources.seed_path, preparation.model_bytes),
                           (resources.rpf_path, preparation.rpf_bytes)):
        with path.open("xb") as stream:
            stream.write(payload)
    with resources.provenance_path.open("x", encoding="utf-8") as stream:
        json.dump(preparation.provenance, stream, indent=2, allow_nan=False)


def isolated_runtime(logical: dict[str, Any], resources_root: Path, *, materialize: bool = False) -> dict[str, Any]:
    preparation = derive_resources(logical, resources_root)
    if preparation.resources.directory.exists():
        verify_resources(preparation)
    if materialize:
        materialize_resources(preparation)
    runtime = deepcopy(logical)
    runtime["base_rpf_file"] = preparation.resources.rpf_path.as_posix()
    runtime["project_name"] = preparation.resources.project_name
    return runtime


def verify_private_runtime(runtime: dict[str, Any]) -> RavenPreparation:
    """Verify a runtime's private reference without changing its captured bytes."""
    rpf = Path(runtime.get("base_rpf_file") or "")
    provenance = read_json_object(rpf.parent / "provenance.json")
    logical = deepcopy(runtime)
    logical["base_rpf_file"] = provenance["original_rpf"]
    preparation = derive_resources(logical, rpf.parent.parent.parent)
    if rpf.resolve() != preparation.resources.rpf_path or runtime["project_name"] != preparation.resources.project_name:
        raise ValueError(f"RAVEN private runtime identity conflict: {rpf}")
    verify_resources(preparation)
    return preparation


def is_private_runtime(runtime: dict[str, Any]) -> bool:
    rpf = Path(runtime.get("base_rpf_file") or "")
    return rpf.parent.parent.parent.name == "raven_resources" or (rpf.parent / "provenance.json").exists()


def verify_resource_inventory(outcome: dict[str, Any], runtime: dict[str, Any]) -> None:
    preparation = verify_private_runtime(runtime)
    inventory_path = Path(outcome["raven_resources_path"])
    if sha256_file(inventory_path) != outcome["raven_resources_sha256"]:
        raise ValueError(f"RAVEN inventory conflict: {inventory_path}")
    inventory = read_json_object(inventory_path)
    if (inventory.get("schema_version") != RESOURCE_VERSION
            or inventory.get("attempt_id") != outcome["attempt_id"]
            or inventory.get("manifest_sha256") != outcome["manifest_sha256"]
            or inventory.get("provenance_path") != preparation.resources.provenance_path.as_posix()
            or inventory.get("provenance_sha256") != sha256_file(preparation.resources.provenance_path)):
        raise ValueError(f"RAVEN inventory identity conflict: {inventory_path}")
    seen: set[Path] = set()
    for entry in inventory["generated_files"]:
        path = Path(entry["path"])
        if (path in seen or path.parent not in {preparation.resources.seed_path.parent, preparation.resources.materials_dir}
                or path.is_symlink() or path.stat().st_size != entry["size_bytes"]
                or sha256_file(path) != entry["sha256"]):
            raise ValueError(f"RAVEN generated resource conflict: {path}")
        seen.add(path)


def persist_resource_inventory(attempt: RenderAttempt) -> None:
    """Dispatcher-owned evidence after observed termination, never diagnostics."""
    runtime = read_json_object(attempt.snapshot_path)
    if runtime.get("render", {}).get("backend", "raven") != "raven":
        return
    preparation = verify_private_runtime(runtime)
    resources = preparation.resources
    entries = []
    for directory in (resources.seed_path.parent, resources.materials_dir):
        for path in sorted(directory.iterdir()):
            if path == resources.seed_path:
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Unexpected RAVEN resource: {path}")
            entries.append({"path": path.as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    record = {"schema_version": RESOURCE_VERSION, "attempt_id": attempt.attempt_id,
              "manifest_sha256": attempt.manifest_sha256,
              "provenance_path": resources.provenance_path.as_posix(),
              "provenance_sha256": sha256_file(resources.provenance_path), "generated_files": entries}
    path = attempt.directory / "raven_resources.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, allow_nan=False)
    attempt.record.update(raven_resources_path=path.as_posix(), raven_resources_sha256=sha256_file(path),
                          raven_isolation="verified_private")
