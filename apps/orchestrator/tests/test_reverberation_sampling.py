import hashlib
import json
import math
from pathlib import Path

import pytest

from acoustic_orchestrator.experiment.reverberation_sampling import (
    build_bins,
    build_global_plan,
    classify,
    summarize,
)
from acoustic_orchestrator.experiment.treatment_catalog import (
    CATALOG_PATH,
    MAX_ADJACENT_STEP,
    get_preset,
    mix_absorption,
    preset_ids,
    validate_catalog,
)


def test_build_bins_is_stable_and_last_bin_includes_upper_bound() -> None:
    bins = build_bins(0.1, 1.2, 0.1)
    assert len(bins) == 11
    assert classify(0.1, bins).index == 0
    assert classify(0.2, bins).index == 1
    assert classify(0.3, bins).index == 2
    assert classify(1.2, bins).index == 10
    assert classify(1.2000000001, bins) is None


def test_build_bins_rejects_a_genuinely_non_integral_partition() -> None:
    with pytest.raises(ValueError, match="debe ser entero"):
        build_bins(0.1, 1.2, 0.3)


def test_build_bins_applies_absolute_tolerance_in_seconds() -> None:
    build_bins(0.1, 1.2000000005, 0.1)

    with pytest.raises(ValueError, match="debe ser entero"):
        build_bins(1.0, 11.000000005, 10.0)


def test_global_plan_balances_and_is_reproducible_even_when_n_is_smaller_than_k() -> None:
    bins = build_bins(0.1, 1.2, 0.1)
    quotas, sequence = build_global_plan(7, bins, 42)
    assert max(quotas) - min(quotas) <= 1
    assert sum(quotas) == len(sequence) == 7
    assert (quotas, sequence) == build_global_plan(7, bins, 42)


def test_batch_summary_marks_soft_tolerance_and_resolution_without_failing() -> None:
    result = summarize(
        [1, 1],
        [2, 0],
        0.1,
        fallback_count=1,
        failed_count=0,
        rejections_by_reason={"out_of_range": 3, "invalid_geometry": 1},
    )
    assert [item["absolute_deviation"] for item in result["bins"]] == [1, 1]
    assert result["bins"][0]["above_tolerance"] is True
    assert result["insufficient_statistical_resolution"] is True
    assert result["status"] == "complete_with_deviation"
    assert result["rejections_by_reason"] == {"invalid_geometry": 1, "out_of_range": 3}


@pytest.mark.parametrize(
    ("observed", "failed_count", "expected_status"),
    [
        ([1, 1], 0, "complete"),
        ([1, 0], 1, "partial"),
        ([0, 0], 2, "failed"),
    ],
)
def test_batch_summary_status(observed: list[int], failed_count: int, expected_status: str) -> None:
    result = summarize([1, 1], observed, 1.0, fallback_count=0, failed_count=failed_count)
    assert result["status"] == expected_status


def test_catalog_v1_is_valid_smooth_and_snapshot_stable() -> None:
    validate_catalog(1)
    assert hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest() == (
        "193de39944beee77b33b4735bcd475c145b3835d8988c6bc1cd43cf782be3120"
    )
    assert preset_ids() == ("broadband_light", "broadband_medium", "broadband_strong")
    assert get_preset("broadband_medium")["absorption"][0] == 0.18
    for preset_id in preset_ids():
        values = get_preset(preset_id)["absorption"]
        assert len(values) == 31
        assert max(abs(a - b) for a, b in zip(values, values[1:])) <= MAX_ADJACENT_STEP
        assert all(right >= left for left, right in zip(values, values[1:]))


def test_mix_has_exact_endpoints_and_no_clamp() -> None:
    base = tuple([0.2] * 31)
    treatment = tuple([0.8] * 31)
    assert mix_absorption(base, treatment, 0) == base
    assert mix_absorption(base, treatment, 1) == treatment
    assert all(math.isclose(value, 0.35) for value in mix_absorption(base, treatment, 0.25))
    with pytest.raises(ValueError, match="coverage"):
        mix_absorption(base, treatment, 1.01)


def test_schema3_example_uses_catalog_v1_and_seven_band_mean() -> None:
    example_path = Path(__file__).resolve().parents[3] / "apps/orchestrator/examples/example_scene_static.json"
    example = json.loads(example_path.read_text(encoding="utf-8"))

    for surface in example["room"]["acoustic_surfaces"].values():
        treatment = surface["treatment"]
        expected = (
            surface["base_material"]["absorption"]
            if treatment["preset_id"] == "none"
            else list(get_preset(treatment["preset_id"])["absorption"])
        )
        assert treatment["catalog_version"] == 1
        assert treatment["absorption"] == expected

    sampling = example["reverberation_sampling"]
    selected = [sampling["rt30_by_band_s"][str(frequency)] for frequency in sampling["mean_bands_hz"]]
    assert sampling["estimated_mean_rt30_s"] == pytest.approx(sum(selected) / len(selected))
