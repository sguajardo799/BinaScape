"""Versioned synthetic absorption treatments used by the RT30 sampler."""

from __future__ import annotations

import json
import math
from pathlib import Path


CATALOG_VERSION = 1
MIX_MODEL = "area_weighted_linear"
MIX_MODEL_VERSION = 1
MAX_ADJACENT_STEP = 0.08
CATALOG_PATH = Path(__file__).with_name("treatment_catalog_v1.json")

_CATALOG = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
_PRESETS = _CATALOG["presets"]


def preset_ids() -> tuple[str, ...]:
    return tuple(_PRESETS)


def get_preset(preset_id: str, catalog_version: int = CATALOG_VERSION) -> dict:
    if catalog_version != CATALOG_VERSION:
        raise ValueError(f"Versión de catálogo no disponible: {catalog_version}")
    try:
        preset = _PRESETS[preset_id]
    except KeyError as exc:
        raise ValueError(f"Preset desconocido: {preset_id}") from exc
    return {
        **preset,
        "absorption": tuple(preset["absorption"]),
        "preset_id": preset_id,
        "catalog_version": catalog_version,
    }


def validate_catalog(catalog_version: int = CATALOG_VERSION) -> None:
    if _CATALOG.get("catalog_version") != catalog_version:
        raise ValueError(f"El recurso no corresponde al catálogo v{catalog_version}")
    rule = _CATALOG.get("plausibility_rule", {})
    if rule.get("version") != 1 or rule.get("max_adjacent_step") != MAX_ADJACENT_STEP:
        raise ValueError("La regla de plausibilidad del catálogo v1 no coincide con el runtime")
    for preset_id in preset_ids():
        values = get_preset(preset_id, catalog_version)["absorption"]
        if len(values) != 31 or any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
            raise ValueError(f"Preset inválido: {preset_id}")
        if any(abs(right - left) > MAX_ADJACENT_STEP for left, right in zip(values, values[1:])):
            raise ValueError(f"Preset no cumple suavidad v1: {preset_id}")
        if any(right < left for left, right in zip(values, values[1:])):
            raise ValueError(f"Preset no cumple monotonicidad v1: {preset_id}")


def mix_absorption(base: tuple[float, ...], treatment: tuple[float, ...], coverage: float) -> tuple[float, ...]:
    if len(base) != 31 or len(treatment) != 31:
        raise ValueError("La mezcla requiere 31 coeficientes base y de tratamiento")
    if not math.isfinite(coverage) or not 0 <= coverage <= 1:
        raise ValueError("coverage debe ser finita y pertenecer a [0,1]")
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in (*base, *treatment)):
        raise ValueError("Los coeficientes de absorción deben ser finitos y pertenecer a [0,1]")
    result = tuple((1 - coverage) * b + coverage * t for b, t in zip(base, treatment, strict=True))
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in result):
        raise ValueError("La mezcla produjo coeficientes inválidos")
    return result
