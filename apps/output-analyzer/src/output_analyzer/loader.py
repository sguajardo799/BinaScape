from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field as data_field
from pathlib import Path
from itertools import combinations
from statistics import pstdev
from typing import Any

JsonObject = dict[str, Any]
SCHEMA_VERSION = "3.0"
TOLERANCE = 1e-8
RT30_ABS_TOL_S = 1e-6
RT30_REL_TOL = 1e-6


@dataclass
class AnalysisData:
    run_dir: Path
    render_scenes: list[JsonObject] = data_field(default_factory=list)
    render_sources: list[JsonObject] = data_field(default_factory=list)
    degraded_records: list[JsonObject] = data_field(default_factory=list)
    scenes: list[JsonObject] = data_field(default_factory=list)
    sources: list[JsonObject] = data_field(default_factory=list)
    render_index: list[JsonObject] = data_field(default_factory=list)
    clarity_index: list[JsonObject] = data_field(default_factory=list)
    incidents: list[JsonObject] = data_field(default_factory=list)
    rt30_pairs: list[JsonObject] = data_field(default_factory=list)
    rt30_comparisons: list[JsonObject] = data_field(default_factory=list)
    rt30_consolidated: list[JsonObject] = data_field(default_factory=list)
    rt30_records: list[JsonObject] = data_field(default_factory=list)
    materials: list[JsonObject] = data_field(default_factory=list)
    sampling_records: list[JsonObject] = data_field(default_factory=list)
    sampling_batches: list[JsonObject] = data_field(default_factory=list)
    sampling_failures: list[JsonObject] = data_field(default_factory=list)
    coverage: list[JsonObject] = data_field(default_factory=list)
    general_parameters: JsonObject = data_field(default_factory=dict)
    warnings: list[str] = data_field(default_factory=list)


class Reader:
    def __init__(self, data):
        self.data = data
        self.seen = set()
        self.versions = Counter()

    def issue(
        self,
        category,
        entity,
        field="",
        path="",
        detail="",
        *,
        cause_id=None,
        evidence=None,
    ):
        key = (category, str(entity), field)
        incident_id = json.dumps(key, separators=(",", ":"))
        if key not in self.seen:
            self.seen.add(key)
            self.data.incidents.append(
                dict(
                    incident_id=incident_id,
                    cause_id=cause_id,
                    evidence=evidence,
                    category=category,
                    entity=str(entity),
                    field=field,
                    path=str(path),
                    detail=detail,
                )
            )

        return incident_id

    def value(self, value, entity, field, path="", positive=False):
        number = _number(value)
        if number is None or (positive and number <= 0):
            self.issue(
                "missing_required_field" if value is None else "malformed_field",
                entity,
                field,
                path,
            )
            return None
        return number

    def identifier(self, value, entity, field, path=""):
        if not isinstance(value, str) or not value.strip():
            self.issue(
                "missing_required_field" if value is None else "malformed_field",
                entity,
                field,
                path,
            )
            return None
        return value

    def read(self, path, schema=None):
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError, OSError) as exc:
            self.issue("invalid_json", path, path=path, detail=str(exc))
            return None
        if not isinstance(doc, dict):
            self.issue("invalid_json", path, path=path, detail="Root must be an object")
            return None
        doc = self.sanitize(doc, str(path), str(path))
        if schema:
            version = doc.get("schema_version")
            self.versions[str(version)] += 1
            if version != schema:
                self.issue(
                    "missing_required_field"
                    if version is None
                    else "unsupported_schema",
                    path,
                    "schema_version",
                    path,
                    f"Expected {schema}; observed {version}",
                )
                return None
        return doc

    def sanitize(self, value, entity, path, field=""):
        if isinstance(value, float) and not math.isfinite(value):
            self.issue("malformed_field", entity, field, path, "Nonfinite number")
            return None
        if isinstance(value, dict):
            return {
                key: self.sanitize(item, entity, path, f"{field}.{key}")
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self.sanitize(item, entity, path, f"{field}[{i}]")
                for i, item in enumerate(value)
            ]
        return value

    def index(self, path):
        if not path.exists():
            self.issue("missing_optional_index", path.name, path=path)
            return []
        rows = []
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (UnicodeError, OSError) as exc:
            self.issue("invalid_index", path.name, path=path, detail=str(exc))
            return []
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                doc = json.loads(line)
                if not isinstance(doc, dict):
                    raise ValueError("Root must be an object")
                rows.append(self.sanitize(doc, f"{path}:{number}", str(path)))
            except ValueError as exc:
                self.issue(
                    "invalid_jsonl", f"{path}:{number}", path=path, detail=str(exc)
                )
        return rows

    def entities(self, value, entity, field, path, singleton=False):
        if singleton and isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            self.issue(
                "missing_required_field" if value is None else "malformed_field",
                entity,
                field,
                path,
            )
            return []
        result = []
        for index, item in enumerate(value):
            if isinstance(item, dict):
                result.append(item)
            else:
                self.issue("malformed_field", entity, f"{field}[{index}]", path)
        return result


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _nested(doc, *keys):
    for key in keys:
        if not isinstance(doc, dict):
            return None
        doc = doc.get(key)
    return doc


def _vector(value, size=3):
    if (
        not isinstance(value, list)
        or len(value) != size
        or any(_number(v) is None for v in value)
    ):
        return None
    return [float(v) for v in value]


def _same(a, b):
    if (
        isinstance(a, (int, float))
        and not isinstance(a, bool)
        and isinstance(b, (int, float))
        and not isinstance(b, bool)
    ):
        return math.isclose(a, b, rel_tol=TOLERANCE, abs_tol=TOLERANCE)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _merge(records, reader, identity):
    """Retain matching fields; exclude only conflicting fields, with provenance."""
    merged = dict(records[0])
    if len(records) == 1:
        return merged
    reader.issue(
        "duplicate_identity", identity, path=records[0].get("metadata_path", "")
    )

    def merge_values(values, field):
        if all(_same(values[0], value) for value in values[1:]):
            return values[0]
        if all(isinstance(value, list) for value in values) and field == "sources":
            if all(
                isinstance(item, dict) and isinstance(item.get("source_id"), str)
                for value in values
                for item in value
            ):
                identities = sorted(
                    {item["source_id"] for value in values for item in value}
                )
                return [
                    merge_values(
                        [
                            next(
                                (item for item in value if item["source_id"] == sid), {}
                            )
                            for value in values
                        ],
                        f"{field}.{sid}",
                    )
                    for sid in identities
                ]
        if all(isinstance(value, dict) for value in values):
            return {
                key: merge_values([v.get(key) for v in values], f"{field}.{key}")
                for key in set().union(*(v.keys() for v in values))
            }
        reader.issue(
            "conflicting_identity", identity, field, records[0].get("metadata_path", "")
        )
        return None

    for key in set().union(*(r.keys() for r in records)):
        if key not in {"metadata_path", "provenance"}:
            merged[key] = merge_values([r.get(key) for r in records], key)
    merged["provenance"] = sorted(
        set(p for r in records for p in r.get("provenance", []))
    )
    return merged


def _group_documents(paths, reader, kind, schema, keys):
    groups = defaultdict(list)
    for path in paths:
        doc = reader.read(path, schema)
        if doc is None:
            continue
        identity = [reader.identifier(doc.get(k), str(path), k, path) for k in keys]
        if any(v is None for v in identity):
            continue
        doc["metadata_path"] = str(path)
        doc["provenance"] = [str(path)]
        groups[tuple(identity)].append(doc)
    return {
        key: _merge(rows, reader, f"{kind}:{'/'.join(key)}")
        for key, rows in groups.items()
    }


def _segment_distance(p, a, b):
    dx, dz = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dz * dz
    t = (
        max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / length2))
        if length2
        else 0
    )
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz)


def _inside(p, vertices):
    inside = False
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        if _segment_distance(p, a, b) <= TOLERANCE:
            return True
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (
            b[1] - a[1]
        ) + a[0]:
            inside = not inside
    return inside


def _geometry(doc, reader, entity):
    geometry = _nested(doc, "room", "geometry")
    result = dict(
        geometry_type=None,
        footprint_vertices_m=None,
        wall_ids=None,
        room_height_m=None,
        room_length_m=None,
        room_width_m=None,
        room_area_m2=None,
        room_volume_m3=None,
    )
    if not isinstance(geometry, dict):
        reader.issue("invalid_geometry", entity, "room.geometry", doc["metadata_path"])
        return result
    kind = geometry.get("type")
    raw = geometry.get("footprint_vertices_m")
    vertices = [_vector(v, 2) for v in raw] if isinstance(raw, list) else []
    height = reader.value(
        geometry.get("height_m"),
        entity,
        "height_m",
        doc["metadata_path"],
        positive=True,
    )
    walls = geometry.get("wall_ids")
    valid_walls = (
        isinstance(walls, list)
        and len(walls) == len(vertices)
        and all(isinstance(w, str) and w for w in walls)
        and len(set(walls)) == len(walls)
    )
    if (
        not isinstance(kind, str)
        or kind not in {"shoebox", "l_shape", "trapezoid"}
        or len(vertices) < 3
        or any(v is None for v in vertices)
        or not valid_walls
    ):
        reader.issue("invalid_geometry", entity, "room.geometry", doc["metadata_path"])
        return result
    area = (
        abs(
            sum(
                a[0] * b[1] - b[0] * a[1]
                for a, b in zip(vertices, vertices[1:] + vertices[:1])
            )
        )
        / 2
    )

    # Reject degenerate edges and crossing non-neighbouring edges.
    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    edges = list(zip(vertices, vertices[1:] + vertices[:1]))
    crossing = any(
        (cross(a, b, c) * cross(a, b, d) < 0 and cross(c, d, a) * cross(c, d, b) < 0)
        or min(
            _segment_distance(c, a, b),
            _segment_distance(d, a, b),
            _segment_distance(a, c, d),
            _segment_distance(b, c, d),
        )
        <= TOLERANCE
        for i, (a, b) in enumerate(edges)
        for j, (c, d) in enumerate(edges)
        if j > i + 1 and not (i == 0 and j == len(edges) - 1)
    )
    if (
        area <= TOLERANCE
        or crossing
        or not math.isfinite(area)
        or any(math.dist(a, b) <= TOLERANCE for a, b in edges)
    ):
        reader.issue(
            "invalid_geometry", entity, "footprint_vertices_m", doc["metadata_path"]
        )
        return result
    result.update(
        geometry_type=kind,
        footprint_vertices_m=vertices,
        wall_ids=walls,
        room_height_m=height,
        room_length_m=max(v[0] for v in vertices) - min(v[0] for v in vertices),
        room_width_m=max(v[1] for v in vertices) - min(v[1] for v in vertices),
        room_area_m2=area,
        room_volume_m3=area * height if height else None,
    )
    return result


def _pose(value, reader, entity, field, path):
    pose = _vector(value)
    if pose is None:
        reader.issue("invalid_pose", entity, field, path)
    return pose


def _spatial(record, position, scene, reader, entity):
    vertices, height = scene.get("footprint_vertices_m"), scene.get("room_height_m")
    record.update(
        inside_footprint=None,
        inside_height=None,
        wall_distance_m=None,
        configured_margin_m=None,
        margin_comparison="unavailable: margin not emitted in current scene metadata",
    )
    if position is None:
        return
    if vertices:
        p = [position[0], position[2]]
        record["inside_footprint"] = _inside(p, vertices)
        record["wall_distance_m"] = min(
            _segment_distance(p, a, b)
            for a, b in zip(vertices, vertices[1:] + vertices[:1])
        )
        if not record["inside_footprint"]:
            reader.issue(
                "outside_footprint",
                entity,
                "position_m",
                record.get("metadata_path", ""),
            )
    if height is not None:
        record["inside_height"] = -TOLERANCE <= position[1] <= height + TOLERANCE
        if not record["inside_height"]:
            reader.issue(
                "outside_height", entity, "position_m", record.get("metadata_path", "")
            )


def _sources(doc, reader, entity, render=False):
    raw = _nested(doc, "summary", "sources") if render else doc.get("sources")
    groups = defaultdict(list)
    for source in reader.entities(
        raw,
        entity,
        "summary.sources" if render else "sources",
        doc["metadata_path"],
        singleton=render,
    ):
        sid = reader.identifier(
            source.get("source_id"), entity, "source_id", doc["metadata_path"]
        )
        if sid:
            groups[sid].append(source)
    return {
        sid: _merge(rows, reader, f"{entity}/source:{sid}")
        for sid, rows in groups.items()
    }


def _source_record(sid, source, scene, reader, provenance, raven=False):
    entity = f"source:{scene['scene_id']}/{sid}"
    position = _pose(source.get("position_m"), reader, entity, "position_m", provenance)
    if raven and position:
        position = [position[0], position[1], -position[2]]
    receiver = _vector(
        [
            scene.get("receiver_x_m"),
            scene.get("receiver_y_m"),
            scene.get("receiver_z_m"),
        ]
    )
    distance = math.dist(receiver, position) if receiver and position else None
    azimuth = elevation = relative = None
    if distance is not None and distance > TOLERANCE:
        dx, dy, dz = [position[i] - receiver[i] for i in range(3)]
        azimuth = (math.degrees(math.atan2(dx, dz)) + 180) % 360 - 180
        elevation = math.degrees(math.atan2(dy, math.hypot(dx, dz)))
        if scene.get("receiver_yaw_deg") is not None:
            relative = (azimuth - scene["receiver_yaw_deg"] + 180) % 360 - 180
    elif distance is not None:
        reader.issue("undefined_angles", entity, "position_m", provenance)
    event = reader.identifier(
        source.get("event_type"), entity, "event_type", provenance
    )
    record = dict(
        scene_id=scene["scene_id"],
        source_id=sid,
        source_type=event,
        position_provenance="RAVEN [x,y,z] -> public [x,y,-z]"
        if raven
        else "manifest public [x,y,z]",
        metadata_path=provenance,
        provenance=[provenance],
        geometry_type=scene.get("geometry_type"),
        distance_m=distance,
        global_doa_deg=azimuth,
        receiver_relative_doa_deg=relative,
        elevation_deg=elevation,
        gain_db=reader.value(source.get("gain_db"), entity, "gain_db", provenance),
        start_time_s=reader.value(
            source.get("source_start_time_s" if raven else "start_time_s"),
            entity,
            "start_time_s",
            provenance,
        ),
        audio_path=source.get("audio_path"),
        has_directivity=bool(source.get("directivity_path")),
    )
    for i, axis in enumerate("xyz"):
        record[f"source_{axis}_m"] = position[i] if position else None
    _spatial(record, position, scene, reader, entity)
    return record


def _bands(frequencies, values, reader, entity, path):
    result = {}
    if (
        not isinstance(frequencies, list)
        or not isinstance(values, list)
        or len(frequencies) != len(values)
    ):
        reader.issue("rt30_invalid_band", entity, "bands", path)
        return result
    repeated = set()
    for index, (f, v) in enumerate(zip(frequencies, values)):
        # JSON object frequency keys are strings by contract; sample values are numeric.
        try:
            frequency = float(f) if not isinstance(f, bool) else None
        except (ValueError, TypeError, OverflowError):
            frequency = None
        value = _number(v)
        if frequency is None or not math.isfinite(frequency) or frequency <= 0:
            reader.issue("rt30_invalid_band", entity, f"frequency[{index}]", path)
            continue
        if frequency in result or frequency in repeated:
            repeated.add(frequency)
            result.pop(frequency, None)
            reader.issue(
                "rt30_invalid_band",
                entity,
                str(frequency),
                path,
                "Ambiguous repeated frequency",
            )
        elif value is None or value <= 0:
            reader.issue(
                "rt30_invalid_band",
                entity,
                str(frequency),
                path,
                "Nonpositive or nonfinite T30",
            )
            repeated.add(frequency)
        else:
            result[frequency] = value
    return result


def _audited_bands(frequencies, values, valid):
    frequencies = frequencies if isinstance(frequencies, list) else []
    values = values if isinstance(values, list) else []
    rows = []
    for index in range(max(len(frequencies), len(values))):
        try:
            frequency = float(frequencies[index])
            if (
                not math.isfinite(frequency)
                or frequency <= 0
                or isinstance(frequencies[index], bool)
            ):
                frequency = None
        except (IndexError, ValueError, TypeError, OverflowError):
            frequency = None
        rows.append((index, frequency, valid.get(frequency)))
    return rows


def load_run(run_dir: str | Path) -> AnalysisData:
    run = Path(run_dir).resolve()
    if not run.is_dir():
        raise OSError(f"Run directory is not accessible: {run}")
    # Explicit directory access prevents an inaccessible root masquerading as empty data.
    list(run.iterdir())
    data = AnalysisData(run)
    reader = Reader(data)
    manifests = _group_documents(
        sorted((run / "metadata/manifests/scene").glob("*.json")),
        reader,
        "scene",
        "3.0",
        ["scene_id"],
    )
    renders = _group_documents(
        sorted((run / "output_audio/render").rglob("*__render.json")),
        reader,
        "variant",
        "3.0",
        ["scene_id", "hrtf_id"],
    )
    degraded = _group_documents(
        sorted((run / "output_audio/degraded").rglob("*.json")),
        reader,
        "degraded",
        "1.0",
        ["variant_id", "hearing_profile_id"],
    )
    indexes = run / "metadata/indexes"
    data.render_index = reader.index(indexes / "render_index.jsonl")
    data.clarity_index = reader.index(indexes / "clarity_index.jsonl")
    data.sampling_failures = reader.index(indexes / "scene_sampling_failures.jsonl")
    scene_map = {}
    source_map = {}
    sabine = {}
    sabine_expected = {}
    expected = set()
    batches = []
    for (scene_id,), doc in manifests.items():
        entity = f"scene:{scene_id}"
        scene = dict(
            scene_id=scene_id,
            metadata_path=doc["metadata_path"],
            provenance=doc["provenance"],
            **_geometry(doc, reader, entity),
        )
        pose = _pose(
            _nested(doc, "receiver", "position_m"),
            reader,
            entity,
            "receiver.position_m",
            doc["metadata_path"],
        )
        for i, axis in enumerate("xyz"):
            scene[f"receiver_{axis}_m"] = pose[i] if pose else None
        scene["receiver_yaw_deg"] = reader.value(
            _nested(doc, "receiver", "orientation_deg", "yaw"),
            entity,
            "receiver.orientation_deg.yaw",
            doc["metadata_path"],
        )
        scene["receiver_position_provenance"] = "manifest public [x,y,z]"
        _spatial(scene, pose, scene, reader, entity)
        sources = _sources(doc, reader, entity)
        scene["n_sources"] = (
            len(sources)
            if isinstance(doc.get("sources"), list)
            and all(
                isinstance(item, dict)
                and isinstance(item.get("source_id"), str)
                and item["source_id"].strip()
                for item in doc["sources"]
            )
            else None
        )
        scene_map[scene_id] = scene
        for sid, source in sources.items():
            source_map[(scene_id, sid)] = _source_record(
                sid, source, scene, reader, doc["metadata_path"]
            )
        hrtfs = reader.entities(
            _nested(doc, "receiver", "hrtfs"),
            entity,
            "receiver.hrtfs",
            doc["metadata_path"],
        )
        for hrtf in hrtfs:
            hid = reader.identifier(
                hrtf.get("hrtf_id"), entity, "hrtf_id", doc["metadata_path"]
            )
            if hid:
                expected.add((scene_id, hid))
        reverb = doc.get("reverberation_sampling")
        if not isinstance(reverb, dict):
            reader.issue(
                "missing_required_field",
                entity,
                "reverberation_sampling",
                doc["metadata_path"],
            )
            reverb = {}
        band_map = reverb.get("rt30_by_band_s")
        all_bands = _bands(
            list(band_map) if isinstance(band_map, dict) else None,
            list(band_map.values()) if isinstance(band_map, dict) else None,
            reader,
            entity,
            doc["metadata_path"],
        )
        selected = reverb.get("mean_bands_hz")
        selected = (
            {_number(f) for f in selected} if isinstance(selected, list) else set()
        )
        if not selected or None in selected:
            reader.issue(
                "rt30_not_comparable", entity, "mean_bands_hz", doc["metadata_path"]
            )
        sabine_expected[scene_id] = {f for f in selected if f is not None and f > 0}
        sabine[scene_id] = {f: v for f, v in all_bands.items() if f in selected}
        for band_index, f, v in _audited_bands(
            list(band_map) if isinstance(band_map, dict) else None,
            list(band_map.values()) if isinstance(band_map, dict) else None,
            all_bands,
        ):
            data.rt30_records.append(
                dict(
                    scene_id=scene_id,
                    variant_id=None,
                    method="Sabine estimate",
                    band_index=band_index,
                    frequency_hz=f,
                    value_s=v,
                    eligible_for_comparison=f in selected and v is not None,
                    metadata_path=doc["metadata_path"],
                )
            )
        data.sampling_records.append(
            dict(
                scene_id=scene_id,
                attempts=reader.value(
                    reverb.get("attempts"),
                    entity,
                    "reverberation_sampling.attempts",
                    doc["metadata_path"],
                ),
                scene_attempt=reader.value(
                    _nested(doc, "sampling", "scene_attempt"),
                    entity,
                    "sampling.scene_attempt",
                    doc["metadata_path"],
                ),
                receiver_attempt=reader.value(
                    _nested(doc, "sampling", "receiver_attempt"),
                    entity,
                    "sampling.receiver_attempt",
                    doc["metadata_path"],
                ),
                requested_bin=_nested(reverb, "requested_bin", "index"),
                obtained_bin=_nested(reverb, "obtained_bin", "index"),
                matched_requested_bin=reverb.get("matched_requested_bin"),
                fallback_used=reverb.get("fallback_used"),
                reverberation_sampling=reverb,
                sampling=doc.get("sampling"),
                metadata_path=doc["metadata_path"],
            )
        )
        if not isinstance(doc.get("sampling"), dict):
            reader.issue(
                "missing_required_field", entity, "sampling", doc["metadata_path"]
            )
        if isinstance(doc.get("reverberation_batch"), dict):
            batches.append(doc["reverberation_batch"])
        else:
            reader.issue(
                "missing_required_field",
                entity,
                "reverberation_batch",
                doc["metadata_path"],
            )
        surfaces = _nested(doc, "room", "materials")
        vertices = scene.get("footprint_vertices_m")
        if vertices and isinstance(surfaces, dict):
            for index, wall in enumerate(scene["wall_ids"] + ["floor", "ceiling"]):
                area = (
                    math.dist(vertices[index], vertices[(index + 1) % len(vertices)])
                    * scene["room_height_m"]
                    if index < len(vertices) and scene["room_height_m"]
                    else scene["room_area_m2"]
                    if index >= len(vertices)
                    else None
                )
                material = reader.identifier(
                    surfaces.get(wall),
                    entity,
                    f"materials.{wall}",
                    doc["metadata_path"],
                )
                data.materials.append(
                    dict(
                        scene_id=scene_id,
                        surface_id=wall,
                        material_id=material,
                        surface_area_m2=area,
                        metadata_path=doc["metadata_path"],
                    )
                )
        else:
            reader.issue(
                "missing_required_field", entity, "room.materials", doc["metadata_path"]
            )
    if batches:
        if all(_same(batches[0], b) for b in batches[1:]):
            data.sampling_batches = [
                dict(batches[0], unit="run_batch", manifest_count=len(batches))
            ]
        else:
            reader.issue(
                "conflicting_identity",
                "run_batch",
                "reverberation_batch",
                detail="Batch totals excluded; distinct records retained",
            )
            for b in batches:
                if not any(_same(b, r["value"]) for r in data.sampling_batches):
                    data.sampling_batches.append(dict(value=b, eligible=False))
    raven = defaultdict(list)
    fallback_sources = defaultdict(list)
    for (scene_id, hid), doc in renders.items():
        variant_id = f"{scene_id}__{hid}"
        entity = f"variant:{variant_id}"
        scene = scene_map.get(scene_id, dict(scene_id=scene_id))
        if scene_id not in scene_map:
            reader.issue(
                "missing_manifest", f"scene:{scene_id}", path=doc["metadata_path"]
            )
        record = dict(
            scene,
            scene_id=scene_id,
            variant_id=variant_id,
            hrtf_id=hid,
            hrtf_path=doc.get("hrtf_path"),
            employed_hrtf=Path(str(doc.get("hrtf_path"))).name
            if doc.get("hrtf_path")
            else hid,
            metadata_path=doc["metadata_path"],
            provenance=doc["provenance"],
            sample_rate_hz=reader.value(
                doc.get("sample_rate_hz"),
                entity,
                "sample_rate_hz",
                doc["metadata_path"],
                positive=True,
            ),
            render_backend=_nested(doc, "summary", "render_backend"),
            background_noise=_nested(doc, "summary", "background_noise"),
        )
        record["background_noise_applied"] = _nested(
            record, "background_noise", "applied"
        )
        if not isinstance(record["background_noise"], dict):
            reader.issue(
                "missing_required_field",
                entity,
                "summary.background_noise",
                doc["metadata_path"],
            )
        data.render_scenes.append(record)
        sources = _sources(doc, reader, entity, True)
        for sid, source in sources.items():
            key = (scene_id, sid)
            if key in source_map:
                normalized = source_map[key]
            else:
                if record["render_backend"] == "RAVEN + ITA":
                    normalized = _source_record(
                        sid, source, scene, reader, doc["metadata_path"], True
                    )
                else:
                    copy = dict(source, position_m=None)
                    normalized = _source_record(
                        sid, copy, scene, reader, doc["metadata_path"]
                    )
                    normalized["position_provenance"] = (
                        "unknown frame; spatial data excluded"
                    )
                    reader.issue(
                        "unknown_coordinate_frame",
                        f"source:{scene_id}/{sid}",
                        "position_m",
                        doc["metadata_path"],
                    )
                fallback_sources[key].append(normalized)
            data.render_sources.append(
                dict(
                    normalized,
                    variant_id=variant_id,
                    hrtf_id=hid,
                    render_metadata_path=doc["metadata_path"],
                )
            )
        reverb = _nested(doc, "summary", "room", "reverberation")
        bands = _bands(
            _nested(reverb, "band_frequencies_hz"),
            _nested(reverb, "t30_s"),
            reader,
            f"variant:{variant_id}",
            doc["metadata_path"],
        )
        raven[scene_id].append(
            dict(variant_id=variant_id, bands=bands, provenance=doc["provenance"])
        )
        for band_index, f, v in _audited_bands(
            _nested(reverb, "band_frequencies_hz"), _nested(reverb, "t30_s"), bands
        ):
            data.rt30_records.append(
                dict(
                    scene_id=scene_id,
                    variant_id=variant_id,
                    method="RAVEN rpf.getT30",
                    band_index=band_index,
                    frequency_hz=f,
                    value_s=v,
                    eligible_for_comparison=v is not None,
                    metadata_path=doc["metadata_path"],
                )
            )
    for key, rows in fallback_sources.items():
        if len(rows) > 1 and all(
            _same(
                {
                    k: v
                    for k, v in rows[0].items()
                    if k not in {"metadata_path", "provenance"}
                },
                {
                    k: v
                    for k, v in row.items()
                    if k not in {"metadata_path", "provenance"}
                },
            )
            for row in rows[1:]
        ):
            source_map[key] = dict(
                rows[0],
                provenance=sorted({p for row in rows for p in row["provenance"]}),
            )
        else:
            source_map[key] = _merge(rows, reader, f"source:{key[0]}/{key[1]}")
    data.scenes = list(scene_map.values())
    data.sources = list(source_map.values())
    for scene_id in sorted(set(sabine) | set(raven)):
        profiles = sorted(raven.get(scene_id, []), key=lambda p: p["variant_id"])
        frequencies = set(sabine_expected.get(scene_id, set()))
        frequencies.update(
            r["frequency_hz"]
            for r in data.rt30_records
            if r["scene_id"] == scene_id
            and r["method"] == "RAVEN rpf.getT30"
            and r["frequency_hz"] is not None
        )
        consolidated = {}
        for frequency in sorted(frequencies):
            contributing = [p for p in profiles if frequency in p["bands"]]
            values = [p["bands"][frequency] for p in contributing]
            equivalent = all(
                math.isclose(a, b, rel_tol=RT30_REL_TOL, abs_tol=RT30_ABS_TOL_S)
                for a, b in combinations(values, 2)
            )
            conflict = bool(values) and not equivalent
            variants = [p["variant_id"] for p in contributing]
            paths = sorted({path for p in contributing for path in p["provenance"]})
            cause = None
            stats = dict(
                n_variants=len(values),
                n_variants_excluded=len(profiles) - len(values),
                variant_ids=variants,
                provenance=paths,
                min_s=min(values) if values else None,
                max_s=max(values) if values else None,
                spread_s=max(values) - min(values) if values else None,
                stddev_s=pstdev(values) if values else None,
            )
            if conflict:
                cause = reader.issue(
                    "rt30_conflict",
                    f"scene:{scene_id}",
                    str(frequency),
                    paths[0] if paths else "",
                    "RAVEN variants exceed numerical equivalence tolerance",
                    evidence=stats,
                )
            missing = [p["variant_id"] for p in profiles if frequency not in p["bands"]]
            if missing:
                invalid_causes = [
                    i["incident_id"]
                    for i in data.incidents
                    if i["category"] == "rt30_invalid_band"
                    and i["entity"] in {f"variant:{v}" for v in missing}
                    and i["field"] in {str(frequency), "bands"}
                ]
                missing_cause = reader.issue(
                    "rt30_variant_band_unavailable",
                    f"scene:{scene_id}",
                    str(frequency),
                    detail="Missing/invalid variant bands; available variants remain usable",
                    cause_id=invalid_causes[0] if len(invalid_causes) == 1 else None,
                    evidence=dict(variant_ids=missing, cause_ids=invalid_causes),
                )
                if not values:
                    cause = missing_cause
            record = dict(
                scene_id=scene_id,
                frequency_hz=frequency,
                method="RAVEN rpf.getT30",
                value_s=_mean(values) if values and equivalent else None,
                eligible_for_comparison=bool(values) and equivalent,
                cause_id=cause,
                abs_tolerance_s=RT30_ABS_TOL_S,
                relative_tolerance=RT30_REL_TOL,
                **stats,
            )
            data.rt30_consolidated.append(record)
            consolidated[frequency] = record
            for raw in data.rt30_records:
                if (
                    raw["scene_id"] == scene_id
                    and raw["method"] == "RAVEN rpf.getT30"
                    and raw["frequency_hz"] == frequency
                ):
                    raw["eligible_for_comparison"] = (
                        raw["value_s"] is not None and equivalent
                    )
                    raw["cause_id"] = cause
        estimates = sabine.get(scene_id, {})
        for frequency in sorted(sabine_expected.get(scene_id, set())):
            measured = consolidated[frequency]
            valid = frequency in estimates and measured["value_s"] is not None
            cause = measured["cause_id"]
            reason = (
                None
                if valid
                else "variant_conflict"
                if measured["n_variants"] and measured["value_s"] is None
                else "missing_or_invalid_band"
            )
            if not valid:
                incident = reader.issue(
                    "rt30_not_comparable",
                    f"scene:{scene_id}",
                    str(frequency),
                    detail=reason,
                    cause_id=cause,
                )
                cause = cause or incident
            row = dict(
                scene_id=scene_id,
                frequency_hz=frequency,
                sabine_s=estimates.get(frequency),
                raven_s=measured["value_s"],
                difference_s=measured["value_s"] - estimates[frequency]
                if valid
                else None,
                exclusion_reason=reason,
                cause_id=cause,
                n_variants=measured["n_variants"],
                n_variants_excluded=measured["n_variants_excluded"],
                variant_ids=measured["variant_ids"],
                provenance=measured["provenance"],
            )
            data.rt30_comparisons.append(row)
            if valid:
                data.rt30_pairs.append(row)
    _coverage(data, reader, expected, renders)
    for (variant, profile), doc in degraded.items():
        data.degraded_records.append(_degraded(doc, reader))
    known_jobs = {r["job_id"] for r in data.degraded_records if r.get("job_id")}
    for row in data.clarity_index:
        job = reader.identifier(row.get("job_id"), "clarity_index", "job_id")
        if job and job not in known_jobs:
            reader.issue("missing_degraded", f"degraded:{job}")
    data.general_parameters = dict(
        supported_schema_version=SCHEMA_VERSION,
        rt30_numerical_tolerance=dict(
            absolute_s=RT30_ABS_TOL_S,
            relative=RT30_REL_TOL,
            aggregation="arithmetic_mean_of_pairwise_equivalent_unique_variants",
        ),
        observed_schema_versions=dict(reader.versions),
        backend=_counts(r.get("render_backend") for r in data.render_scenes),
        sample_rate_hz=_counts(r.get("sample_rate_hz") for r in data.render_scenes),
        project_name=_counts(d.get("project_name") for d in manifests.values()),
        render_configuration=[
            dict(scene_id=k[0], render=d.get("render")) for k, d in manifests.items()
        ],
    )
    data.warnings = [
        f"{i['category']}: {i['entity']} {i['field']}" for i in data.incidents
    ]
    return data


def _coverage(data, reader, expected, renders):
    indexed = set()
    for row in data.render_index:
        scene = reader.identifier(row.get("scene_id"), "render_index", "scene_id")
        hid = reader.identifier(row.get("output_type"), "render_index", "output_type")
        variant = reader.identifier(row.get("variant_id"), "render_index", "variant_id")
        if scene and hid and variant:
            if variant != f"{scene}__{hid}":
                reader.issue("index_mismatch", f"variant:{variant}", "identity")
            else:
                indexed.add((scene, hid))
            snapshot = row.get("metadata_snapshot")
            if isinstance(snapshot, dict) and (
                snapshot.get("scene_id") != scene or snapshot.get("hrtf_id") != hid
            ):
                reader.issue(
                    "index_mismatch", f"variant:{variant}", "metadata_snapshot"
                )
    expectation = expected | indexed
    for scene, hid in sorted(expectation | set(renders)):
        present = (scene, hid) in renders
        expected_flag = True if (scene, hid) in expectation else None
        if expected_flag and not present:
            reader.issue("missing_render", f"variant:{scene}__{hid}")
        if indexed and present and (scene, hid) not in indexed:
            reader.issue("orphan_render", f"variant:{scene}__{hid}")
        data.coverage.append(
            dict(
                scene_id=scene,
                hrtf_id=hid,
                variant_id=f"{scene}__{hid}",
                present=present,
                expected=expected_flag,
                indexed=(scene, hid) in indexed,
            )
        )


def _degraded(doc, reader):
    entity = f"degraded:{doc['variant_id']}/{doc['hearing_profile_id']}"
    for key in ["scene_id", "job_id", "output_type", "status"]:
        doc[key] = reader.identifier(doc.get(key), entity, key, doc["metadata_path"])
    if doc.get("status") not in {"completed", "failed"}:
        reader.issue("malformed_field", entity, "status", doc["metadata_path"])
    if (
        doc.get("scene_id")
        and doc.get("output_type")
        and doc["variant_id"] != f"{doc['scene_id']}__{doc['output_type']}"
    ):
        reader.issue("index_mismatch", entity, "variant_id", doc["metadata_path"])
    bands = {}
    for ear in ["left", "right"]:
        raw = _nested(doc, "degradation_applied", ear, "loss_db_by_band")
        bands[ear] = {}
        if not isinstance(raw, dict):
            reader.issue(
                "missing_required_field",
                entity,
                f"{ear}.loss_db_by_band",
                doc["metadata_path"],
            )
            continue
        ambiguous = set()
        for frequency, value in raw.items():
            try:
                f = float(frequency)
            except (ValueError, TypeError, OverflowError):
                f = None
            v = _number(value)
            if (
                f is None
                or not math.isfinite(f)
                or f <= 0
                or v is None
                or v < 0
                or f in bands[ear]
                or f in ambiguous
            ):
                if f is not None:
                    bands[ear].pop(f, None)
                    ambiguous.add(f)
                reader.issue(
                    "malformed_field",
                    entity,
                    f"{ear}.loss_db_by_band.{frequency}",
                    doc["metadata_path"],
                )
            else:
                bands[ear][f] = v
    common = set(bands["left"]) & set(bands["right"])
    result = {
        key: doc.get(key)
        for key in [
            "scene_id",
            "variant_id",
            "job_id",
            "hearing_profile_id",
            "status",
            "output_type",
            "metadata_path",
            "provenance",
            "input_render_metadata_path",
        ]
    }
    result.update(
        processor_name=_nested(doc, "processor", "name"),
        left_mean_loss_db=_mean(list(bands["left"].values())),
        right_mean_loss_db=_mean(list(bands["right"].values())),
        ear_asymmetry_db=_mean(
            [abs(bands["left"][f] - bands["right"][f]) for f in common]
        ),
        common_frequencies_hz=sorted(common),
        loss_by_band=bands,
    )
    result["mean_loss_db"] = _mean([v for ear in bands.values() for v in ear.values()])
    return result


def _mean(values):
    return sum(values) / len(values) if values else None


def _counts(values):
    return dict(
        sorted(Counter(str(v) for v in values if v is not None and v != "").items())
    )


def summary_for(data: AnalysisData) -> JsonObject:
    populations = {}

    def population(key, records, field, unit, measurement_unit):
        valid = [r[field] for r in records if _number(r.get(field)) is not None]
        populations[key] = dict(
            observation_unit=unit,
            unit=measurement_unit,
            n_valid=len(valid),
            n_excluded=len(records) - len(valid),
            mean=_mean(valid),
            status="available" if valid else "unavailable",
        )

    for field in [
        "room_length_m",
        "room_width_m",
        "room_height_m",
        "room_area_m2",
        "room_volume_m3",
        "n_sources",
        "wall_distance_m",
    ]:
        population(
            field,
            data.scenes,
            field,
            "scene",
            "m2"
            if "area" in field
            else "m3"
            if "volume" in field
            else "sources"
            if field == "n_sources"
            else "m",
        )
    for field in [
        "distance_m",
        "global_doa_deg",
        "receiver_relative_doa_deg",
        "elevation_deg",
        "gain_db",
        "start_time_s",
        "wall_distance_m",
    ]:
        population(
            "source_" + field,
            data.sources,
            field,
            "source",
            "deg"
            if "deg" in field
            else "dB"
            if "db" in field
            else "s"
            if field.endswith("_s")
            else "m",
        )
    population(
        "rt30_difference_s",
        data.rt30_comparisons,
        "difference_s",
        "scene_frequency_pair",
        "s",
    )
    for field in ["left_mean_loss_db", "right_mean_loss_db", "ear_asymmetry_db"]:
        population(field, data.degraded_records, field, "degraded_record", "dB")
    pair_scenes = defaultdict(list)
    for row in data.rt30_pairs:
        pair_scenes[row["scene_id"]].append(row)
    matched = [
        dict(
            scene_id=s,
            n_pairs=len(rows),
            frequencies_hz=[r["frequency_hz"] for r in rows],
            sabine_mean_s=_mean([r["sabine_s"] for r in rows]),
            raven_mean_s=_mean([r["raven_s"] for r in rows]),
            difference_mean_s=_mean([r["difference_s"] for r in rows]),
        )
        for s, rows in pair_scenes.items()
    ]
    known = [r for r in data.coverage if r["expected"]]
    return dict(
        run_dir=str(data.run_dir),
        status="no_analyzable_data"
        if not (data.scenes or data.render_scenes or data.degraded_records)
        else "partial"
        if data.incidents
        else "complete",
        unique_scene_count=len(
            {r["scene_id"] for r in data.scenes + data.render_scenes}
        ),
        scenes_with_render_count=len({r["scene_id"] for r in data.render_scenes}),
        render_variant_count=len(data.render_scenes),
        unique_source_count=len(data.sources),
        source_variant_count=len(data.render_sources),
        degraded_metadata_count=len(data.degraded_records),
        render_scene_count=len({r["scene_id"] for r in data.render_scenes}),
        render_source_count=len(data.sources),
        render_index_count=len(data.render_index),
        clarity_index_count=len(data.clarity_index),
        source_types=_counts(r.get("source_type") for r in data.sources),
        geometry_types=_counts(r.get("geometry_type") for r in data.scenes),
        employed_hrtfs=_counts(r.get("employed_hrtf") for r in data.render_scenes),
        degraded_profiles=_counts(
            r.get("hearing_profile_id") for r in data.degraded_records
        ),
        degraded_statuses=_counts(r.get("status") for r in data.degraded_records),
        incident_counts=_counts(i["category"] for i in data.incidents),
        affected_entity_count=len({i["entity"] for i in data.incidents}),
        populations=populations,
        general_parameters=data.general_parameters,
        rt30_matched_means=matched,
        expected_variant_count=len(known) if known else None,
        variant_completeness=sum(r["present"] for r in known) / len(known)
        if known
        else None,
        sampling_batches=data.sampling_batches,
        warnings=data.warnings,
    )


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, records: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def write_csv(path: Path, records: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for record in records for key in record})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in records:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False, allow_nan=False)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )
