"""Generate the MSBG reference used by test_legacy_active_rescale_reproduces_previous_output_bit_exactly.

msbg_legacy_output.wav was produced with the MSBG integration that predates level_handling and
outer_middle_ear (commit 15e123f: Ear(equiv_0db_spl=70), no latency compensation). Re-running this
script now goes through legacy_active_rescale + free_field + 90 dB, which must reproduce it.
"""
from pathlib import Path

import numpy as np
import soundfile as sf

from clarity_backend import msbg

HERE = Path(__file__).parent
PROFILE = {
    "hearing_profile_id": "legacy_reference",
    "left_loss_db_by_band": {"250": 10.0, "500": 15.0, "1000": 20.0, "2000": 30.0, "4000": 40.0, "8000": 50.0},
    "right_loss_db_by_band": {"250": 20.0, "500": 25.0, "1000": 30.0, "2000": 40.0, "4000": 50.0, "8000": 60.0},
}


def build_input() -> np.ndarray:
    fs = 44100
    rng = np.random.default_rng(1234)
    t = np.arange(int(0.6 * fs)) / fs
    gate = ((t // 0.1) % 2 == 0).astype(float)  # 100 ms bursts with 100 ms pauses
    left = gate * (0.2 * np.sin(2 * np.pi * 500 * t) + 0.05 * rng.standard_normal(t.size))
    right = 0.5 * gate * (0.2 * np.sin(2 * np.pi * 1500 * t) + 0.05 * rng.standard_normal(t.size))
    return np.column_stack((left, right))


if __name__ == "__main__":
    sf.write(HERE / "msbg_legacy_input.wav", build_input(), 44100, subtype="PCM_16")
    left, right, _ = msbg.build_audiograms(PROFILE)
    msbg.process_wav(
        HERE / "msbg_legacy_input.wav", HERE / "msbg_legacy_output.wav", left, right,
        effective_scale_db_spl=90.0, outer_middle_ear="free_field", level_handling="legacy_active_rescale",
    )
