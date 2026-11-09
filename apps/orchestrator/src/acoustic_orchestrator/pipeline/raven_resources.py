"""Deterministic, read-only description of private RAVEN resources."""

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RESOURCE_VERSION = 1


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
