import json
from pathlib import Path


def write_manifest(scene_manifest: dict, manifest_path: Path, overwrite: bool = False) -> Path:
    if manifest_path.exists() and not overwrite:
        raise FileExistsError(f"El manifiesto ya existe y overwrite_existing=false: {manifest_path}")

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(scene_manifest, indent=2), encoding="utf-8")
    return manifest_path
