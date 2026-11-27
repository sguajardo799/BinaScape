"""Export renders and their degraded versions for a calibrated playback chain (design D11).

Every version of a render (unprocessed, MSBG, WHIS) gets the same per-channel gain
``g_c = K_render - k_play_db_c``, so each is presented at the physical level it was
simulated at. A render is exported whole or not at all: if any version would exceed
``max_spl_db`` or clip, none of its versions is written.
"""

import json
import math
import re
from pathlib import Path
from typing import Any, TypedDict

import numpy as np
import soundfile as sf
import yaml

from acoustic_orchestrator.pipeline.level_calibration import CHANNEL_NAMES, load_level_calibration_index
from acoustic_orchestrator.pipeline.output_index import load_clarity_index, load_variant_index


PLAYBACK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
UNPROCESSED = "unprocessed"


class PlaybackCalibration(TypedDict):
    playback_id: str
    k_play_db: dict[str, float]
    max_spl_db: float


class ExportSummary(TypedDict):
    playback_id: str
    export_root: str
    exported_renders: int
    exported_files: int
    skipped_renders: dict[str, str]
    missing_degraded: dict[str, list[dict[str, str]]]


def load_playback_calibration(path: Path) -> PlaybackCalibration:
    """Read and validate the playback calibration; raise ValueError naming every invalid field."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"No se pudo leer la calibración de reproducción {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("La calibración de reproducción debe ser un objeto con playback_id, k_play_db y max_spl_db")
    errors: list[str] = []
    playback_id = raw.get("playback_id")
    if not isinstance(playback_id, str) or not PLAYBACK_ID_PATTERN.fullmatch(playback_id):
        errors.append("playback_id debe ser un identificador path-safe")
    k_play = raw.get("k_play_db")
    k_play_db: dict[str, float] = {}
    if not isinstance(k_play, dict):
        errors.append("k_play_db debe declarar left y right")
    else:
        for channel in CHANNEL_NAMES:
            value = k_play.get(channel)
            if not _is_finite_number(value):
                errors.append(f"k_play_db.{channel} debe ser numérico y finito")
            else:
                k_play_db[channel] = float(value)
    max_spl = raw.get("max_spl_db")
    if not _is_finite_number(max_spl):
        errors.append("max_spl_db debe ser numérico y finito")
    if errors:
        raise ValueError("\n".join(errors))
    return {"playback_id": playback_id, "k_play_db": k_play_db, "max_spl_db": float(max_spl)}


def export_presentation(run_root: Path, playback_path: Path) -> ExportSummary:
    playback = load_playback_calibration(playback_path)
    run_root = Path(run_root).resolve()
    indexes = run_root / "metadata" / "indexes"
    calibrations = load_level_calibration_index(indexes / "level_calibration.jsonl")
    renders = load_variant_index(indexes / "render_index.jsonl")
    clarity = load_clarity_index(indexes / "clarity_index.jsonl")
    export_root = run_root / "output_audio" / "presentation" / playback["playback_id"]

    summary: ExportSummary = {
        "playback_id": playback["playback_id"],
        "export_root": export_root.as_posix(),
        "exported_renders": 0,
        "exported_files": 0,
        "skipped_renders": {},
        "missing_degraded": {},
    }
    for variant_id in sorted(set(renders) | set(calibrations)):
        calibration = calibrations.get(variant_id)
        if calibration is None:
            summary["skipped_renders"][variant_id] = "no_calibration"
            continue
        scale = calibration.get("effective_scale_db_spl")
        if not _is_finite_number(scale):
            summary["skipped_renders"][variant_id] = "calibration_undefined"
            continue
        render = renders.get(variant_id)
        output_type = render["output_type"] if render else _output_type_from_jobs(clarity, variant_id)
        if output_type is None:
            summary["skipped_renders"][variant_id] = "render_unknown"
            continue

        versions, missing = _collect_versions(clarity, variant_id, calibration, output_type, export_root)
        if missing:
            summary["missing_degraded"][variant_id] = missing
        prepared, reason = _prepare_versions(variant_id, versions, float(scale), playback)
        if reason is not None:
            summary["skipped_renders"][variant_id] = reason
            continue
        for version, audio, record in prepared:
            version["target"].parent.mkdir(parents=True, exist_ok=True)
            sf.write(version["target"], audio, record["sample_rate_hz"], subtype="FLOAT")
            version["target"].with_suffix(".json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            summary["exported_files"] += 1
        summary["exported_renders"] += 1

    export_root.mkdir(parents=True, exist_ok=True)
    (export_root / "export_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _collect_versions(
    clarity: dict[str, Any], variant_id: str, calibration: dict[str, Any], output_type: str, export_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    source = Path(calibration["input_wav_path"])
    versions = [{
        "version": UNPROCESSED, "hearing_profile_id": None, "source": source,
        "target": export_root / UNPROCESSED / output_type / source.name,
    }]
    missing: list[dict[str, str]] = []
    for record in sorted(clarity.values(), key=lambda item: item["job_id"]):
        if record["variant_id"] != variant_id or record["processor"] is None:
            continue
        processor, profile = record["processor"], record["hearing_profile_id"]
        reason = _degraded_issue(record, calibration)
        if reason is not None:
            missing.append({"processor": processor, "hearing_profile_id": profile, "reason": reason})
            continue
        degraded = Path(record["expected_output_wav_path"])
        versions.append({
            "version": processor, "hearing_profile_id": profile, "source": degraded,
            "target": export_root / processor / output_type / profile / degraded.name,
        })
    return versions, missing


def _degraded_issue(record: dict[str, Any], calibration: dict[str, Any]) -> str | None:
    if record["status"] not in {"completed", "skipped"} or not record["validation"]["ok"]:
        return f"status_{record['status']}"
    wav, sidecar = Path(record["expected_output_wav_path"]), Path(record["expected_output_metadata_path"])
    if not wav.is_file() or not sidecar.is_file():
        return "output_missing"
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "sidecar_unreadable"
    if payload.get("status") != "completed":
        return f"sidecar_{payload.get('status')}"
    if (payload.get("calibration") or {}).get("effective_scale_db_spl") != calibration["effective_scale_db_spl"]:
        return "calibration_mismatch"
    return None


def _prepare_versions(
    variant_id: str, versions: list[dict[str, Any]], scale: float, playback: PlaybackCalibration,
) -> tuple[list[tuple[dict[str, Any], np.ndarray, dict[str, Any]]], str | None]:
    """Apply the render gain to every version; refuse the whole render if any version is unsafe."""
    gains_db = {channel: scale - playback["k_play_db"][channel] for channel in CHANNEL_NAMES}
    gain = np.array([10 ** (gains_db[channel] / 20) for channel in CHANNEL_NAMES])
    prepared = []
    exceeds = clips = False
    for version in versions:
        audio, sample_rate = sf.read(version["source"], always_2d=True, dtype="float64")
        if audio.shape[1] != 2:
            return [], "not_stereo"
        rms = np.sqrt(np.mean(np.square(audio), axis=0))
        spl = {
            channel: (scale + 20 * math.log10(rms[index]) if rms[index] > 0 else None)
            for index, channel in enumerate(CHANNEL_NAMES)
        }
        scaled = audio * gain
        peak = float(np.max(np.abs(scaled))) if scaled.size else 0.0
        exceeds |= any(level is not None and level > playback["max_spl_db"] for level in spl.values())
        clips |= peak > 1.0
        record = {
            "variant_id": variant_id,
            "version": version["version"],
            "hearing_profile_id": version["hearing_profile_id"],
            "source_wav_path": Path(version["source"]).resolve().as_posix(),
            "sample_rate_hz": int(sample_rate),
            "playback_id": playback["playback_id"],
            "effective_scale_db_spl": scale,
            "k_play_db": playback["k_play_db"],
            "max_spl_db": playback["max_spl_db"],
            "gain_db": gains_db,
            "estimated_spl_db": spl,
            "peak_after_gain": peak,
        }
        prepared.append((version, scaled, record))
    if exceeds:
        return [], "exceeds_max_spl"
    if clips:
        return [], "would_clip"
    return prepared, None


def _output_type_from_jobs(clarity: dict[str, Any], variant_id: str) -> str | None:
    return next((record["output_type"] for record in clarity.values() if record["variant_id"] == variant_id), None)


def _is_finite_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
