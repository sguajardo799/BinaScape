import json
import re
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import TypedDict

from acoustic_orchestrator.config.models import OutputsConfig, ReceiverOutputConfig
from acoustic_orchestrator.pipeline.output_paths import resolve_output_paths


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MATLAB_APP_DIR = (PROJECT_ROOT.parent / "matlab").resolve()
MATLAB_ENTRYPOINT = MATLAB_APP_DIR / "run_raven_static_render.m"


class RenderVariantPaths(TypedDict):
    variant_id: str
    scene_id: str
    output_type: str
    output_subdir: str
    runtime_manifest_path: str
    rendered_wav_path: str
    render_metadata_path: str
    receiver_ir_path: str


def render_manifest_with_matlab(manifest_path: Path, matlab_executable: str = "matlab") -> None:
    if not MATLAB_ENTRYPOINT.is_file():
        raise FileNotFoundError(f"No se encontró el entrypoint de MATLAB: {MATLAB_ENTRYPOINT}")

    manifest_path = manifest_path.resolve()
    batch_command = (
        f"addpath(genpath('{_escape_matlab_path(MATLAB_APP_DIR)}')); "
        f"run_raven_static_render('{_escape_matlab_path(manifest_path)}');"
    )
    subprocess.run([matlab_executable, "-batch", batch_command], check=True)


def write_single_hrtf_render_manifest(
    scene_manifest: dict,
    hrtf: dict,
    output_dir: Path,
    outputs: OutputsConfig,
    receiver_output: ReceiverOutputConfig,
    source_path_rewrites: dict[str, str] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    variant_paths = build_render_variant_paths(
        scene_manifest=scene_manifest,
        hrtf=hrtf,
        runtime_manifest_dir=output_dir,
        outputs=outputs,
        receiver_output=receiver_output,
    )
    variant_manifest = deepcopy(scene_manifest)
    variant_manifest["project_name"] = build_raven_project_name(
        scene_manifest["project_name"],
        variant_paths["variant_id"],
    )
    _flip_manifest_z_axis_for_raven(variant_manifest)
    variant_manifest["receiver"]["hrtfs"] = [deepcopy(hrtf)]
    variant_manifest["render"]["output_wav_path"] = variant_paths["rendered_wav_path"]
    variant_manifest["render"]["output_metadata_path"] = variant_paths["render_metadata_path"]

    for source in variant_manifest["sources"]:
        rewritten_path = (source_path_rewrites or {}).get(source["source_id"])
        if rewritten_path is not None:
            source["audio_path"] = rewritten_path

    manifest_path = Path(variant_paths["runtime_manifest_path"])
    Path(variant_paths["rendered_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(variant_paths["render_metadata_path"]).parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(variant_manifest, indent=2), encoding="utf-8")
    return manifest_path


def build_raven_project_name(project_name: str, variant_id: str) -> str:
    """Return a deterministic RAVEN-safe project name for one render variant."""
    return _filesystem_safe_name(f"{project_name}__{variant_id}")


def _filesystem_safe_name(value: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    safe_value = safe_value.strip("._-")
    if not safe_value:
        raise ValueError("RAVEN project name cannot be empty")
    return safe_value


def _flip_manifest_z_axis_for_raven(manifest: dict) -> None:
    manifest["receiver"]["position_m"] = _negate_z_coordinate(manifest["receiver"]["position_m"])
    for source in manifest["sources"]:
        source["position_m"] = _negate_z_coordinate(source["position_m"])


def _negate_z_coordinate(position_m: list[float]) -> list[float]:
    if len(position_m) != 3:
        raise ValueError(f"Se esperaba una posición XYZ de 3 elementos, pero se recibió: {position_m}")

    return [position_m[0], position_m[1], -position_m[2]]


def build_render_variant_paths(
    scene_manifest: dict,
    hrtf: dict,
    runtime_manifest_dir: Path,
    outputs: OutputsConfig,
    receiver_output: ReceiverOutputConfig,
) -> RenderVariantPaths:
    scene_id = scene_manifest["scene_id"]
    output_type = hrtf["hrtf_id"]
    resolved_paths = resolve_output_paths(
        scene_id,
        output_type,
        outputs,
        receiver_output,
        scene_manifest["project_name"],
    )
    return {
        "variant_id": resolved_paths["variant_id"],
        "scene_id": scene_id,
        "output_type": output_type,
        "output_subdir": resolved_paths["output_subdir"],
        "runtime_manifest_path": (runtime_manifest_dir / f"{resolved_paths['variant_id']}.json").resolve().as_posix(),
        "rendered_wav_path": resolved_paths["wav_path"],
        "render_metadata_path": resolved_paths["metadata_path"],
        "receiver_ir_path": Path(hrtf["hrtf_path"]).resolve().as_posix(),
    }


def _escape_matlab_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")
