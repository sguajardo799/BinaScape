"""Declarative per-backend render capabilities; never imports a backend package.

Config validation, render validation and the output index read this table so
backend-specific literals (sample rates, the T30 descriptor, the metadata
identity) live in one place. RAVEN values reproduce the contract that existed
before backends were selectable, so RAVEN outcomes are unchanged.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

DEFAULT_RENDER_BACKEND = "raven"
DEFAULT_SAMPLE_RATE_HZ = 44100


@dataclass(frozen=True)
class RenderBackend:
    name: str
    supported_sample_rates: frozenset[int]
    t30_method: str
    t30_estimated_from: str
    # Required ``summary.render_backend`` value; None means not checked (RAVEN).
    summary_render_backend: str | None
    receiver_output_types: frozenset[str]
    # Rate assumed by validation when a legacy manifest omits ``render.sample_rate_hz``.
    default_sample_rate: int = DEFAULT_SAMPLE_RATE_HZ
    # Distribution name and supported half-open version range [min, max).
    package: str | None = None
    package_min_version: str | None = None
    package_max_version: str | None = None

    @property
    def package_requirement(self) -> str | None:
        if self.package is None:
            return None
        return f"{self.package}>={self.package_min_version},<{self.package_max_version}"


RENDER_BACKENDS: dict[str, RenderBackend] = {
    "raven": RenderBackend(
        name="raven",
        supported_sample_rates=frozenset({44100}),
        t30_method="rpf.getT30",
        t30_estimated_from="room",
        summary_render_backend=None,
        receiver_output_types=frozenset({"binaural_hrtf", "bte_rear_hartf", "bte_front_hartf", "circular_array"}),
    ),
    "pyroomacoustics": RenderBackend(
        name="pyroomacoustics",
        supported_sample_rates=frozenset({44100}),
        t30_method="schroeder_backward_integration_v1",
        t30_estimated_from="rir",
        summary_render_backend="Pyroomacoustics",
        receiver_output_types=frozenset({"circular_array"}),
        package="pyroomacoustics",
        package_min_version="0.10.1",
        package_max_version="0.11",
    ),
}


def get_render_backend(name: str | None) -> RenderBackend:
    """Resolve a backend name; ``None`` means the default (RAVEN)."""
    key = DEFAULT_RENDER_BACKEND if name is None else name
    try:
        return RENDER_BACKENDS[key]
    except (KeyError, TypeError):
        raise ValueError(f"unknown render backend: {name!r}") from None


def manifest_render_backend(manifest: Mapping[str, Any]) -> RenderBackend:
    """Backend selected by a scene/runtime manifest; an absent key is RAVEN."""
    render = manifest.get("render")
    name = render.get("backend") if isinstance(render, Mapping) else None
    return get_render_backend(name)


_RELEASE = re.compile(r"\d+(?:\.\d+)*")


def parse_release(version: str) -> tuple[int, ...] | None:
    """Plain release versions only; pre/dev/post/local versions are rejected."""
    if not isinstance(version, str) or _RELEASE.fullmatch(version) is None:
        return None
    parts = [int(part) for part in version.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def version_in_range(version: str, minimum: str, maximum: str) -> bool:
    observed, low, high = parse_release(version), parse_release(minimum), parse_release(maximum)
    if observed is None or low is None or high is None:
        return False
    return low <= observed < high
