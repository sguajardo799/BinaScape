"""Pure deterministic planning utilities for global RT30 stratification."""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from decimal import Decimal


BIN_ABS_TOL_S = 1e-9
BIN_REL_TOL = 1e-12
PROPOSAL_STRATEGY_VERSION = 1


@dataclass(frozen=True)
class Rt30Bin:
    index: int
    lower_s: float
    upper_s: float
    include_upper: bool = False

    def contains(self, value: float) -> bool:
        return self.lower_s <= value <= self.upper_s if self.include_upper else self.lower_s <= value < self.upper_s

    def as_dict(self) -> dict:
        return {"index": self.index, "lower_s": self.lower_s, "upper_s": self.upper_s}


def build_bins(min_s: float, max_s: float, bin_width_s: float) -> tuple[Rt30Bin, ...]:
    if any(not math.isfinite(v) or v <= 0 for v in (min_s, max_s, bin_width_s)) or min_s >= max_s:
        raise ValueError("El rango y ancho de bins deben ser finitos, positivos y min < max")
    span_s = max_s - min_s
    ratio = span_s / bin_width_s
    count = round(ratio)
    reconstructed_span_s = count * bin_width_s
    if count < 1 or not math.isclose(
        span_s,
        reconstructed_span_s,
        rel_tol=BIN_REL_TOL,
        abs_tol=BIN_ABS_TOL_S,
    ):
        raise ValueError("(max_s - min_s) / bin_width_s debe ser entero")

    decimal_min = Decimal(str(min_s))
    decimal_width = Decimal(str(bin_width_s))
    return tuple(
        Rt30Bin(
            i,
            float(decimal_min + i * decimal_width),
            max_s if i == count - 1 else float(decimal_min + (i + 1) * decimal_width),
            i == count - 1,
        )
        for i in range(count)
    )


def classify(value: float, bins: tuple[Rt30Bin, ...]) -> Rt30Bin | None:
    if not math.isfinite(value):
        return None
    return next((candidate for candidate in bins if candidate.contains(value)), None)


def build_global_plan(count: int, bins: tuple[Rt30Bin, ...], seed: int) -> tuple[list[int], list[int]]:
    if count < 0 or not bins:
        raise ValueError("Se requiere count >= 0 y al menos un bin")
    rng = random.Random(_stable_seed(seed, "rt30-global-plan-v1"))
    order = list(range(len(bins)))
    rng.shuffle(order)
    base, remainder = divmod(count, len(bins))
    quotas = [base] * len(bins)
    for index in order[:remainder]:
        quotas[index] += 1
    sequence = [index for index, quota in enumerate(quotas) for _ in range(quota)]
    rng.shuffle(sequence)
    return quotas, sequence


def summarize(
    quotas: list[int],
    observed: list[int],
    tolerance: float,
    fallback_count: int,
    failed_count: int,
    rejections_by_reason: dict[str, int] | None = None,
) -> dict:
    bins = []
    insufficient = False
    above_tolerance = False
    for index, (target, actual) in enumerate(zip(quotas, observed, strict=True)):
        deviation = abs(actual - target)
        relative = deviation / max(1, target)
        resolution = 1 / max(1, target)
        insufficient |= resolution > tolerance
        bin_above_tolerance = relative > tolerance
        above_tolerance |= bin_above_tolerance
        bins.append({"index": index, "target_count": target, "observed_count": actual, "absolute_deviation": deviation, "relative_deviation": relative, "quota_resolution_fraction": resolution, "above_tolerance": bin_above_tolerance})

    if failed_count:
        status = "partial" if sum(observed) else "failed"
    elif above_tolerance:
        status = "complete_with_deviation"
    else:
        status = "complete"

    return {
        "status": status,
        "bins": bins,
        "fallback_count": fallback_count,
        "failed_count": failed_count,
        "rejections_by_reason": dict(sorted((rejections_by_reason or {}).items())),
        "insufficient_statistical_resolution": insufficient,
    }


def _stable_seed(seed: int, label: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{seed}:{label}".encode()).digest()[:8], "big")
