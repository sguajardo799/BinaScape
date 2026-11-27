"""Constant-latency measurement and compensation shared by the hearing processors (design D10).

Each processor measures its latency once per configuration by processing an impulse with a
0 dB HL profile, then every output is shifted left by that many samples and zero padded at the
end, so the degraded WAV stays sample aligned with its input and keeps its length.
"""

from collections.abc import Callable, Hashable

import numpy as np


IMPULSE_AMPLITUDE = 0.5
_CACHE: dict[Hashable, int] = {}


def measure_impulse_latency(process: Callable[[np.ndarray], np.ndarray], sample_rate: int) -> int:
    """Samples between an input impulse and the absolute maximum of the processed output."""
    length = int(sample_rate)  # 1 s keeps every FIR tail inside the buffer
    position = length // 4
    impulse = np.zeros(length)
    impulse[position] = IMPULSE_AMPLITUDE
    output = np.asarray(process(impulse), dtype=float)
    return int(np.argmax(np.abs(output[:length]))) - position


def cached_latency(key: Hashable, measure: Callable[[], int]) -> int:
    if key not in _CACHE:
        _CACHE[key] = measure()
    return _CACHE[key]


def compensate_latency(channel: np.ndarray, latency_samples: int, expected_samples: int) -> np.ndarray:
    """Advance ``channel`` by ``latency_samples`` and return exactly ``expected_samples`` samples."""
    channel = np.asarray(channel, dtype=float)
    if latency_samples > 0:
        channel = channel[latency_samples:]
    elif latency_samples < 0:
        channel = np.concatenate((np.zeros(-latency_samples), channel))
    if channel.shape[0] >= expected_samples:
        return channel[:expected_samples]
    return np.pad(channel, (0, expected_samples - channel.shape[0]))
