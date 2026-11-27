"""Digital level <-> dB SPL calibration shared by every hearing processor of a render.

K (dB SPL for digital RMS 1.0) is computed once per render here and travels unchanged
inside every degradation job of that render, so MSBG, WHIS and the unprocessed export
all see the same scale. The signal itself is never rescaled for calibration.
"""

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal, TypedDict

import numpy as np
import soundfile as sf

from acoustic_orchestrator.config.models import HearingDegradationConfig
from acoustic_orchestrator.pipeline.output_index import _upsert_many_jsonl


DEFAULT_DIGITAL_RMS1_SPL_DB = 90.0
DEFAULT_OUTER_MIDDLE_EAR = "free_field"
LEVEL_MEASURE = "rms_unweighted_full_file"
CALIBRATION_UNDEFINED_SILENT_INPUT = "calibration_undefined_silent_input"
CHANNEL_NAMES = ("left", "right")


class EffectiveCalibration(TypedDict):
    mode: Literal["fixed_scale", "presentation_level"]
    configured: dict[str, Any]
    is_default: bool


class ChannelLevel(TypedDict):
    mean_square: float
    leq_db_spl: float | None
    silent: bool


class RenderCalibrationRecord(TypedDict):
    variant_id: str
    input_wav_path: str
    mode: str
    configured: dict[str, Any]
    is_default: bool
    random_seed: int | None
    presentation_level_db_spl: float | None
    effective_scale_db_spl: float | None
    channels: dict[str, ChannelLevel]
    silent_channels: list[str]
    level_measure: str
    status: Literal["ok", "undefined_silent_input"]


def effective_calibration(hearing: HearingDegradationConfig) -> EffectiveCalibration:
    calibration = hearing.calibration
    if calibration is None:
        return {
            "mode": "fixed_scale",
            "configured": {"digital_rms1_spl_db": DEFAULT_DIGITAL_RMS1_SPL_DB},
            "is_default": True,
        }
    if calibration.mode == "fixed_scale":
        configured: dict[str, Any] = {"digital_rms1_spl_db": calibration.digital_rms1_spl_db}
    elif calibration.presentation_level_range_db_spl is not None:
        configured = {"presentation_level_range_db_spl": list(calibration.presentation_level_range_db_spl)}
    else:
        configured = {"presentation_level_db_spl": calibration.presentation_level_db_spl}
    return {"mode": calibration.mode, "configured": configured, "is_default": False}


def effective_outer_middle_ear(hearing: HearingDegradationConfig) -> str:
    return hearing.outer_middle_ear or DEFAULT_OUTER_MIDDLE_EAR


def uses_level_range(calibration: EffectiveCalibration) -> bool:
    return "presentation_level_range_db_spl" in calibration["configured"]


def sample_presentation_level(random_seed: int, variant_id: str, low: float, high: float) -> float:
    """Uniform P in [low, high], reproducible for (seed, variant) regardless of iteration order."""
    digest = hashlib.sha256(f"{random_seed}\x1f{variant_id}".encode("utf-8")).digest()
    u = float(np.random.default_rng(int.from_bytes(digest[:8], "big")).random())
    return low + u * (high - low)


def measure_channel_mean_squares(wav_path: str | Path) -> tuple[float, float]:
    audio, _ = sf.read(Path(wav_path), always_2d=True, dtype="float64")
    if audio.shape[1] != 2:
        raise ValueError(f"La calibración requiere un WAV estéreo de 2 canales: {wav_path} ({audio.shape[1]} canales)")
    mean_squares = np.mean(np.square(audio), axis=0)
    return float(mean_squares[0]), float(mean_squares[1])


def compute_render_calibration(
    hearing: HearingDegradationConfig,
    *,
    random_seed: int,
    variant_id: str,
    input_wav_path: str | Path,
    mean_squares: tuple[float, float] | None = None,
) -> RenderCalibrationRecord:
    calibration = effective_calibration(hearing)
    configured = calibration["configured"]
    ms_left, ms_right = mean_squares if mean_squares is not None else measure_channel_mean_squares(input_wav_path)
    silent = [name for name, ms in zip(CHANNEL_NAMES, (ms_left, ms_right)) if ms <= 0.0]

    presentation_level: float | None = None
    if calibration["mode"] == "fixed_scale":
        scale: float | None = float(configured["digital_rms1_spl_db"])
    else:
        if uses_level_range(calibration):
            low, high = configured["presentation_level_range_db_spl"]
            presentation_level = sample_presentation_level(random_seed, variant_id, float(low), float(high))
        else:
            presentation_level = float(configured["presentation_level_db_spl"])
        binaural_ms = (ms_left + ms_right) / 2.0
        scale = presentation_level - 10.0 * math.log10(binaural_ms) if binaural_ms > 0.0 else None

    channels: dict[str, ChannelLevel] = {}
    for name, ms in zip(CHANNEL_NAMES, (ms_left, ms_right)):
        leq = scale + 10.0 * math.log10(ms) if scale is not None and ms > 0.0 else None
        channels[name] = {"mean_square": ms, "leq_db_spl": leq, "silent": ms <= 0.0}

    return {
        "variant_id": variant_id,
        "input_wav_path": Path(input_wav_path).resolve().as_posix(),
        "mode": calibration["mode"],
        "configured": configured,
        "is_default": calibration["is_default"],
        "random_seed": random_seed if uses_level_range(calibration) else None,
        "presentation_level_db_spl": presentation_level,
        "effective_scale_db_spl": scale,
        "channels": channels,
        "silent_channels": silent,
        "level_measure": LEVEL_MEASURE,
        "status": "ok" if scale is not None else "undefined_silent_input",
    }


def load_level_calibration_index(index_path: Path) -> dict[str, RenderCalibrationRecord]:
    if not index_path.is_file():
        return {}
    records: dict[str, RenderCalibrationRecord] = {}
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[record["variant_id"]] = record
    return records


def upsert_level_calibration_records(
    index_path: Path, records: dict[str, RenderCalibrationRecord], updates: list[RenderCalibrationRecord],
) -> None:
    if updates:
        _upsert_many_jsonl(index_path, records, {record["variant_id"]: record for record in updates})
