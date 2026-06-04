from pathlib import Path, PurePath
from string import Formatter
from typing import TypedDict

from acoustic_orchestrator.config.models import AppConfig, OutputsConfig, ReceiverOutputConfig


SUPPORTED_NAMING_FIELDS = {"scene_id", "output_type"}


class ResolvedOutputPaths(TypedDict):
    variant_id: str
    wav_path: str
    metadata_path: str
    output_subdir: str


class ArtifactLayout(TypedDict):
    run_root: Path
    scene_manifest_dir: Path
    runtime_manifest_dir: Path
    prepared_audio_dir: Path
    clarity_manifest_dir: Path
    clarity_jobs_path: Path
    render_index_path: Path
    clarity_index_path: Path
    render_outputs_root: Path
    degraded_output_root: Path


class ClarityJobPaths(TypedDict):
    job_id: str
    output_dir: str
    expected_output_wav_path: str
    expected_output_metadata_path: str


def resolve_artifact_layout(outputs: OutputsConfig, experiment_id: str) -> ArtifactLayout:
    run_name = outputs.run_name or experiment_id
    run_root = (outputs.artifact_root / run_name).resolve()
    return {
        "run_root": run_root,
        "scene_manifest_dir": run_root / "manifests" / "scene",
        "runtime_manifest_dir": run_root / "manifests" / "runtime" / "render",
        "prepared_audio_dir": run_root / "audio" / "prepared",
        "clarity_manifest_dir": run_root / "manifests" / "runtime" / "clarity",
        "clarity_jobs_path": run_root / "manifests" / "runtime" / "clarity" / "clarity_jobs.jsonl",
        "render_index_path": run_root / "indexes" / "render_index.jsonl",
        "clarity_index_path": run_root / "indexes" / "clarity_index.jsonl",
        "render_outputs_root": run_root / "outputs" / "render",
        "degraded_output_root": run_root / "outputs" / "degraded",
    }


def resolve_output_paths(
    scene_id: str,
    output_type: str,
    outputs: OutputsConfig,
    receiver_output: ReceiverOutputConfig,
    experiment_id: str,
) -> ResolvedOutputPaths:
    variant_id = f"{scene_id}__{output_type}"
    values = {"scene_id": scene_id, "output_type": output_type}
    output_subdir = receiver_output.output_subdir
    layout = resolve_artifact_layout(outputs, experiment_id)
    render_output_dir = (layout["render_outputs_root"] / output_subdir).resolve()

    wav_name = outputs.naming.wav_pattern.format(**values)
    metadata_name = outputs.naming.metadata_pattern.format(**values)

    return {
        "variant_id": variant_id,
        "wav_path": (render_output_dir / wav_name).resolve().as_posix(),
        "metadata_path": (render_output_dir / metadata_name).resolve().as_posix(),
        "output_subdir": output_subdir,
    }


def get_receiver_output_config(config: AppConfig, output_type: str) -> ReceiverOutputConfig:
    receiver_output = {
        "binaural_hrtf": config.receiver_outputs.binaural_hrtf,
        "bte_rear_hartf": config.receiver_outputs.bte_rear_hartf,
        "bte_front_hartf": config.receiver_outputs.bte_front_hartf,
    }.get(output_type)
    if receiver_output is None:
        raise KeyError(f"receiver_output desconocido: {output_type}")
    return receiver_output


def build_clarity_job_id(variant_id: str, hearing_profile_id: str) -> str:
    return f"clarity__{variant_id}__{hearing_profile_id}"


def resolve_clarity_job_paths(
    *,
    output_root: Path,
    output_type: str,
    variant_id: str,
    hearing_profile_id: str,
    input_wav_path: str | Path,
) -> ClarityJobPaths:
    input_wav = Path(input_wav_path)
    output_dir = (output_root / output_type / hearing_profile_id).resolve()
    return {
        "job_id": build_clarity_job_id(variant_id, hearing_profile_id),
        "output_dir": output_dir.as_posix(),
        "expected_output_wav_path": (output_dir / input_wav.name).resolve().as_posix(),
        "expected_output_metadata_path": (output_dir / f"{input_wav.stem}.json").resolve().as_posix(),
    }


def find_unsupported_placeholders(pattern: str) -> set[str]:
    formatter = Formatter()
    placeholders = {
        field_name
        for _, field_name, _, _ in formatter.parse(pattern)
        if field_name is not None and field_name != ""
    }
    return placeholders - SUPPORTED_NAMING_FIELDS


def is_safe_output_subdir(output_subdir: str) -> bool:
    if output_subdir.strip() == "":
        return False

    path = PurePath(output_subdir)
    if path.is_absolute():
        return False

    return all(part not in {"", ".", ".."} for part in path.parts)
