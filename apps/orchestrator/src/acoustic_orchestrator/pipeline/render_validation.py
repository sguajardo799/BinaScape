"""Shared artifact validation; process exit codes and diagnostics are not inputs."""

from dataclasses import dataclass, field
from collections.abc import Mapping
from copy import deepcopy
import math
import os
import re
from pathlib import Path
import struct
from typing import Any, Literal, TypeGuard
import wave

from acoustic_orchestrator.pipeline.render_attempt import read_json_object


ValidationProfile = Literal["normal", "recovery", "reuse"]
RenderValidity = Literal["valid", "incomplete", "invalid", "not_verifiable"]
RAVEN_OCTAVE_FREQUENCIES_HZ = (31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)


def requires_metadata(*, metadata_required: bool, canonical: bool, profile: ValidationProfile) -> bool:
    if profile not in {"normal", "recovery", "reuse"}:
        raise ValueError(f"unknown render validation profile: {profile}")
    return metadata_required or canonical or profile == "recovery"


@dataclass
class RenderValidation:
    """One evaluation, retaining integrity problems separately from T30 rejection."""

    missing: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    unverifiable: list[str] = field(default_factory=list)
    acoustic_rejection: dict[str, Any] | None = None
    wav_info: dict[str, int] | None = None
    metadata: dict[str, Any] | None = None

    @property
    def artifacts_ok(self) -> bool:
        return not (self.missing or self.invalid or self.unverifiable)

    @property
    def validity(self) -> RenderValidity:
        if self.invalid:
            return "invalid"
        if self.missing:
            return "incomplete"
        if self.unverifiable:
            return "not_verifiable"
        return "invalid" if self.acoustic_rejection is not None else "valid"

    def as_record(self) -> dict[str, Any]:
        issues = [*self.missing, *self.invalid, *self.unverifiable]
        return {
            "ok": self.validity == "valid",
            "validity": self.validity,
            "issues": issues + ([self.acoustic_rejection["reason"]] if self.acoustic_rejection is not None else []),
            "artifact_issues": issues,
            "acoustic_rejection": self.acoustic_rejection,
        }


def _pcm_data_size(path: Path) -> int:
    """Check RIFF bounds and chunks as well as wave's interpreted header.

    wave.readframes alone silently returns fewer bytes on truncated files and
    ignores unaligned bytes at the end of a declared data chunk.
    """
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ValueError("wav_riff_header_invalid")
        end = struct.unpack("<I", header[4:8])[0] + 8
        if end != path.stat().st_size:
            raise ValueError("wav_riff_size_mismatch")
        data_sizes = []
        fmt_count = 0
        while stream.tell() < end:
            chunk = stream.read(8)
            if len(chunk) != 8:
                raise ValueError("wav_chunk_header_truncated")
            kind, size = struct.unpack("<4sI", chunk)
            next_offset = stream.tell() + size + size % 2
            if next_offset > end:
                raise ValueError("wav_chunk_truncated")
            if kind == b"data":
                data_sizes.append(size)
            elif kind == b"fmt ":
                fmt_count += 1
            stream.seek(next_offset)
        if len(data_sizes) != 1 or fmt_count != 1:
            raise ValueError("wav_chunk_inventory_invalid")
        return data_sizes[0]


def validate_wav(path: Path, *, expected_channels: int, sample_rate_hz: int = 44100) -> RenderValidation:
    """Read every PCM frame with bounded memory; PCM integer samples are finite."""
    result = RenderValidation()
    try:
        data_size = _pcm_data_size(path)
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            width = audio.getsampwidth()
            frames = audio.getnframes()
            rate = audio.getframerate()
            result.wav_info = {"num_channels": channels, "num_samples": frames, "sample_rate_hz": rate,
                               "sample_width_bytes": width}
            if audio.getcomptype() != "NONE" or width not in (1, 2, 3, 4):
                result.invalid.append("wav_pcm_format_unsupported")
            if channels != expected_channels:
                result.invalid.append("wav_channel_count_mismatch")
            if rate != sample_rate_hz or rate != 44100:
                result.invalid.append("wav_sample_rate_mismatch")
            frame_size = channels * width
            if data_size % frame_size or data_size != frames * frame_size:
                result.invalid.append("wav_frame_alignment_invalid")
            if frames <= 0:
                result.invalid.append("wav_empty")
            consumed = 0
            while block := audio.readframes(65536):
                if len(block) % frame_size:
                    result.invalid.append("wav_frame_alignment_invalid")
                consumed += len(block)
            # The PCM encoding defines only finite integer values. No signal
            # threshold (silence, clipping or channel equality) is imposed.
            if consumed != data_size:
                result.invalid.append("wav_payload_truncated")
    except FileNotFoundError:
        result.missing.append("rendered_wav_missing")
    except (wave.Error, EOFError, ValueError, struct.error) as exc:
        result.invalid.append(f"wav_unreadable: {exc}")
    except OSError as exc:
        result.unverifiable.append(f"wav_read_error: {exc}")
    return result


def is_finite_number(value: Any) -> TypeGuard[int | float]:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_dimensions(result: RenderValidation, render: dict[str, Any], summary: dict[str, Any] | None) -> None:
    """Cross-check actual frames, optional sidecar counters and target duration."""
    if result.wav_info is None:
        return
    observed = result.wav_info
    if summary is not None:
        for metadata_field, wav_field in (("output_num_samples", "num_samples"), ("output_num_channels", "num_channels")):
            value = summary.get(metadata_field)
            if type(value) is not int or value <= 0:
                result.invalid.append(f"render_metadata_{metadata_field}_invalid")
            elif value != observed[wav_field]:
                result.invalid.append(f"render_metadata_{metadata_field}_mismatch")
    target = render.get("target_duration_s")
    if target is None or target == []:
        return
    rate = render.get("sample_rate_hz", 44100)
    if not is_finite_number(target) or target <= 0 or not is_finite_number(rate) or rate <= 0:
        result.invalid.append("runtime_target_duration_invalid")
        return
    product = target * rate
    if not math.isfinite(product):
        result.invalid.append("runtime_target_duration_invalid")
        return
    # Positive durations: MATLAB rounds half away from zero, unlike Python.
    expected_samples = max(1, math.floor(product + 0.5))
    if observed["num_samples"] != expected_samples:
        result.invalid.append("wav_target_duration_mismatch")


def _finite_json(value: Any) -> bool:
    if isinstance(value, dict):
        return all(_finite_json(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite_json(item) for item in value)
    return not isinstance(value, float) or math.isfinite(value)


def _path_equal(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, str) or not left or not right:
        return False
    try:
        return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))
    except (OSError, ValueError, RuntimeError):
        return False


def object_entries(value: Any) -> list[dict[str, Any]] | None:
    """MATLAB jsonencode emits a scalar struct as an object, not a list."""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    return None


def read_render_metadata(
    result: RenderValidation, path: Path, manifest: dict[str, Any], *, required: bool,
) -> None:
    try:
        payload = read_json_object(path)
    except FileNotFoundError:
        if required:
            result.missing.append("render_metadata_missing")
        return
    except (ValueError, UnicodeError) as exc:
        result.invalid.append(f"render_metadata_parse_error: {exc}")
        return
    except OSError as exc:
        result.unverifiable.append(f"render_metadata_read_error: {exc}")
        return
    result.metadata = payload
    if not _finite_json(payload):
        result.invalid.append("render_metadata_non_finite")
    schema = payload.get("schema_version")
    if schema not in ("1.0", "2.0", "3.0"):
        result.invalid.append("render_metadata_schema_unsupported")
    elif schema != manifest.get("schema_version", "1.0"):
        result.invalid.append("render_metadata_schema_mismatch")
    render = manifest.get("render", {})
    expected = {
        "scene_id": manifest.get("scene_id"), "job_id": manifest.get("job_id"),
        "scene_type": "static", "output_type": render.get("active_output_type"),
        "project_name": manifest.get("project_name"),
        "output_wav_path": render.get("output_wav_path"),
        "output_metadata_path": render.get("output_metadata_path"),
    }
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        result.invalid.append("render_metadata_summary_invalid")
        summary = {}
    for field_name, expected_value in expected.items():
        observed = payload.get(field_name)
        if not isinstance(observed, str) or not observed.strip():
            result.invalid.append(f"render_metadata_{field_name}_missing")
        elif (not _path_equal(observed, expected_value) if field_name.endswith("_path") else observed != expected_value):
            result.invalid.append(f"render_metadata_{field_name}_mismatch")
        # Older layouts can duplicate these fields. They must agree rather
        # than allowing a preferred section to conceal a contradiction.
        sections = [summary]
        if isinstance(payload.get("render"), dict):
            sections.append(payload["render"])
        for section in sections:
            if field_name in section:
                value = section[field_name]
                equal = _path_equal(value, observed) if field_name.endswith("_path") else value == observed
                if not equal:
                    result.invalid.append(f"render_metadata_{field_name}_contradictory")
    rate = payload.get("sample_rate_hz")
    if type(rate) is not int or rate != render.get("sample_rate_hz", 44100):
        result.invalid.append("render_metadata_sample_rate_hz_mismatch")
    for field_name in ("output_num_samples", "output_num_channels", "n_sources"):
        value = summary.get(field_name)
        if type(value) is not int or value <= 0:
            result.invalid.append(f"render_metadata_{field_name}_invalid")
    sources = object_entries(summary.get("sources"))
    if sources is None or not sources or len(sources) != summary.get("n_sources"):
        result.invalid.append("render_metadata_sources_invalid")
    validate_dimensions(result, render, summary)


def numeric_equal(expected: Any, observed: Any) -> bool:
    if isinstance(expected, (list, tuple)):
        return isinstance(observed, list) and len(expected) == len(observed) and all(
            numeric_equal(a, b) for a, b in zip(expected, observed, strict=True)
        )
    return is_finite_number(expected) and is_finite_number(observed) and math.isclose(
        expected, observed, rel_tol=1e-9, abs_tol=1e-6,
    )


def _optional(value: Any, default: Any) -> Any:
    return default if value is None or value == [] or value == "" else value


def _source_pose(source: dict[str, Any], schema: str) -> dict[str, list[float]]:
    position = source.get("position_m")
    if not isinstance(position, list) or len(position) != 3 or not all(is_finite_number(v) for v in position):
        raise ValueError("source position must be three finite numbers")
    orientation = source.get("orientation_deg")
    if not isinstance(orientation, dict):
        raise ValueError("source orientation missing")
    pitch, yaw = (_optional(orientation.get(field_name), 0) for field_name in ("pitch", "yaw"))
    if not is_finite_number(pitch) or not is_finite_number(yaw):
        raise ValueError("source orientation invalid")
    pitch, yaw = math.radians(pitch), math.radians(yaw)
    reflection = -1 if schema in ("2.0", "3.0") else 1
    # Mirrors orientation_deg_to_vectors: roll does not alter these vectors.
    return {
        "position_m": [position[0], position[1], reflection * position[2]],
        "view_vector": [math.cos(pitch) * math.cos(yaw), math.sin(pitch),
                        reflection * math.cos(pitch) * math.sin(yaw)],
        "up_vector": [0, 1, 0],
    }


def validate_sources(result: RenderValidation, manifest: dict[str, Any]) -> None:
    if result.metadata is None:
        return
    summary = result.metadata.get("summary")
    if not isinstance(summary, dict):
        return  # Structural validator has already recorded this problem.
    expected = object_entries(manifest.get("sources"))
    observed = object_entries(summary.get("sources"))
    if expected is None or not expected:
        result.invalid.append("runtime_sources_invalid")
        return
    if observed is None:
        result.invalid.append("render_metadata_sources_invalid")
        return
    expected_ids = [item.get("source_id") for item in expected]
    observed_ids = [item.get("source_id") for item in observed]
    if (any(not isinstance(value, str) or not value for value in expected_ids + observed_ids)
            or len(set(expected_ids)) != len(expected_ids) or len(set(observed_ids)) != len(observed_ids)
            or set(expected_ids) != set(observed_ids)):
        result.invalid.append("render_metadata_source_inventory_mismatch")
        return
    by_id = {item["source_id"]: item for item in observed}
    if summary.get("n_sources") != len(expected):
        result.invalid.append("render_metadata_source_count_mismatch")
    for source in expected:
        source_id = source["source_id"]
        metadata = by_id[source_id]
        for field_name in ("event_type", "identity_id", "audio_relpath"):
            value = _optional(source.get(field_name), "")
            if not isinstance(metadata.get(field_name), str) or metadata[field_name] != value:
                result.invalid.append(f"source_{field_name}_mismatch: {source_id}")
        if metadata.get("source_name") != source_id:
            result.invalid.append(f"source_name_mismatch: {source_id}")
        if not _path_equal(metadata.get("audio_path"), source.get("audio_path")):
            result.invalid.append(f"source_audio_path_mismatch: {source_id}")
        directivity = _optional(source.get("directivity_path"), "")
        observed_directivity = metadata.get("directivity_path")
        if not (_path_equal(directivity, observed_directivity) if directivity else observed_directivity == ""):
            result.invalid.append(f"source_directivity_path_mismatch: {source_id}")
        for field_name, runtime_field in (("gain_db", "gain_db"), ("source_start_time_s", "start_time_s")):
            if not numeric_equal(_optional(source.get(runtime_field), 0), metadata.get(field_name)):
                result.invalid.append(f"source_{field_name}_mismatch: {source_id}")
        try:
            expected_pose = _source_pose(source, manifest.get("schema_version", "1.0"))
            for field_name, value in expected_pose.items():
                if not numeric_equal(value, metadata.get(field_name)):
                    result.invalid.append(f"source_{field_name}_mismatch: {source_id}")
        except ValueError as exc:
            result.invalid.append(f"runtime_source_pose_invalid: {source_id}: {exc}")
        duration = metadata.get("source_original_duration_s")
        start, end = metadata.get("source_start_time_s"), metadata.get("source_end_time_s")
        if not is_finite_number(duration) or duration <= 0 or not is_finite_number(start) or not numeric_equal(start + duration, end):
            result.invalid.append(f"source_duration_invalid: {source_id}")


def _matches_value(expected: Any, observed: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(observed, dict) and all(
            key in observed and _matches_value(value, observed[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(observed, list) and len(expected) == len(observed) and all(
            _matches_value(a, b) for a, b in zip(expected, observed, strict=True)
        )
    if type(expected) in (int, float):
        return numeric_equal(expected, observed)
    return type(expected) is type(observed) and expected == observed


def effective_seed(manifest: dict[str, Any]) -> tuple[Any, str]:
    seed = _optional(manifest.get("render", {}).get("seed"), None)
    if seed is not None:
        return seed, "render.seed"
    seed = _optional(manifest.get("seed"), None)
    return (seed, "seed") if seed is not None else ([], "unspecified")


def _receiver_irs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    receiver = manifest.get("receiver", {})
    if not isinstance(receiver, dict):
        return []
    items = object_entries(receiver.get("hrtfs") or receiver.get("hrtf")) or []
    used: dict[str, int] = {}
    normalized = []
    for item in items:
        raw = item.get("hrtf_id", "")
        if not isinstance(raw, str) or not isinstance(item.get("hrtf_path"), str) or not item["hrtf_path"]:
            continue
        safe = re.sub("-+", "-", re.sub(r"[^a-zA-Z0-9_-]+", "-", raw.strip().lower())).strip("-_")
        if not safe:
            stem = Path(item.get("hrtf_path", "")).stem.lower()
            safe = re.sub("-+", "-", re.sub(r"[^a-zA-Z0-9_-]+", "-", stem)).strip("-_")
        base = safe
        used[base] = used.get(base, 0) + 1
        if used[base] > 1:
            safe = f"{base}-{used[base]}"
        normalized.append({**item, "hrtf_id": safe})
    return normalized


def validate_receiver_and_seed(result: RenderValidation, manifest: dict[str, Any]) -> None:
    if result.metadata is None:
        return
    metadata = result.metadata
    receiver = manifest.get("receiver", {})
    if not isinstance(receiver, dict):
        result.invalid.append("runtime_receiver_invalid")
        return
    output_type = manifest.get("render", {}).get("active_output_type")
    if output_type == "circular_array":
        array = receiver.get("circular_array")
        observed_receiver = metadata.get("receiver")
        if not isinstance(observed_receiver, dict):
            observed_receiver = {}
        if not isinstance(array, dict) or not _matches_value(array, observed_receiver.get("circular_array")):
            result.invalid.append("array_metadata_geometry_mismatch")
        if observed_receiver.get("coordinate_frame") != "public_xyz_y_up":
            result.invalid.append("array_metadata_coordinate_frame_mismatch")
        if "hrtf_id" in metadata or "hrtf_path" in metadata:
            result.invalid.append("array_metadata_fake_hrtf")
        summary = metadata.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}
        for source in object_entries(summary.get("sources")) or []:
            count = source.get("original_num_channels")
            if type(count) is not int or count < 1 or source.get("downmix_policy") != (
                "arithmetic_mean" if count > 1 else "none"
            ):
                result.invalid.append("array_source_downmix_policy_invalid")
    else:
        selected = [item for item in _receiver_irs(manifest) if item["hrtf_id"] == output_type]
        if len(selected) != 1:
            result.invalid.append("runtime_receiver_selection_invalid")
        else:
            if metadata.get("hrtf_id") != output_type:
                result.invalid.append("render_metadata_hrtf_id_mismatch")
            if not _path_equal(selected[0].get("hrtf_path"), metadata.get("hrtf_path")):
                result.invalid.append("render_metadata_hrtf_path_mismatch")
        summary = metadata.get("summary", {})
        if isinstance(summary, dict) and "hrtf_id" in summary and summary["hrtf_id"] != metadata.get("hrtf_id"):
            result.invalid.append("render_metadata_hrtf_id_contradictory")
    seed, seed_source = effective_seed(manifest)
    if not _matches_value(seed, metadata.get("effective_seed")) or metadata.get("seed_source") != seed_source:
        result.invalid.append("render_metadata_effective_seed_mismatch")


def validate_noise(result: RenderValidation, manifest: dict[str, Any]) -> None:
    if result.metadata is None:
        return
    summary = result.metadata.get("summary")
    if not isinstance(summary, dict):
        return
    config = manifest.get("background_noise") or {}
    if not isinstance(config, dict):
        result.invalid.append("runtime_background_noise_invalid")
        return
    enabled = bool(_optional(config.get("enabled"), False))
    observed = summary.get("background_noise")
    if not isinstance(observed, dict) or type(observed.get("enabled")) is not bool or observed["enabled"] != enabled:
        result.invalid.append("render_metadata_background_noise_mismatch")
        return
    if not enabled:
        if observed.get("applied") is not False or observed.get("skipped_reason") != "disabled":
            result.invalid.append("background_noise_disabled_inconsistent")
        return
    expected_layers = object_entries(config.get("layers"))
    layers = object_entries(observed.get("layers"))
    if expected_layers is None or layers is None or not expected_layers or len(expected_layers) != len(layers):
        result.invalid.append("background_noise_layer_inventory_mismatch")
        return
    base_seed, _ = effective_seed(manifest)
    if base_seed != [] and not is_finite_number(base_seed):
        result.invalid.append("runtime_effective_seed_invalid")
        return
    applied = []
    for index, (expected, layer) in enumerate(zip(expected_layers, layers, strict=True)):
        prefix = f"background_noise_layer_{index + 1}"
        raw_strategy = expected.get("strategy", "")
        strategy = raw_strategy.strip().lower() if isinstance(raw_strategy, str) else ""
        strategy = "audio_file" if strategy == "audio_folder" else strategy
        if strategy not in ("colored", "audio_file") or layer.get("strategy") != strategy or layer.get("strategy_original") != raw_strategy:
            result.invalid.append(f"{prefix}_strategy_mismatch")
        if not numeric_equal(expected.get("snr_db"), layer.get("snr_db")):
            result.invalid.append(f"{prefix}_snr_mismatch")
        seed = _optional(expected.get("seed"), [])
        effective = seed if seed != [] else (base_seed + index if base_seed != [] else index + 1)
        if not _matches_value(seed, layer.get("seed")) or not numeric_equal(effective, layer.get("effective_seed")):
            result.invalid.append(f"{prefix}_seed_mismatch")
        if strategy == "colored":
            if layer.get("color") != str(expected.get("color", "")).strip().lower():
                result.invalid.append(f"{prefix}_color_mismatch")
        elif not _path_equal(expected.get("path"), layer.get("path")):
            result.invalid.append(f"{prefix}_path_mismatch")
        if result.wav_info is not None:
            for metadata_field, wav_field in (("target_num_samples", "num_samples"), ("target_num_channels", "num_channels")):
                if type(layer.get(metadata_field)) is not int or layer[metadata_field] != result.wav_info[wav_field]:
                    result.invalid.append(f"{prefix}_{metadata_field}_mismatch")
        channels = layer.get("source_num_channels")
        target_channels = layer.get("target_num_channels")
        expected_mode = "mono_replicated" if channels == 1 and is_finite_number(target_channels) and target_channels > 1 else "channel_to_channel"
        if type(channels) is not int or channels < 1 or channels not in (1, target_channels) or layer.get("channel_mode") != expected_mode:
            result.invalid.append(f"{prefix}_channel_mode_invalid")
        source_samples, target_samples = layer.get("source_num_samples"), layer.get("target_num_samples")
        if type(source_samples) is not int or source_samples <= 0 or type(target_samples) is not int or target_samples <= 0:
            result.invalid.append(f"{prefix}_duration_mode_invalid")
        else:
            mode = "loop" if source_samples < target_samples else "trim" if source_samples > target_samples else "exact"
            if layer.get("duration_mode") != mode:
                result.invalid.append(f"{prefix}_duration_mode_invalid")
        skipped = layer.get("skipped")
        if type(skipped) is not bool or type(layer.get("applied")) is not bool or layer["applied"] is skipped:
            result.invalid.append(f"{prefix}_applied_inconsistent")
        elif skipped and layer.get("skipped_reason") not in ("silent_signal", "silent_noise"):
            result.invalid.append(f"{prefix}_skip_reason_invalid")
        applied.append(layer.get("applied") is True)
    if observed.get("applied") is not any(applied) or (not any(applied) and observed.get("skipped_reason") != "all_layers_skipped"):
        result.invalid.append("background_noise_applied_inconsistent")


def validate_t30(result: RenderValidation) -> None:
    """Canonical acoustic gate, distinct from unreadable/corrupted artifacts."""
    if result.metadata is None:
        return
    summary = result.metadata.get("summary")
    room = summary.get("room") if isinstance(summary, dict) else None
    reverberation = room.get("reverberation") if isinstance(room, dict) else None
    if not isinstance(reverberation, dict) or "t30_s" not in reverberation:
        result.acoustic_rejection = {"reason": "raven_t30_missing", "invalid_bands": list(RAVEN_OCTAVE_FREQUENCIES_HZ)}
        return
    values = reverberation["t30_s"]
    if not isinstance(values, list) or len(values) != len(RAVEN_OCTAVE_FREQUENCIES_HZ):
        result.acoustic_rejection = {
            "reason": "raven_t30_band_count", "observed_band_count": len(values) if isinstance(values, list) else None,
            "expected_band_count": len(RAVEN_OCTAVE_FREQUENCIES_HZ), "invalid_bands": list(RAVEN_OCTAVE_FREQUENCIES_HZ),
        }
        return
    valid = [is_finite_number(value) and value > 0 for value in values]
    if not all(valid):
        result.acoustic_rejection = {
            "reason": "raven_t30_non_finite_or_non_positive",
            "invalid_bands": [{"frequency_hz": frequency, "value": value if value is None or isinstance(value, (str, bool))
                               or is_finite_number(value) else str(value)}
                              for frequency, value, ok in zip(RAVEN_OCTAVE_FREQUENCIES_HZ, values, valid, strict=True) if not ok],
        }
    expected = {
        "metric": "T30", "estimated_from": "room", "method": "rpf.getT30",
        "band_resolution": "octave", "frequency_mapping": "center_frequency",
        "band_frequencies_hz": list(RAVEN_OCTAVE_FREQUENCIES_HZ), "valid_band_count": sum(valid),
        "status": "estimated" if all(valid) else "unavailable",
        "mean_t30_s": sum(values) / len(values) if all(valid) else [],
    }
    for field_name, value in expected.items():
        if not _matches_value(value, reverberation.get(field_name)):
            result.invalid.append(f"render_metadata_t30_{field_name}_inconsistent")


def validate_render(
    paths: Mapping[str, Any], *, metadata_required: bool, profile: ValidationProfile = "normal",
    canonical: bool = False, manifest_path: Path | None = None,
) -> RenderValidation:
    """Evaluate disk artifacts against the runtime input, never a process log."""
    required = requires_metadata(metadata_required=metadata_required, canonical=canonical, profile=profile)
    result = RenderValidation()
    try:
        manifest = read_json_object(manifest_path or Path(paths["runtime_manifest_path"]))
    except FileNotFoundError:
        result.missing.append("runtime_manifest_missing")
        return result
    except (ValueError, UnicodeError) as exc:
        result.invalid.append(f"runtime_manifest_invalid: {exc}")
        return result
    except OSError as exc:
        result.unverifiable.append(f"runtime_manifest_read_error: {exc}")
        return result
    render = manifest.get("render")
    receiver = manifest.get("receiver")
    if not isinstance(render, dict) or not isinstance(receiver, dict):
        result.invalid.append("runtime_structure_invalid")
        return result
    output_type = paths["output_type"]
    if manifest.get("scene_id") != paths["scene_id"] or manifest.get("scene_type") != "static":
        result.invalid.append("runtime_scene_identity_mismatch")
    if manifest.get("schema_version", "1.0") not in ("1.0", "2.0", "3.0"):
        result.invalid.append("runtime_schema_unsupported")
    selected = render.get("active_output_type")
    if selected is None:
        irs = _receiver_irs(manifest)
        if len(irs) == 1 and not receiver.get("circular_array"):
            selected = irs[0]["hrtf_id"]
        elif not irs and receiver.get("circular_array"):
            selected = "circular_array"
        manifest = {**manifest, "render": {**render, "active_output_type": selected}}
        render = manifest["render"]
    if selected != output_type:
        result.invalid.append("runtime_output_selection_mismatch")
    if not _path_equal(render.get("output_wav_path"), paths["rendered_wav_path"]):
        result.invalid.append("runtime_output_wav_path_mismatch")
    if paths.get("render_metadata_path") and not _path_equal(render.get("output_metadata_path"), paths["render_metadata_path"]):
        result.invalid.append("runtime_output_metadata_path_mismatch")
    if render.get("sample_rate_hz") != 44100 or type(render.get("sample_rate_hz")) is not int:
        result.invalid.append("runtime_sample_rate_invalid")
    sources = object_entries(manifest.get("sources"))
    if not sources:
        result.invalid.append("runtime_sources_invalid")
    else:
        ids = [source.get("source_id") for source in sources]
        if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
            result.invalid.append("runtime_source_identity_invalid")
        for source in sources:
            try:
                _source_pose(source, manifest.get("schema_version", "1.0"))
                if not isinstance(source.get("audio_path"), str) or not source["audio_path"]:
                    raise ValueError("source audio_path missing")
                if not is_finite_number(_optional(source.get("gain_db"), 0)) or not is_finite_number(_optional(source.get("start_time_s"), 0)):
                    raise ValueError("source timing or gain invalid")
            except ValueError as exc:
                result.invalid.append(f"runtime_source_invalid: {exc}")
    if output_type == "circular_array":
        array = receiver.get("circular_array")
        expected_array = paths.get("circular_array")
        if not isinstance(array, dict) or not isinstance(expected_array, dict) or not _matches_value(expected_array, array) or receiver.get("hrtfs"):
            result.invalid.append("array_runtime_geometry_mismatch")
            return result
        channels = expected_array.get("num_microphones")
        if type(channels) is not int or channels < 2:
            result.invalid.append("array_runtime_channel_count_invalid")
            return result
    else:
        irs = _receiver_irs(manifest)
        chosen = [item for item in irs if item["hrtf_id"] == selected]
        if len(chosen) != 1 or (paths.get("receiver_ir_path") and not _path_equal(chosen[0].get("hrtf_path"), paths["receiver_ir_path"])):
            result.invalid.append("runtime_receiver_ir_mismatch")
        channels = 2
    wav = validate_wav(Path(paths["rendered_wav_path"]), expected_channels=channels,
                       sample_rate_hz=render.get("sample_rate_hz", 44100))
    result.missing.extend(wav.missing)
    result.invalid.extend(wav.invalid)
    result.unverifiable.extend(wav.unverifiable)
    result.wav_info = wav.wav_info
    metadata_path = paths.get("render_metadata_path")
    if metadata_path:
        read_render_metadata(result, Path(metadata_path), manifest, required=required)
    elif required:
        result.missing.append("render_metadata_missing")
    validate_dimensions(result, render, None)
    validate_sources(result, manifest)
    validate_receiver_and_seed(result, manifest)
    validate_noise(result, manifest)
    if canonical:
        validate_t30(result)
    return result


def equivalent_render_inputs(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    """Compare runtime semantics, ignoring sampling/batch bookkeeping only."""
    def normalize(manifest: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(manifest)
        for name in ("sampling", "reverberation_batch", "reverberation_sampling"):
            payload.pop(name, None)
        payload.setdefault("schema_version", "1.0")
        render = payload.get("render")
        if not isinstance(render, dict):
            return payload
        seed, _ = effective_seed(payload)
        payload.pop("seed", None)
        for name in ("outputs", "receiver_variants", "effective_seed", "seed_source"):
            render.pop(name, None)
        render["seed"] = seed
        render["trim_reverb_tail"] = _optional(render.get("trim_reverb_tail"), False)
        render["target_duration_s"] = _optional(render.get("target_duration_s"), None)
        noise = payload.get("background_noise")
        if not noise or isinstance(noise, dict) and not noise.get("enabled"):
            payload["background_noise"] = {"enabled": False, "layers": []}
        return payload

    def match(left: Any, right: Any, key: str = "") -> bool:
        if isinstance(left, dict):
            return isinstance(right, dict) and left.keys() == right.keys() and all(
                match(value, right[name], name) for name, value in left.items()
            )
        if isinstance(left, list):
            return isinstance(right, list) and len(left) == len(right) and all(
                match(a, b, key) for a, b in zip(left, right, strict=True)
            )
        if key.endswith("_path") or key == "base_rpf_file":
            return left == right if not left and not right else _path_equal(left, right)
        if type(left) in (int, float):
            return numeric_equal(left, right)
        return type(left) is type(right) and left == right

    return match(normalize(expected), normalize(observed))
