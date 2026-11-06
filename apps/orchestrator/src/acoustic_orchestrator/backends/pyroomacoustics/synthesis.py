"""Signal synthesis after the MATLAB static array semantics (numpy/scipy only).

Reproduces ``render_static_scene.m`` and its ``core/`` helpers for array
variants: downmix, peak normalization, gain, full convolution, optional reverb
tail trim, offsets, target duration, background noise with per-layer SNR and
both 0.999 peak guards. Noise is statistically, not bit-, equivalent to MATLAB.
The simulator's own mixing, SNR and noise facilities are deliberately unused.
"""

from collections.abc import Sequence
from dataclasses import dataclass
import math
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve, resample_poly
import soundfile

from acoustic_orchestrator.backends.pyroomacoustics.errors import InvalidRenderInput

SOURCE_PEAK = 0.99
GUARD_LIMIT = 0.999
_COLOR_EXPONENTS = {"white": 0.0, "pink": -1.0, "brown": -2.0}
_RNG_SEED_MASK = 2**64 - 1


def round_half_away_from_zero(value: float) -> int:
    """MATLAB ``round``: halves move away from zero (Python rounds to even)."""
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def target_num_samples(target_duration_s: float, fs: int) -> int:
    return max(1, round_half_away_from_zero(float(target_duration_s) * fs))


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    """``[samples, channels]`` float64 in [-1, 1] and the file's sample rate."""
    data, rate = soundfile.read(str(path), dtype="float64", always_2d=True)
    return data, int(rate)


def resample(audio: np.ndarray, source_fs: int, target_fs: int) -> np.ndarray:
    if source_fs == target_fs:
        return audio
    divisor = gcd(int(source_fs), int(target_fs))
    return resample_poly(audio, target_fs // divisor, source_fs // divisor, axis=0)


@dataclass
class PreparedSource:
    signal: np.ndarray  # mono, after downmix, normalization and gain
    original_num_channels: int
    downmix_policy: str
    original_duration_s: float


def prepare_source(audio: np.ndarray, source_fs: int, fs: int, gain_db: float) -> PreparedSource:
    """Resample → arithmetic-mean downmix → peak 0.99 → gain (in this order)."""
    if audio.ndim != 2 or audio.shape[0] == 0:
        raise InvalidRenderInput("source audio is empty")
    original_duration_s = audio.shape[0] / float(source_fs)
    original_channels = audio.shape[1]
    audio = resample(audio, source_fs, fs)
    mono = audio.mean(axis=1) if original_channels > 1 else audio[:, 0].astype(np.float64, copy=True)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 0:
        mono = mono * (SOURCE_PEAK / peak)
    mono = mono * 10.0 ** (float(gain_db) / 20.0)
    return PreparedSource(mono, original_channels, "arithmetic_mean" if original_channels > 1 else "none",
                          original_duration_s)


def render_source(signal: np.ndarray, rirs: Sequence[np.ndarray], *, trim_reverb_tail: bool) -> np.ndarray:
    """Full linear convolution per microphone → ``[samples, microphones]``."""
    longest = max(len(rir) for rir in rirs)
    length = len(signal) + longest - 1
    out = np.zeros((length, len(rirs)), dtype=np.float64)
    for channel, rir in enumerate(rirs):
        convolved = fftconvolve(signal, rir, mode="full")
        out[: len(convolved), channel] = convolved
    if trim_reverb_tail:
        out = out[: max(0, length - (longest - 1))]
    return out


def mix_sources(rendered: Sequence[np.ndarray], offsets: Sequence[int], channels: int,
                target_duration_s: float | None, fs: int) -> tuple[np.ndarray, dict]:
    """Place, sum, fit to the target duration and apply the first 0.999 guard."""
    total = max((offset + len(item) for item, offset in zip(rendered, offsets, strict=True)), default=0)
    mix = np.zeros((total, channels), dtype=np.float64)
    for item, offset in zip(rendered, offsets, strict=True):
        mix[offset: offset + len(item)] += item
    if target_duration_s is not None:
        target = target_num_samples(target_duration_s, fs)
        if len(mix) > target:
            mix = mix[:target]
        elif len(mix) < target:
            mix = np.vstack([mix, np.zeros((target - len(mix), channels))])
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    guard = {"peak_before_guard": peak, "guard_applied": peak > GUARD_LIMIT}
    if peak > GUARD_LIMIT:
        mix = GUARD_LIMIT * mix / peak
    return mix, guard


def source_offset_samples(start_time_s: float, fs: int) -> int:
    return max(0, round_half_away_from_zero(float(start_time_s) * fs))


# --- background noise ---------------------------------------------------------


def noise_rng(seed: float) -> np.random.Generator:
    return np.random.default_rng(int(seed) & _RNG_SEED_MASK)


def generate_colored_noise(color: str, num_samples: int, seed: float) -> np.ndarray:
    """Spectral shaping of seeded white noise; zero mean and unit RMS (mono)."""
    color = color.strip().lower()
    if color not in _COLOR_EXPONENTS:
        raise InvalidRenderInput(f"unsupported colored noise color: {color}")
    if num_samples == 0:
        return np.zeros((0, 1))
    white = noise_rng(seed).standard_normal(num_samples)
    alpha = _COLOR_EXPONENTS[color]
    if alpha == 0:
        noise = white
    else:
        frequencies = np.arange(num_samples, dtype=np.float64)
        mirrored = np.minimum(frequencies, num_samples - frequencies)
        with np.errstate(divide="ignore"):
            weights = mirrored ** (alpha / 2)
        weights[0] = 0.0
        weights[~np.isfinite(weights)] = 0.0
        noise = np.real(np.fft.ifft(np.fft.fft(white) * weights))
    noise = noise - noise.mean()
    rms = math.sqrt(float(np.mean(noise ** 2)))
    if rms > 0:
        noise = noise / rms
    return noise[:, None]


def fit_noise(noise: np.ndarray, num_samples: int, channels: int) -> tuple[np.ndarray, dict]:
    source_samples, source_channels = noise.shape
    info = {"duration_mode": "exact", "channel_mode": "exact",
            "target_num_samples": int(num_samples), "target_num_channels": int(channels)}
    if source_samples == 0 and num_samples > 0:
        raise InvalidRenderInput("cannot fit empty noise to a non-empty target")
    if num_samples == 0:
        noise = np.zeros((0, source_channels))
        info["duration_mode"] = "trim"
    elif source_samples < num_samples:
        noise = np.tile(noise, (math.ceil(num_samples / source_samples), 1))[:num_samples]
        info["duration_mode"] = "loop"
    elif source_samples > num_samples:
        noise = noise[:num_samples]
        info["duration_mode"] = "trim"
    if source_channels == 1 and channels > 1:
        noise = np.tile(noise, (1, channels))
        info["channel_mode"] = "mono_replicated"
    elif source_channels == channels:
        info["channel_mode"] = "channel_to_channel"
    else:
        raise InvalidRenderInput(
            f"noise channel count ({source_channels}) is incompatible with the array channel count ({channels})")
    return noise, info


def layer_seed(render_seed, layer_index: int, explicit_seed) -> float:
    """Explicit seed, else ``render_seed + layer_index - 1``, else ``layer_index`` (1-based)."""
    if explicit_seed not in (None, []):
        return float(explicit_seed)
    if render_seed not in (None, []):
        return float(render_seed) + layer_index - 1
    return float(layer_index)


def _strategy(layer: dict) -> tuple[str, str]:
    raw = layer.get("strategy_original", layer.get("strategy", ""))
    strategy = str(layer.get("strategy", "")).strip().lower()
    return str(raw), "audio_file" if strategy == "audio_folder" else strategy


def check_noise_layers(layers: Sequence[dict], channels: int) -> None:
    """Validate noise inputs before simulation so mismatches are ``invalid_input``."""
    for index, layer in enumerate(layers, 1):
        _, strategy = _strategy(layer)
        if not isinstance(layer.get("snr_db"), (int, float)) or isinstance(layer.get("snr_db"), bool) \
                or not math.isfinite(layer["snr_db"]):
            raise InvalidRenderInput(f"background_noise.layers[{index}].snr_db must be finite")
        if strategy == "colored":
            color = str(layer.get("color") or "white").strip().lower()
            if color not in _COLOR_EXPONENTS:
                raise InvalidRenderInput(f"background_noise.layers[{index}].color unsupported: {color}")
        elif strategy == "audio_file":
            try:
                info = soundfile.info(str(layer.get("path", "")))
            except (RuntimeError, OSError) as exc:
                raise InvalidRenderInput(f"background_noise.layers[{index}].path unreadable: {exc}") from exc
            if info.channels not in (1, channels):
                raise InvalidRenderInput(
                    f"background_noise.layers[{index}] channel mismatch: noise has {info.channels} channel(s), "
                    f"array has {channels}")
        else:
            raise InvalidRenderInput(f"background_noise.layers[{index}].strategy unsupported: {strategy}")


def _prepare_noise_layer(layer: dict, fs: int, num_samples: int, channels: int, render_seed, index: int) -> tuple[np.ndarray, dict]:
    raw_strategy, strategy = _strategy(layer)
    explicit = layer.get("seed")
    info: dict = {
        "strategy_original": raw_strategy, "strategy": strategy, "snr_db": float(layer["snr_db"]),
        "seed": [] if explicit in (None, []) else float(explicit),
        "effective_seed": layer_seed(render_seed, index, explicit),
        "source_fs": fs, "resampled": False, "path": "", "color": "",
    }
    if strategy == "colored":
        info["color"] = str(layer.get("color") or "white").strip().lower()
        raw = generate_colored_noise(info["color"], num_samples, info["effective_seed"])
    elif strategy == "audio_file":
        info["path"] = str(layer.get("path", ""))
        raw, source_fs = read_audio(Path(info["path"]))
        info["source_fs"] = source_fs
        if source_fs != fs:
            raw = resample(raw, source_fs, fs)
            info["resampled"] = True
    else:
        raise InvalidRenderInput(f"unsupported background noise strategy: {strategy}")
    info["source_num_samples"], info["source_num_channels"] = int(raw.shape[0]), int(raw.shape[1])
    noise, fit = fit_noise(raw, num_samples, channels)
    info.update(fit)
    return noise, info


def _scale_to_snr(reference: np.ndarray, noise: np.ndarray, snr_db: float) -> tuple[np.ndarray, dict]:
    signal_rms = math.sqrt(float(np.mean(reference ** 2))) if reference.size else 0.0
    noise_rms = math.sqrt(float(np.mean(noise ** 2))) if noise.size else 0.0
    info = {"signal_rms": signal_rms, "rms_before_scale": noise_rms, "gain": 0.0,
            "skipped": False, "skipped_reason": ""}
    if reference.size == 0 or signal_rms == 0:
        info.update(skipped=True, skipped_reason="silent_signal")
        return np.zeros_like(noise), info
    if noise.size == 0 or noise_rms == 0:
        info.update(skipped=True, skipped_reason="silent_noise")
        return np.zeros_like(noise), info
    info["gain"] = signal_rms / 10.0 ** (float(snr_db) / 20.0) / noise_rms
    return noise * info["gain"], info


def apply_background_noise(mix: np.ndarray, fs: int, config: dict | None, render_seed) -> tuple[np.ndarray, dict]:
    """Layers scaled independently against the pooled pre-noise RMS; second guard."""
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    metadata: dict = {
        "enabled": bool(isinstance(config, dict) and config.get("enabled")), "applied": False,
        "skipped_reason": "", "signal_rms": math.sqrt(float(np.mean(mix ** 2))) if mix.size else 0.0,
        "peak_before_guard": peak, "peak_after_guard": peak, "guard_gain": 1.0, "guard_limit": GUARD_LIMIT,
        "guard_applied": False, "effective_snr_may_change": False, "layers": [],
    }
    if not metadata["enabled"]:
        metadata["skipped_reason"] = "disabled"
        return mix, metadata
    if mix.size == 0:
        metadata["skipped_reason"] = "empty_audio"
        return mix, metadata
    num_samples, channels = mix.shape
    accumulated = np.zeros_like(mix)
    for index, layer in enumerate(config.get("layers") or [], 1):
        noise, prepared = _prepare_noise_layer(layer, fs, num_samples, channels, render_seed, index)
        scaled, scaling = _scale_to_snr(mix, noise, prepared["snr_db"])
        info = {**prepared, **scaling, "applied": not scaling["skipped"]}
        metadata["layers"].append(info)
        accumulated = accumulated + scaled
    out = mix + accumulated
    metadata["applied"] = any(layer["applied"] for layer in metadata["layers"])
    if not metadata["applied"]:
        metadata["skipped_reason"] = "all_layers_skipped"
    metadata["peak_before_guard"] = float(np.max(np.abs(out)))
    if metadata["peak_before_guard"] > GUARD_LIMIT:
        metadata["guard_gain"] = GUARD_LIMIT / metadata["peak_before_guard"]
        out = out * metadata["guard_gain"]
        metadata["guard_applied"] = True
        metadata["effective_snr_may_change"] = True
    metadata["peak_after_guard"] = float(np.max(np.abs(out)))
    return out, metadata


# --- WAV export -------------------------------------------------------------------


def write_wav(path: Path, audio: np.ndarray, fs: int) -> None:
    """RIFF WAV, 16-bit PCM, one channel per microphone, parents created."""
    if audio.ndim != 2 or audio.shape[0] == 0:
        raise ValueError("refusing to write an empty WAV")
    if not np.all(np.isfinite(audio)):
        raise ValueError("refusing to write non-finite samples")
    path.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(path), audio, fs, subtype="PCM_16", format="WAV")
