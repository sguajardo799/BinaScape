from __future__ import annotations

import importlib.metadata
import math
from pathlib import Path

import numpy as np
import soundfile as sf
from clarity.evaluator.msbg import msbg as msbg_module
from clarity.evaluator.msbg.msbg import Ear
from clarity.utils.audiogram import Audiogram

from clarity_backend.latency import cached_latency, compensate_latency, measure_impulse_latency


SUPPORTED_MSBG_FREQUENCIES_HZ = (250, 500, 1000, 2000, 4000, 8000)
# Accepted in the shared catalog for WHIS; MSBG ignores them and reports them as ignored.
IGNORED_MSBG_BANDS = ("125",)
LEVEL_HANDLINGS = ("true_level", "legacy_active_rescale")
# Outer/middle ear reference shared with WHIS (design D5). eardrum keeps only the middle ear.
SRC_POS_BY_OUTER_MIDDLE_EAR = {"free_field": "ff", "diffuse_field": "df", "itu": "ITU", "eardrum": "ff"}
MSBG_SAMPLE_RATE_HZ = 44100
# pyclarity's Ear default "ahr"; equiv_0db_spl + ahr is the dB SPL of digital RMS 1.0.
MSBG_AHR_DB = 20.0
# Same arguments Ear.process uses to find the active (non-pause) frames.
ACTIVE_RMS_REL_DB = -12
# Degraded outputs are written as 32-bit float (design D13).
OUTPUT_SUBTYPE = "FLOAT"


def build_audiograms(profile: dict[str, object]) -> tuple[Audiogram, Audiogram, dict[str, object]]:
    hearing_profile_id = str(profile["hearing_profile_id"])
    left_loss_db_by_band = _normalize_loss_db_by_band(
        profile.get("left_loss_db_by_band"),
        hearing_profile_id,
        "left",
    )
    right_loss_db_by_band = _normalize_loss_db_by_band(
        profile.get("right_loss_db_by_band"),
        hearing_profile_id,
        "right",
    )

    frequencies = np.array(SUPPORTED_MSBG_FREQUENCIES_HZ, dtype=int)
    left_levels = np.array([left_loss_db_by_band[str(frequency)] for frequency in frequencies], dtype=float)
    right_levels = np.array([right_loss_db_by_band[str(frequency)] for frequency in frequencies], dtype=float)

    return (
        Audiogram(levels=left_levels, frequencies=frequencies),
        Audiogram(levels=right_levels, frequencies=frequencies),
        {
            "left": {
                "loss_db_by_band": left_loss_db_by_band,
                "audiogram_frequencies_hz": list(SUPPORTED_MSBG_FREQUENCIES_HZ),
            },
            "right": {
                "loss_db_by_band": right_loss_db_by_band,
                "audiogram_frequencies_hz": list(SUPPORTED_MSBG_FREQUENCIES_HZ),
            },
        },
    )


def ignored_profile_fields(profile: dict[str, object]) -> dict[str, list[str]]:
    """Catalog fields MSBG accepts but does not use, per ear."""
    ignored: dict[str, list[str]] = {}
    for ear_name in ("left", "right"):
        fields = [
            f"loss_db_by_band.{band}"
            for band in IGNORED_MSBG_BANDS
            if band in (profile.get(f"{ear_name}_loss_db_by_band") or {})
        ]
        if profile.get(f"{ear_name}_compression_health") is not None:
            fields.append("compression_health")
        ignored[ear_name] = fields
    return ignored


def process_wav(
    input_wav_path: Path,
    output_wav_path: Path,
    left_audiogram: Audiogram,
    right_audiogram: Audiogram,
    *,
    effective_scale_db_spl: float = 90.0,
    outer_middle_ear: str = "free_field",
    level_handling: str = "true_level",
) -> dict[str, object]:
    if level_handling not in LEVEL_HANDLINGS:
        raise ValueError(f"msbg.level_handling no soportado: {level_handling}; usar {', '.join(LEVEL_HANDLINGS)}")
    if outer_middle_ear not in SRC_POS_BY_OUTER_MIDDLE_EAR:
        raise ValueError(
            f"outer_middle_ear no soportado por MSBG: {outer_middle_ear}; "
            f"usar {', '.join(SRC_POS_BY_OUTER_MIDDLE_EAR)}"
        )
    if effective_scale_db_spl is None or not math.isfinite(effective_scale_db_spl):
        raise ValueError(f"MSBG requiere una escala efectiva K finita (effective_scale_db_spl={effective_scale_db_spl})")
    try:
        audio, sample_rate = sf.read(input_wav_path, always_2d=True)
        input_info = sf.info(input_wav_path)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"No se pudo leer el WAV de entrada para MSBG: {input_wav_path}: {exc}") from exc

    if input_info.format != "WAV":
        raise ValueError(f"MSBG solo soporta archivos WAV: {input_wav_path}")
    if sample_rate != MSBG_SAMPLE_RATE_HZ:
        raise ValueError(f"MSBG solo soporta sample_rate 44100 Hz: {input_wav_path} ({sample_rate} Hz)")
    if audio.shape[1] != 2:
        raise ValueError(f"MSBG requiere WAV estéreo de 2 canales: {input_wav_path} ({audio.shape[1]} canales)")

    expected_samples = audio.shape[0]
    compensate = level_handling == "true_level"
    latency = msbg_latency_samples(outer_middle_ear) if compensate else 0
    channels = []
    channel_metadata: dict[str, dict[str, object]] = {}
    for index, (ear_name, audiogram) in enumerate((("left", left_audiogram), ("right", right_audiogram))):
        processed, active_rescale_db = _process_channel(
            audio[:, index], sample_rate, audiogram, effective_scale_db_spl, outer_middle_ear, level_handling,
        )
        channels.append(compensate_latency(processed, latency, expected_samples))
        channel_metadata[ear_name] = {
            "silent": active_rescale_db is None and not np.any(audio[:, index]),
            "active_rescale_db": active_rescale_db,
        }
    processed_audio = np.column_stack(channels)

    output_peak = float(np.max(np.abs(processed_audio))) if processed_audio.size else 0.0
    # true_level outputs are float32 so filtering overshoot above full scale is kept, never clipped
    # (design D13). legacy_active_rescale keeps the input subtype to stay bit-identical to before.
    output_subtype = input_info.subtype if level_handling == "legacy_active_rescale" else OUTPUT_SUBTYPE
    check_clipping(output_peak, output_subtype, processor="MSBG")
    output_wav_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_wav_path, processed_audio, sample_rate, format="WAV", subtype=output_subtype)

    return {
        "name": "msbg",
        "package": "pyclarity",
        "package_version": _get_pyclarity_version(),
        "sample_rate_hz": sample_rate,
        "channels": int(processed_audio.shape[1]),
        "level_handling": level_handling,
        "outer_middle_ear": outer_middle_ear,
        "src_pos": "eardrum (middle ear only)" if outer_middle_ear == "eardrum" else SRC_POS_BY_OUTER_MIDDLE_EAR[outer_middle_ear],
        "equiv_0db_spl_basis": "K - ahr" if level_handling == "legacy_active_rescale" else "K - ahr - active_rescale_db",
        "ahr_db": MSBG_AHR_DB,
        "latency_compensated": compensate,
        "latency_compensation_samples": latency,
        "output_peak": output_peak,
        "output_subtype": output_subtype,
        "channel_levels": channel_metadata,
    }


def check_clipping(output_peak: float, subtype: str, *, processor: str) -> None:
    """Integer PCM cannot hold values above full scale: fail instead of writing clipped audio (design D13)."""
    if subtype.startswith("PCM") and output_peak > 1.0:
        raise ValueError(
            f"Recorte: la salida {processor} alcanza un pico de {output_peak:.4f} (> 1.0) con subtipo {subtype}"
        )


def active_rescale_db(channel: np.ndarray, sample_rate: int) -> float:
    """dB that Ear.process adds to make the active-frame RMS equal the full-file RMS (<= 0)."""
    signal = np.asarray(channel, dtype=float)
    full_rms_db = 10 * np.log10(np.mean(signal**2))
    active_rms, _, _, _ = msbg_module.measure_rms(signal, sample_rate, ACTIVE_RMS_REL_DB)
    return float(full_rms_db - 20 * np.log10(active_rms))


def msbg_latency_samples(outer_middle_ear: str) -> int:
    def measure() -> int:
        audiogram = Audiogram(
            levels=np.zeros(len(SUPPORTED_MSBG_FREQUENCIES_HZ)),
            frequencies=np.array(SUPPORTED_MSBG_FREQUENCIES_HZ, dtype=int),
        )
        return measure_impulse_latency(
            lambda impulse: _process_channel(
                impulse, MSBG_SAMPLE_RATE_HZ, audiogram, 90.0, outer_middle_ear, "true_level",
            )[0],
            MSBG_SAMPLE_RATE_HZ,
        )

    return cached_latency(("msbg", MSBG_SAMPLE_RATE_HZ, outer_middle_ear), measure)


def _process_channel(
    channel: np.ndarray,
    sample_rate: int,
    audiogram: Audiogram,
    effective_scale_db_spl: float,
    outer_middle_ear: str,
    level_handling: str,
) -> tuple[np.ndarray, float | None]:
    """Return the processed channel and the active-rescale compensation applied (None if not applied)."""
    signal = np.asarray(channel, dtype=float)
    if not np.any(signal):
        return np.zeros_like(signal), None

    if level_handling == "legacy_active_rescale":
        rescale_db = None
        equiv_0db_spl = effective_scale_db_spl - MSBG_AHR_DB
    else:
        # Ear.process scales the signal by rescale_db (<= 0) before simulating. Raising its
        # 0 dB reference by the same amount simulates the active frames at K + active RMS dB,
        # their real level, and the output is brought back to the input digital domain (D4).
        rescale_db = active_rescale_db(signal, sample_rate)
        equiv_0db_spl = effective_scale_db_spl - MSBG_AHR_DB - rescale_db

    ear = Ear(src_pos=SRC_POS_BY_OUTER_MIDDLE_EAR[outer_middle_ear], sample_rate=float(sample_rate), equiv_0db_spl=equiv_0db_spl)
    if outer_middle_ear == "eardrum":
        ear.src_correction = np.zeros_like(ear.src_correction)
    ear.set_audiogram(audiogram)
    processed = np.asarray(ear.process(signal)[0], dtype=float)
    if rescale_db is not None:
        processed = processed * np.power(10.0, -rescale_db / 20.0)
    return processed, rescale_db


def _normalize_loss_db_by_band(
    raw_loss_db_by_band: object,
    hearing_profile_id: str,
    ear_name: str,
) -> dict[str, float]:
    if not isinstance(raw_loss_db_by_band, dict) or not raw_loss_db_by_band:
        raise ValueError(f"El perfil {hearing_profile_id} debe contener {ear_name}_loss_db_by_band no vacío")

    normalized_loss_db_by_band: dict[str, float] = {}
    supported_band_names = {str(frequency) for frequency in SUPPORTED_MSBG_FREQUENCIES_HZ}
    for raw_band_name, raw_loss_db in raw_loss_db_by_band.items():
        band_name = str(raw_band_name).strip()
        if band_name == "":
            raise ValueError(f"El perfil {hearing_profile_id} contiene una banda vacía en {ear_name}")
        if band_name in IGNORED_MSBG_BANDS:
            continue
        if band_name not in supported_band_names:
            raise ValueError(
                f"El perfil {hearing_profile_id} contiene una banda no soportada en {ear_name}: {band_name}"
            )
        if not isinstance(raw_loss_db, int | float) or not math.isfinite(raw_loss_db):
            raise ValueError(
                f"El perfil {hearing_profile_id} debe contener valores numéricos finitos en {ear_name}.{band_name}"
            )
        normalized_loss_db_by_band[band_name] = float(raw_loss_db)

    for frequency in SUPPORTED_MSBG_FREQUENCIES_HZ:
        band_name = str(frequency)
        if band_name not in normalized_loss_db_by_band:
            raise ValueError(f"El perfil {hearing_profile_id} debe contener la banda {band_name} en {ear_name}")

    return normalized_loss_db_by_band


def _get_pyclarity_version() -> str | None:
    try:
        return importlib.metadata.version("pyclarity")
    except importlib.metadata.PackageNotFoundError:
        return None
