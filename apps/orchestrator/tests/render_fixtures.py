"""Small, decodable render fixtures matching the MATLAB file contract.

These are simulated outputs, not evidence of a physical RAVEN simulation.
Real exporter interoperability is covered by write_render_contract_fixture.m.
"""

import json
import hashlib
from functools import wraps
import math
from pathlib import Path
import wave

from acoustic_orchestrator.pipeline.matlab_runner import MatlabProcessResult
from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt


FREQUENCIES = [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]


def runtime_for_paths(paths):
    manifest = {
        "schema_version": "3.0", "scene_type": "static", "scene_id": paths["scene_id"],
        "job_id": "job", "project_name": "project",
        "receiver": {"hrtfs": [{"hrtf_id": paths["output_type"], "hrtf_path": paths["receiver_ir_path"]}]},
        "sources": [{"source_id": "src1", "audio_path": str(Path(paths["rendered_wav_path"]).parent / "source.wav"),
                     "position_m": [1, 1, 1], "orientation_deg": {"yaw": 0, "pitch": 0, "roll": 0}}],
        "render": {"active_output_type": paths["output_type"], "sample_rate_hz": 44100,
                   "output_wav_path": paths["rendered_wav_path"], "output_metadata_path": paths["render_metadata_path"]},
    }
    if paths["output_type"] == "circular_array":
        manifest["receiver"] = {"hrtfs": [], "circular_array": paths["circular_array"]}
    return manifest


def write_render_wav(manifest):
    render = manifest["render"]
    output_type = render.get("active_output_type", "binaural_hrtf")
    channels = manifest["receiver"]["circular_array"]["num_microphones"] if output_type == "circular_array" else 2
    samples = max(1, math.floor(render["target_duration_s"] * 44100 + 0.5)) if render.get("target_duration_s") else 200
    path = Path(render["output_wav_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        block = b"\x10\x00" * channels * min(samples, 65536)
        for start in range(0, samples, 65536):
            audio.writeframesraw(block[:min(samples - start, 65536) * channels * 2])
    return samples, channels


def write_render_metadata(manifest, t30_s=None):
    render = manifest["render"]
    output_type = render.get("active_output_type") or manifest["receiver"]["hrtfs"][0]["hrtf_id"]
    path = Path(render["output_metadata_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(render["output_wav_path"]), "rb") as audio:
        samples, channels = audio.getnframes(), audio.getnchannels()
    sources = manifest["sources"]
    sources = sources if isinstance(sources, list) else [sources]
    entries = []
    reflection = -1 if manifest.get("schema_version", "1.0") in ("2.0", "3.0") else 1
    for source in sources:
        yaw, pitch = (math.radians(source.get("orientation_deg", {}).get(field, 0)) for field in ("yaw", "pitch"))
        position = source["position_m"]
        start = source.get("start_time_s", 0)
        original_channels, duration = 1, 1.0
        if Path(source["audio_path"]).is_file():
            with wave.open(str(source["audio_path"]), "rb") as original:
                original_channels = original.getnchannels()
                duration = original.getnframes() / original.getframerate()
        entry = {
            "source_id": source["source_id"], "source_name": source["source_id"],
            "event_type": source.get("event_type", ""), "identity_id": source.get("identity_id", ""),
            "audio_relpath": source.get("audio_relpath", ""), "audio_path": source["audio_path"],
            "directivity_path": source.get("directivity_path", ""),
            "position_m": [position[0], position[1], reflection * position[2]],
            "view_vector": [math.cos(pitch)*math.cos(yaw), math.sin(pitch), reflection*math.cos(pitch)*math.sin(yaw)],
            "up_vector": [0, 1, 0], "gain_db": source.get("gain_db", 0),
            "source_start_time_s": start, "source_original_duration_s": duration, "source_end_time_s": start + duration,
        }
        if output_type == "circular_array":
            entry.update(original_num_channels=original_channels,
                         downmix_policy="arithmetic_mean" if original_channels > 1 else "none")
        entries.append(entry)
    values = [0.5] * 10 if t30_s is None else t30_s
    # MATLAB jsonencode represents non-finite T30 measurements as null.
    values = [value if type(value) in (int, float) and math.isfinite(value) else None for value in values]
    valid = [value is not None and value > 0 for value in values]
    all_valid = len(values) == 10 and all(valid)
    noise = {"enabled": False, "applied": False, "skipped_reason": "disabled", "layers": []}
    seed = render.get("seed", manifest.get("seed", []))
    configured_noise = manifest.get("background_noise") or {}
    if configured_noise.get("enabled"):
        layers = []
        for index, expected in enumerate(configured_noise["layers"]):
            raw_strategy = expected["strategy"]
            strategy = "audio_file" if raw_strategy == "audio_folder" else raw_strategy
            source_samples, source_channels = samples, 1
            if strategy == "audio_file":
                with wave.open(str(expected["path"]), "rb") as source:
                    source_samples, source_channels = source.getnframes(), source.getnchannels()
            layer_seed = expected.get("seed", [])
            layers.append({"strategy_original": raw_strategy, "strategy": strategy,
                "snr_db": expected["snr_db"], "color": expected.get("color", ""), "path": expected.get("path", ""),
                "seed": layer_seed, "effective_seed": layer_seed if layer_seed != [] else seed + index if seed != [] else index + 1,
                "source_num_samples": source_samples, "source_num_channels": source_channels,
                "target_num_samples": samples, "target_num_channels": channels,
                "duration_mode": "loop" if source_samples < samples else "trim" if source_samples > samples else "exact",
                "channel_mode": "mono_replicated" if source_channels == 1 and channels > 1 else "channel_to_channel",
                "skipped": False, "skipped_reason": "", "applied": True})
        noise = {"enabled": True, "applied": True, "skipped_reason": "", "layers": layers}
    summary = {"render_backend": "RAVEN + ITA", "n_sources": len(sources), "sources": entries,
        "output_num_samples": samples, "output_num_channels": channels, "background_noise": noise,
        "room": {"reverberation": {"metric": "T30", "estimated_from": "room", "method": "rpf.getT30",
            "t30_s": values, "band_frequencies_hz": FREQUENCIES if len(values) == 10 else [],
            "band_resolution": "octave", "frequency_mapping": "center_frequency", "valid_band_count": sum(valid),
            "mean_t30_s": sum(values)/10 if all_valid else [], "status": "estimated" if all_valid else "unavailable"}}}
    payload = {"schema_version": manifest.get("schema_version", "1.0"), "scene_id": manifest["scene_id"],
        "job_id": manifest["job_id"], "scene_type": "static", "project_name": manifest["project_name"],
        "sample_rate_hz": 44100, "output_type": output_type, "summary": summary,
        "effective_seed": seed, "seed_source": "render.seed" if "seed" in render else "seed" if "seed" in manifest else "unspecified",
        "output_wav_path": render["output_wav_path"], "output_metadata_path": render["output_metadata_path"]}
    if output_type == "circular_array":
        payload["receiver"] = {"circular_array": manifest["receiver"]["circular_array"], "coordinate_frame": "public_xyz_y_up"}
    else:
        ir = next(item for item in manifest["receiver"]["hrtfs"] if item["hrtf_id"] == output_type)
        payload.update(hrtf_id=output_type, hrtf_path=ir["hrtf_path"])
    path.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
    return payload


def write_variant_fixture(paths, *, metadata=False):
    manifest = runtime_for_paths(paths)
    runtime = Path(paths["runtime_manifest_path"])
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(json.dumps(manifest), encoding="utf-8")
    write_render_wav(manifest)
    if metadata:
        write_render_metadata(manifest)
    return manifest


def write_completion_for_attempt(attempt):
    payload = {key: attempt.record[key] for key in (
        "attempt_id", "scene_id", "variant_id", "output_type", "manifest_sha256",
    )}
    payload.update(schema_version=1, artifacts=[
        {**item, "size_bytes": Path(item["path"]).stat().st_size,
         "sha256": hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()}
        for item in attempt.expected_artifacts
    ])
    attempt.completion_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def simulate_matlab_process(render):
    """Adapt a renderer stub to the explicit process-evidence runner contract."""
    @wraps(render)
    def invoke(manifest_path, *args, **kwargs):
        attempt = kwargs.get("attempt") or RenderAttempt(manifest_path, kwargs.get("attempts_dir"))
        render(manifest_path, *args, **kwargs)
        attempt.record.update(process_status="succeeded", exit_code=0, termination_observed=True)
        attempt.save()
        return MatlabProcessResult(attempt, 0, None, True, "succeeded")
    return invoke
