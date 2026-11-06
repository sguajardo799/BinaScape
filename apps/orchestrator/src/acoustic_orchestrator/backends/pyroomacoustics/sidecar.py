"""Render metadata sidecar compatible with the existing array-variant validation.

Mirrors ``export_render_metadata.m`` for array variants (legacy z-reflected
source poses, ``receiver.circular_array`` + ``coordinate_frame``, seeds, noise,
T30 block, ``batch``) and adds ``summary.render_backend`` and a top-level
``backend`` block. Backend-frame data lives only under ``backend``.
"""

from copy import deepcopy

from acoustic_orchestrator.backends.pyroomacoustics.frames import transform_metadata
from acoustic_orchestrator.backends.pyroomacoustics.synthesis import PreparedSource
from acoustic_orchestrator.pipeline.render_backends import get_render_backend
from acoustic_orchestrator.pipeline.render_validation import _source_pose, effective_seed

BACKEND = get_render_backend("pyroomacoustics")


def _optional(value, default):
    return default if value is None or value == [] or value == "" else value


def source_summary(source: dict, prepared: PreparedSource, schema_version: str) -> dict:
    pose = _source_pose(source, schema_version)
    start = float(_optional(source.get("start_time_s"), 0.0))
    return {
        "source_id": source["source_id"],
        "source_name": source["source_id"],
        "event_type": str(_optional(source.get("event_type"), "")),
        "audio_path": source["audio_path"],
        "identity_id": str(_optional(source.get("identity_id"), "")),
        "audio_relpath": str(_optional(source.get("audio_relpath"), "")),
        "directivity_path": "",
        "position_m": pose["position_m"],
        "view_vector": pose["view_vector"],
        "up_vector": pose["up_vector"],
        "gain_db": float(_optional(source.get("gain_db"), 0.0)),
        "source_start_time_s": start,
        "source_end_time_s": start + prepared.original_duration_s,
        "source_original_duration_s": prepared.original_duration_s,
        "original_num_channels": prepared.original_num_channels,
        "downmix_policy": prepared.downmix_policy,
    }


def build_sidecar(*, job, prepared_sources: list[PreparedSource], num_samples: int, background_noise: dict,
                  reverberation: dict, t30_pairs: list, timing: dict, rir_lengths: list[list[int]],
                  seed_applied: int, package_version: str, effective_max_order: int) -> dict:
    manifest = job.manifest
    render = manifest["render"]
    schema = manifest.get("schema_version", "1.0")
    seed, seed_source = effective_seed(manifest)
    parameters = job.parameters
    description = job.room_description
    return {
        "schema_version": schema,
        "scene_id": manifest["scene_id"],
        "job_id": manifest["job_id"],
        "scene_type": manifest["scene_type"],
        "sample_rate_hz": job.fs,
        "summary": {
            "render_backend": BACKEND.summary_render_backend,
            "n_sources": len(job.sources),
            "room": {"reverberation": reverberation},
            "sources": [source_summary(item.source, prepared, schema)
                        for item, prepared in zip(job.sources, prepared_sources, strict=True)],
            "output_num_samples": num_samples,
            "output_num_channels": job.num_channels,
            "background_noise": background_noise,
        },
        "output_type": "circular_array",
        "receiver": {"circular_array": deepcopy(manifest["receiver"]["circular_array"]),
                     "coordinate_frame": "public_xyz_y_up"},
        "effective_seed": seed,
        "seed_source": seed_source,
        "output_wav_path": render["output_wav_path"],
        "output_metadata_path": render["output_metadata_path"],
        "project_name": manifest["project_name"],
        "batch": {"n_variants": 1},
        "backend": {
            "name": BACKEND.name,
            "version": package_version,
            "simulation": {
                "method": "ism",
                "max_order": parameters["max_order"],
                "max_order_non_shoebox": parameters["max_order_non_shoebox"],
                "effective_max_order": effective_max_order,
                "air_absorption": parameters["air_absorption"],
                "temperature_c": parameters.get("temperature_c"),
                "humidity_pct": parameters.get("humidity_pct"),
                "speed_of_sound_m_s": timing["speed_of_sound_m_s"],
                "ray_tracing": False,
                "randomized_image_sources": False,
                "seed_applied": seed_applied,
            },
            "coordinate_transform": transform_metadata(description["translation_m"]),
            "room": {
                "geometry_type": description["geometry_type"],
                "construction": "shoebox" if description["geometry_type"] == "shoebox" else "extruded_polygon",
                "height_m": description["height_m"],
                "footprint_vertices_xy_m": description["vertices_xy"],
                "wall_surface_ids": description["wall_surface_ids"],
            },
            "positions_m": {
                "microphones": [list(position) for position in job.microphones_backend_m],
                "sources": {item.source["source_id"]: list(item.position_backend_m) for item in job.sources},
            },
            "materials": job.materials,
            "rir": {
                "global_delay_samples": timing["global_delay_samples"],
                "frac_delay_length": timing["frac_delay_length"],
                "high_pass_filter": timing["high_pass_filter"],
                "lengths_samples": rir_lengths,
            },
            "t30_pairs_s": t30_pairs,
        },
    }
