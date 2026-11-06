"""Backend-independent T30 from impulse responses (method ``schroeder_backward_integration_v1``).

Per (source, microphone) RIR and octave band:

1. Butterworth band-pass (order 3 per side, second-order sections) between
   ``f/√2`` and ``f·√2``; the upper edge is clipped to 0.95·Nyquist. The filter is
   applied to the time-reversed RIR (backward filtering, less filter-decay bias).
2. Validity: the band must decay by at least 35 dB, i.e. the energy envelope
   at the end of the RIR (last window of ``max(10 ms, 2 periods)``) must be at
   least 35 dB below the envelope peak. A decay truncated before -35 dB is invalid.
3. Schroeder backward integration of the band energy, in dB re. total energy.
4. Least-squares line through the decay curve between its first -5 dB and
   first -35 dB crossings; ``T30 = -60 / slope``.

A band's value is the arithmetic mean over all pairs, and null if any pair is
invalid. No comparison with the Sabine estimate is made.
"""

from collections.abc import Sequence
import math

import numpy as np
from scipy.signal import butter, sosfilt

from acoustic_orchestrator.backends.pyroomacoustics.materials import OCTAVE_CENTER_FREQUENCIES_HZ

METHOD = "schroeder_backward_integration_v1"
FIT_START_DB = -5.0
FIT_END_DB = -35.0
_UPPER_EDGE_NYQUIST_FRACTION = 0.95
_FILTER_ORDER = 3


def octave_band_edges(center_hz: float, fs: int) -> tuple[float, float]:
    low, high = center_hz / math.sqrt(2.0), center_hz * math.sqrt(2.0)
    return low, min(high, _UPPER_EDGE_NYQUIST_FRACTION * fs / 2.0)


def band_filter(rir: np.ndarray, center_hz: float, fs: int) -> np.ndarray:
    low, high = octave_band_edges(center_hz, fs)
    sos = butter(_FILTER_ORDER, [low, high], btype="bandpass", fs=fs, output="sos")
    return sosfilt(sos, np.asarray(rir, dtype=np.float64)[::-1])[::-1]


def _envelope_window(center_hz: float, fs: int) -> int:
    return max(1, int(round(max(0.010, 2.0 / center_hz) * fs)))


def band_t30(rir: np.ndarray, center_hz: float, fs: int) -> float | None:
    """T30 of one band of one RIR, or ``None`` when the decay is invalid."""
    band = band_filter(rir, center_hz, fs)
    energy = band * band
    total = float(energy.sum())
    if not math.isfinite(total) or total <= 0:
        return None
    window = _envelope_window(center_hz, fs)
    if len(energy) < 2 * window:
        return None
    envelope = np.convolve(energy, np.ones(window) / window, mode="valid")
    peak = float(envelope.max())
    if peak <= 0 or 10.0 * math.log10(max(float(envelope[-1]), 1e-300) / peak) > FIT_END_DB:
        return None
    decay = np.cumsum(energy[::-1])[::-1]
    with np.errstate(divide="ignore"):
        decay_db = 10.0 * np.log10(decay / total)
    start = np.flatnonzero(decay_db <= FIT_START_DB)
    end = np.flatnonzero(decay_db <= FIT_END_DB)
    if start.size == 0 or end.size == 0 or end[0] - start[0] < 2:
        return None
    indices = np.arange(start[0], end[0] + 1)
    slope, _ = np.polyfit(indices / fs, decay_db[indices], 1)
    if not math.isfinite(slope) or slope >= 0:
        return None
    return float(-60.0 / slope)


def pair_t30(rir: np.ndarray, fs: int) -> list[float | None]:
    return [band_t30(rir, center, fs) for center in OCTAVE_CENTER_FREQUENCIES_HZ]


def measure_t30(rirs: Sequence[Sequence[np.ndarray]], fs: int) -> tuple[dict, list[list[list[float | None]]]]:
    """Reverberation descriptor plus per-pair values ``[source][microphone][band]``."""
    pairs = [[pair_t30(rir, fs) for rir in row] for row in rirs]
    flat = [values for row in pairs for values in row]
    bands: list[float | None] = []
    for band in range(len(OCTAVE_CENTER_FREQUENCIES_HZ)):
        column = [values[band] for values in flat]
        bands.append(None if not column or any(value is None for value in column)
                     else float(sum(column) / len(column)))
    return reverberation_descriptor(bands), pairs


def reverberation_descriptor(t30_s: Sequence[float | None]) -> dict:
    valid = [value is not None and math.isfinite(value) and value > 0 for value in t30_s]
    all_valid = len(t30_s) == len(OCTAVE_CENTER_FREQUENCIES_HZ) and all(valid)
    return {
        "metric": "T30",
        "estimated_from": "rir",
        "method": METHOD,
        "t30_s": [value if ok else None for value, ok in zip(t30_s, valid, strict=True)],
        "band_frequencies_hz": list(OCTAVE_CENTER_FREQUENCIES_HZ),
        "band_resolution": "octave",
        "frequency_mapping": "center_frequency",
        "valid_band_count": sum(valid),
        "mean_t30_s": sum(t30_s) / len(t30_s) if all_valid else [],  # type: ignore[arg-type]
        "status": "estimated" if all_valid else "unavailable",
        "notes": ("T30 por banda de octava medido desde las RIR de Pyroomacoustics (ISM): filtrado hacia atrás, "
                  "integración de Schroeder y ajuste lineal entre -5 y -35 dB. Media aritmética sobre los pares "
                  "fuente-micrófono; una banda es nula si algún par no decae 35 dB."),
    }
