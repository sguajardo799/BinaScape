"""Cross-component check of the canonical pose convention (spatial-pose-convention).

A fixed_position source is sampled in the public frame, both poses are mapped into RAVEN
coordinates with the adapter contract (positions reflect Z; view vectors are mirrored by
render validation), and the source's angle around the RAVEN up axis is measured relative to the
receiver view. That relative angle must depend only on the configured azimuth, never on yaw.
"""

import math

import pytest

from acoustic_orchestrator.experiment import sampler
from acoustic_orchestrator.pipeline.render_validation import _source_pose

YAWS = [-135.0, -90.0, 0.0, 45.0, 90.0, 180.0]
AZIMUTHS = [-90.0, 0.0, 30.0, 90.0]


def _raven_relative_angle(yaw: float, azimuth: float) -> float:
    center = [2.0, 1.5, 2.0]
    receiver = {"position_m": center, "orientation_deg": {"yaw": yaw, "pitch": 0.0, "roll": 0.0}}
    source = sampler._fixed_position_relative_to_receiver(receiver, (azimuth, 0.0, 1.5))
    receiver_raven = _source_pose(receiver, "3.0")
    source_raven = _source_pose({**receiver, "position_m": source}, "3.0")["position_m"]
    offset = [s - r for s, r in zip(source_raven, receiver_raven["position_m"], strict=True)]
    view = receiver_raven["view_vector"]
    # Signed horizontal angle from view to offset in RAVEN's X/Z plane.
    cross = view[2] * offset[0] - view[0] * offset[2]
    dot = view[0] * offset[0] + view[2] * offset[2]
    return math.degrees(math.atan2(cross, dot))


@pytest.mark.parametrize("yaw", YAWS)
@pytest.mark.parametrize("azimuth", AZIMUTHS)
def test_raven_relative_angle_depends_only_on_azimuth(yaw: float, azimuth: float) -> None:
    reference = _raven_relative_angle(0.0, azimuth)

    assert _raven_relative_angle(yaw, azimuth) == pytest.approx(reference, abs=1e-9)
    assert abs(reference) == pytest.approx(abs(azimuth), abs=1e-9)


@pytest.mark.parametrize("yaw", YAWS)
def test_azimuth_zero_is_in_front_in_raven(yaw: float) -> None:
    assert _raven_relative_angle(yaw, 0.0) == pytest.approx(0.0, abs=1e-9)
