import json
import re
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import NotRequired, TypedDict

from acoustic_orchestrator.config.models import CircularArrayOutputConfig, OutputsConfig, ReceiverOutputConfig
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
    receiver_ir_path: str | None
    circular_array: NotRequired[dict]
    sample_rate_hz: NotRequired[int]


def render_manifest_with_matlab(manifest_path: Path, matlab_executable: str = "matlab") -> None:
    if not MATLAB_ENTRYPOINT.is_file():
        raise FileNotFoundError(f"No se encontró el entrypoint de MATLAB: {MATLAB_ENTRYPOINT}")

    manifest_path = manifest_path.resolve()
    batch_command = (
        f"addpath(genpath('{_escape_matlab_path(MATLAB_APP_DIR)}')); "
        f"run_raven_static_render('{_escape_matlab_path(manifest_path)}');"
    )
    subprocess.run([matlab_executable, "-batch", batch_command], check=True)


def write_single_receiver_render_manifest(
    scene_manifest: dict,
    hrtf: dict,
    output_dir: Path,
    outputs: OutputsConfig,
    receiver_output: ReceiverOutputConfig | CircularArrayOutputConfig,
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
    receiver = variant_manifest["receiver"]
    receiver.pop("hrtf", None)
    receiver.pop("active_hrtf", None)
    if variant_paths["output_type"] == "circular_array":
        receiver["hrtfs"] = []
        receiver["circular_array"] = deepcopy(hrtf)
    else:
        receiver["hrtfs"] = [deepcopy(hrtf)]
        receiver.pop("circular_array", None)
    variant_manifest["render"]["active_output_type"] = variant_paths["output_type"]
    variant_manifest["render"]["output_wav_path"] = variant_paths["rendered_wav_path"]
    variant_manifest["render"]["output_metadata_path"] = variant_paths["render_metadata_path"]

    for source in variant_manifest["sources"]:
        rewritten_path = (source_path_rewrites or {}).get(source["source_id"])
        if rewritten_path is not None:
            source["audio_path"] = rewritten_path

    manifest_path = Path(variant_paths["runtime_manifest_path"])
    manifest_path.write_text(json.dumps(variant_manifest, indent=2), encoding="utf-8")
    return manifest_path


# Compatibilidad para consumidores del helper anterior, sin cambiar el contrato.
write_single_hrtf_render_manifest = write_single_receiver_render_manifest


def build_raven_project_name(project_name: str, variant_id: str) -> str:
    """Return a deterministic RAVEN-safe project name for one render variant."""
    return _filesystem_safe_name(f"{project_name}__{variant_id}")


def _filesystem_safe_name(value: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    safe_value = safe_value.strip("._-")
    if not safe_value:
        raise ValueError("RAVEN project name cannot be empty")
    return safe_value


def build_render_variant_paths(
    scene_manifest: dict,
    hrtf: dict,
    runtime_manifest_dir: Path,
    outputs: OutputsConfig,
    receiver_output: ReceiverOutputConfig | CircularArrayOutputConfig,
) -> RenderVariantPaths:
    scene_id = scene_manifest["scene_id"]
    is_array = hrtf.get("output_type") == "circular_array"
    output_type = "circular_array" if is_array else hrtf["hrtf_id"]
    resolved_paths = resolve_output_paths(
        scene_id,
        output_type,
        outputs,
        receiver_output,
        scene_manifest["project_name"],
    )
    paths: RenderVariantPaths = {
        "variant_id": resolved_paths["variant_id"],
        "scene_id": scene_id,
        "output_type": output_type,
        "output_subdir": resolved_paths["output_subdir"],
        "runtime_manifest_path": (runtime_manifest_dir / f"{resolved_paths['variant_id']}.json").resolve().as_posix(),
        "rendered_wav_path": resolved_paths["wav_path"],
        "render_metadata_path": resolved_paths["metadata_path"],
        "receiver_ir_path": None if is_array else Path(hrtf["hrtf_path"]).resolve().as_posix(),
    }
    if is_array:
        paths["circular_array"] = deepcopy(hrtf)
        paths["sample_rate_hz"] = scene_manifest["render"]["sample_rate_hz"]
    return paths


def receiver_variants(scene_manifest: dict) -> list[dict]:
    variants = sorted(scene_manifest["receiver"].get("hrtfs", []), key=lambda item: item["hrtf_id"])
    array = scene_manifest["receiver"].get("circular_array")
    if array is not None:
        variants.append(array)
    return variants


def _escape_matlab_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")
