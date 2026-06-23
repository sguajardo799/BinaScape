from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class AnalysisData:
    run_dir: Path
    render_scenes: list[JsonObject]
    render_sources: list[JsonObject]
    degraded_records: list[JsonObject]
    render_index: list[JsonObject] = field(default_factory=list)
    clarity_index: list[JsonObject] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_run(run_dir: str | Path) -> AnalysisData:
    run_path = Path(run_dir).resolve()
    warnings: list[str] = []
    scene_manifests = _load_scene_manifests(run_path, warnings)
    render_index = _read_jsonl(run_path / "indexes" / "render_index.jsonl", warnings)
    clarity_index = _read_jsonl(run_path / "indexes" / "clarity_index.jsonl", warnings)

    render_scenes: list[JsonObject] = []
    render_sources: list[JsonObject] = []
    for metadata_path in sorted((run_path / "outputs" / "render").glob("**/*__render.json")):
        metadata = _read_json(metadata_path, warnings)
        if not metadata:
            continue
        scene_id = str(metadata.get("scene_id") or _scene_id_from_render_path(metadata_path))
        manifest = scene_manifests.get(scene_id, {})
        scene_record = _render_scene_record(metadata, manifest, metadata_path, run_path)
        render_scenes.append(scene_record)
        render_sources.extend(_source_records(scene_record, metadata, manifest))

    degraded_records = []
    for metadata_path in sorted((run_path / "outputs" / "degraded").glob("**/*.json")):
        metadata = _read_json(metadata_path, warnings)
        if metadata:
            degraded_records.append(_degraded_record(metadata, metadata_path, run_path))

    return AnalysisData(
        run_dir=run_path,
        render_scenes=render_scenes,
        render_sources=render_sources,
        degraded_records=degraded_records,
        render_index=render_index,
        clarity_index=clarity_index,
        warnings=warnings,
    )


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_csv(path: Path, records: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for record in records for key in record if _csv_scalar(record[key])})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({key: record.get(key) for key in fieldnames})


def summary_for(data: AnalysisData) -> JsonObject:
    source_types = _counts(record.get("source_type") for record in data.render_sources)
    hrtfs = _counts(record.get("employed_hrtf") for record in data.render_scenes)
    degraded_profiles = _counts(record.get("hearing_profile_id") for record in data.degraded_records)
    degraded_statuses = _counts(record.get("status") for record in data.degraded_records)
    return {
        "run_dir": str(data.run_dir),
        "render_scene_count": len(data.render_scenes),
        "render_source_count": len(data.render_sources),
        "degraded_metadata_count": len(data.degraded_records),
        "render_index_count": len(data.render_index),
        "clarity_index_count": len(data.clarity_index),
        "source_types": source_types,
        "employed_hrtfs": hrtfs,
        "degraded_profiles": degraded_profiles,
        "degraded_statuses": degraded_statuses,
        "warnings": data.warnings,
    }


def _render_scene_record(
    metadata: JsonObject,
    manifest: JsonObject,
    metadata_path: Path,
    run_path: Path,
) -> JsonObject:
    scene_id = str(metadata.get("scene_id") or manifest.get("scene_id") or "")
    hrtf_path = metadata.get("hrtf_path") or _first_hrtf_path(manifest)
    room_dimensions = _number_list(_nested(manifest, "room", "dimensions_m"))
    width = room_dimensions[0] if len(room_dimensions) >= 1 else None
    length = room_dimensions[1] if len(room_dimensions) >= 2 else None
    height = room_dimensions[2] if len(room_dimensions) >= 3 else None
    area = width * length if width is not None and length is not None else None
    reverb = _nested(metadata, "summary", "room", "reverberation", "mean_t30_s")
    if reverb is None:
        reverb = _nested(metadata, "batch", "room", "reverberation", "mean_t30_s")

    return {
        "scene_id": scene_id,
        "job_id": metadata.get("job_id") or manifest.get("job_id"),
        "scene_type": metadata.get("scene_type") or manifest.get("scene_type"),
        "variant_id": _variant_id(scene_id, metadata.get("hrtf_id")),
        "hrtf_id": metadata.get("hrtf_id") or _first_hrtf_id(manifest),
        "hrtf_path": hrtf_path,
        "employed_hrtf": Path(str(hrtf_path)).name if hrtf_path else metadata.get("hrtf_id"),
        "sample_rate_hz": metadata.get("sample_rate_hz") or _nested(manifest, "render", "sample_rate_hz"),
        "n_sources": _nested(metadata, "summary", "n_sources") or len(manifest.get("sources", [])),
        "room_width_m": width,
        "room_length_m": length,
        "room_height_m": height,
        "room_area_m2": area,
        "reverberation_time_s": _float_or_none(reverb),
        "receiver_x_m": _coordinate(manifest, "receiver", 0),
        "receiver_y_m": _coordinate(manifest, "receiver", 1),
        "receiver_z_m": _coordinate(manifest, "receiver", 2),
        "receiver_yaw_deg": _float_or_none(_nested(manifest, "receiver", "orientation_deg", "yaw")),
        "rendered_wav_path": metadata.get("output_wav_path") or _nested(manifest, "render", "output_wav_path"),
        "render_metadata_path": _relative_or_absolute(metadata_path, run_path),
        "background_noise_applied": _nested(metadata, "summary", "background_noise", "applied"),
    }


def _source_records(scene_record: JsonObject, metadata: JsonObject, manifest: JsonObject) -> list[JsonObject]:
    manifest_sources = {str(source.get("source_id")): source for source in manifest.get("sources", [])}
    metadata_sources = _nested(metadata, "summary", "sources") or _nested(metadata, "batch", "sources") or []
    source_ids = list(manifest_sources)
    for source in metadata_sources:
        source_id = str(source.get("source_id") or "")
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)

    records = []
    receiver = _number_list(_nested(manifest, "receiver", "position_m"))
    receiver_yaw = _float_or_none(_nested(manifest, "receiver", "orientation_deg", "yaw")) or 0.0
    metadata_by_id = {str(source.get("source_id")): source for source in metadata_sources}

    for source_id in source_ids:
        source = manifest_sources.get(source_id, {})
        render_source = metadata_by_id.get(source_id, {})
        position = _number_list(source.get("position_m") or render_source.get("position_m"))
        distance = _distance(receiver, position)
        global_doa = _azimuth(receiver, position)
        relative_doa = _wrap_degrees(global_doa - receiver_yaw) if global_doa is not None else None
        elevation = _elevation(receiver, position)
        source_type = source.get("event_type") or render_source.get("event_type") or "unknown"

        records.append(
            {
                "scene_id": scene_record.get("scene_id"),
                "variant_id": scene_record.get("variant_id"),
                "hrtf_id": scene_record.get("hrtf_id"),
                "employed_hrtf": scene_record.get("employed_hrtf"),
                "source_id": source_id,
                "source_type": source_type,
                "audio_path": source.get("audio_path") or render_source.get("audio_path"),
                "audio_file": Path(str(source.get("audio_path") or render_source.get("audio_path") or "")).name,
                "has_directivity": bool(source.get("directivity_path") or render_source.get("directivity_path")),
                "source_x_m": position[0] if len(position) >= 1 else None,
                "source_y_m": position[1] if len(position) >= 2 else None,
                "source_z_m": position[2] if len(position) >= 3 else None,
                "distance_m": distance,
                "global_doa_deg": global_doa,
                "receiver_relative_doa_deg": relative_doa,
                "elevation_deg": elevation,
                "gain_db": _float_or_none(source.get("gain_db") or render_source.get("gain_db")),
                "start_time_s": _float_or_none(source.get("start_time_s") or render_source.get("source_start_time_s")),
                "room_width_m": scene_record.get("room_width_m"),
                "room_length_m": scene_record.get("room_length_m"),
                "room_area_m2": scene_record.get("room_area_m2"),
                "reverberation_time_s": scene_record.get("reverberation_time_s"),
                "receiver_x_m": scene_record.get("receiver_x_m"),
                "receiver_y_m": scene_record.get("receiver_y_m"),
                "receiver_z_m": scene_record.get("receiver_z_m"),
                "receiver_yaw_deg": scene_record.get("receiver_yaw_deg"),
            }
        )
    return records


def _degraded_record(metadata: JsonObject, metadata_path: Path, run_path: Path) -> JsonObject:
    left_loss = _loss_values(metadata, "left")
    right_loss = _loss_values(metadata, "right")
    left_mean = _mean(left_loss)
    right_mean = _mean(right_loss)
    return {
        "scene_id": metadata.get("scene_id"),
        "variant_id": metadata.get("variant_id"),
        "job_id": metadata.get("job_id"),
        "output_type": metadata.get("output_type"),
        "hearing_profile_id": metadata.get("hearing_profile_id"),
        "status": metadata.get("status"),
        "processor_name": _nested(metadata, "processor", "name"),
        "processor_package": _nested(metadata, "processor", "package"),
        "processor_package_version": _nested(metadata, "processor", "package_version"),
        "sample_rate_hz": _nested(metadata, "processor", "sample_rate_hz"),
        "channels": _nested(metadata, "processor", "channels"),
        "left_mean_loss_db": left_mean,
        "right_mean_loss_db": right_mean,
        "mean_loss_db": _mean([value for value in [left_mean, right_mean] if value is not None]),
        "ear_asymmetry_db": abs(left_mean - right_mean) if left_mean is not None and right_mean is not None else None,
        "input_render_metadata_path": metadata.get("input_render_metadata_path"),
        "input_wav_path": metadata.get("input_wav_path"),
        "output_wav_path": metadata.get("output_wav_path"),
        "degraded_metadata_path": _relative_or_absolute(metadata_path, run_path),
    }


def _load_scene_manifests(run_path: Path, warnings: list[str]) -> dict[str, JsonObject]:
    manifests = {}
    for path in sorted((run_path / "manifests" / "scene").glob("*.json")):
        data = _read_json(path, warnings)
        scene_id = data.get("scene_id") if data else None
        if scene_id:
            manifests[str(scene_id)] = data
    return manifests


def _read_json(path: Path, warnings: list[str]) -> JsonObject:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        warnings.append(f"Missing file: {path}")
    except json.JSONDecodeError as exc:
        warnings.append(f"Invalid JSON in {path}: {exc}")
    return {}


def _read_jsonl(path: Path, warnings: list[str]) -> list[JsonObject]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                warnings.append(f"Invalid JSONL row in {path}:{line_number}: {exc}")
    return records


def _nested(data: JsonObject, *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _number_list(value: Any) -> list[float]:
    if not isinstance(value, list | tuple):
        return []
    numbers = []
    for item in value:
        number = _float_or_none(item)
        if number is not None:
            numbers.append(number)
    return numbers


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _coordinate(manifest: JsonObject, section: str, index: int) -> float | None:
    values = _number_list(_nested(manifest, section, "position_m"))
    return values[index] if len(values) > index else None


def _distance(receiver: list[float], source: list[float]) -> float | None:
    if len(receiver) < 3 or len(source) < 3:
        return None
    return math.dist(receiver[:3], source[:3])


def _azimuth(receiver: list[float], source: list[float]) -> float | None:
    if len(receiver) < 3 or len(source) < 3:
        return None
    dx = source[0] - receiver[0]
    dz = source[2] - receiver[2]
    return _wrap_degrees(math.degrees(math.atan2(dx, dz)))


def _elevation(receiver: list[float], source: list[float]) -> float | None:
    if len(receiver) < 3 or len(source) < 3:
        return None
    dx = source[0] - receiver[0]
    dy = source[1] - receiver[1]
    dz = source[2] - receiver[2]
    horizontal = math.hypot(dx, dz)
    return math.degrees(math.atan2(dy, horizontal))


def _wrap_degrees(value: float) -> float:
    return ((value + 180.0) % 360.0) - 180.0


def _loss_values(metadata: JsonObject, ear: str) -> list[float]:
    values = _nested(metadata, "degradation_applied", ear, "loss_db_by_band")
    if not isinstance(values, dict):
        return []
    return [number for number in (_float_or_none(value) for value in values.values()) if number is not None]


def _mean(values: list[float | None]) -> float | None:
    finite = [value for value in values if value is not None and math.isfinite(value)]
    return sum(finite) / len(finite) if finite else None


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if value is None or value == "":
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _first_hrtf_id(manifest: JsonObject) -> str | None:
    hrtfs = _nested(manifest, "receiver", "hrtfs")
    if isinstance(hrtfs, list) and hrtfs:
        return hrtfs[0].get("hrtf_id")
    return None


def _first_hrtf_path(manifest: JsonObject) -> str | None:
    hrtfs = _nested(manifest, "receiver", "hrtfs")
    if isinstance(hrtfs, list) and hrtfs:
        return hrtfs[0].get("hrtf_path")
    return None


def _variant_id(scene_id: str, hrtf_id: Any) -> str:
    return f"{scene_id}__{hrtf_id}" if scene_id and hrtf_id else scene_id


def _scene_id_from_render_path(path: Path) -> str:
    return path.name.split("__", maxsplit=1)[0]


def _relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _csv_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)
