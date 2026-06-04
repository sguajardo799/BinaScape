from pathlib import Path

from acoustic_orchestrator.config.models import AppConfig
from acoustic_orchestrator.pipeline.output_paths import get_receiver_output_config, resolve_output_paths


def build_static_manifest(config: AppConfig, sampled_scene: dict, scene_index: int, manifest_path: Path) -> dict:
    sequence = scene_index + 1
    scene_id = f"{config.outputs.naming.scene_id_prefix}_{sequence:04d}"
    primary_hrtf_id = sampled_scene["receiver"]["hrtfs"][0]["hrtf_id"]
    primary_output = get_receiver_output_config(config, primary_hrtf_id)
    resolved_paths = resolve_output_paths(
        scene_id,
        primary_hrtf_id,
        config.outputs,
        primary_output,
        config.experiment.experiment_id,
    )
    render = {
        "sample_rate_hz": config.render.sample_rate_hz,
        "seed": config.experiment.random_seed,
        "output_wav_path": resolved_paths["wav_path"],
        "output_metadata_path": resolved_paths["metadata_path"],
    }
    if config.source_sampling.timing.total_duration_s is not None:
        render["target_duration_s"] = config.source_sampling.timing.total_duration_s

    return {
        "schema_version": "1.0",
        "scene_type": config.experiment.scene_type,
        "base_rpf_file": config.raven.base_rpf_file.as_posix(),
        "project_name": config.experiment.experiment_id,
        "scene_id": scene_id,
        "job_id": f"render_job_static_{sequence:04d}",
        "room": {
            "room_id": sampled_scene["room"]["room_id"],
            "dimensions_m": [
                sampled_scene["room"]["dimensions"]["length"],
                sampled_scene["room"]["dimensions"]["width"],
                sampled_scene["room"]["dimensions"]["height"],
            ],
            "materials": sampled_scene["room"]["materials"],
            "material_files": {
                surface_id: {
                    "material_id": material_file["material_id"],
                    "material_path": _absolute_path(material_file["material_path"]),
                }
                for surface_id, material_file in sampled_scene["room"]["material_files"].items()
            },
        },
        "receiver": {
            "receiver_id": sampled_scene["receiver"]["receiver_id"],
            "position_m": sampled_scene["receiver"]["position_m"],
            "orientation_deg": sampled_scene["receiver"]["orientation_deg"],
            "hrtfs": [
                {
                    "hrtf_id": hrtf["hrtf_id"],
                    "hrtf_path": _absolute_path(hrtf["hrtf_path"]),
                }
                for hrtf in sampled_scene["receiver"]["hrtfs"]
            ],
        },
        "sources": [
            _build_source_manifest(source)
            for source in sampled_scene["sources"]
        ],
        "background_noise": _build_background_noise_manifest(sampled_scene.get("background_noise")),
        "render": render,
    }


def _build_source_manifest(source: dict) -> dict:
    manifest_source = {
        "source_id": source["source_id"],
        "event_type": source["event_type"],
        "audio_path": _absolute_path(source["audio_path"]),
        "position_m": source["position_m"],
        "orientation_deg": source["orientation_deg"],
        "gain_db": source["gain_db"],
        "start_time_s": source["start_time_s"],
    }
    if "directivity" in source:
        manifest_source["directivity_path"] = _absolute_path(source["directivity"])
    return manifest_source


def _build_background_noise_manifest(background_noise: dict | None) -> dict:
    if not background_noise or not background_noise.get("enabled", False):
        return {"enabled": False, "layers": []}

    return {
        "enabled": True,
        "layers": [dict(layer) for layer in background_noise.get("layers", [])],
    }


def _absolute_path(path: Path) -> str:
    return Path(path).resolve().as_posix()
