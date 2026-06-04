from __future__ import annotations

import importlib.metadata
import math
from pathlib import Path

import numpy as np
import soundfile as sf
from clarity.evaluator.msbg.msbg import Ear
from clarity.utils.audiogram import Audiogram


SUPPORTED_MSBG_FREQUENCIES_HZ = (250, 500, 1000, 2000, 4000, 8000)


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


def process_wav(
    input_wav_path: Path,
    output_wav_path: Path,
    left_audiogram: Audiogram,
    right_audiogram: Audiogram,
) -> dict[str, object]:
    try:
        audio, sample_rate = sf.read(input_wav_path, always_2d=True)
        input_info = sf.info(input_wav_path)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"No se pudo leer el WAV de entrada para MSBG: {input_wav_path}: {exc}") from exc

    if input_info.format != "WAV":
        raise ValueError(f"MSBG solo soporta archivos WAV: {input_wav_path}")
    if sample_rate != 44100:
        raise ValueError(f"MSBG solo soporta sample_rate 44100 Hz: {input_wav_path} ({sample_rate} Hz)")
    if audio.shape[1] != 2:
        raise ValueError(f"MSBG requiere WAV estéreo de 2 canales: {input_wav_path} ({audio.shape[1]} canales)")

    expected_samples = audio.shape[0]
    left_processed = _match_expected_samples(_process_channel(audio[:, 0], sample_rate, left_audiogram), expected_samples)
    right_processed = _match_expected_samples(_process_channel(audio[:, 1], sample_rate, right_audiogram), expected_samples)
    processed_audio = np.column_stack((left_processed, right_processed))

    output_wav_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_wav_path, processed_audio, sample_rate, format="WAV", subtype=input_info.subtype)

    return {
        "name": "msbg",
        "package": "pyclarity",
        "package_version": _get_pyclarity_version(),
        "sample_rate_hz": sample_rate,
        "channels": int(processed_audio.shape[1]),
    }


def _process_channel(channel: np.ndarray, sample_rate: int, audiogram: Audiogram) -> np.ndarray:
    ear = Ear(sample_rate=float(sample_rate))
    ear.set_audiogram(audiogram)
    processed = ear.process(np.asarray(channel, dtype=float))
    return np.asarray(processed[0], dtype=float)


def _match_expected_samples(channel: np.ndarray, expected_samples: int) -> np.ndarray:
    if channel.shape[0] >= expected_samples:
        return channel[:expected_samples]
    return np.pad(channel, (0, expected_samples - channel.shape[0]))


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
