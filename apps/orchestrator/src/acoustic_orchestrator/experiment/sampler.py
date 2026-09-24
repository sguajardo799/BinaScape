import hashlib
import math
import random
from dataclasses import dataclass
from itertools import product
from pathlib import Path
import wave
from typing import Any, TypeVar

from acoustic_orchestrator.config.models import (
    AppConfig,
    BackgroundNoiseStrategyConfig,
    LShapeGeometryConfig,
    ReceiverOutputConfig,
    RoomShapeConfig,
    ShoeboxGeometryConfig,
    SourceOrientationStrategy,
    SourceTypeConfig,
    TrapezoidGeometryConfig,
)
from acoustic_orchestrator.config.validator import (
    derive_materials_root,
    inspect_material_file,
    resolve_background_audio_candidates,
    resolve_material_candidates,
    load_material_absorption_coefficients,
    load_material_scattering_coefficients,
)
from acoustic_orchestrator.experiment.room_acoustics import (
    RT30_GUARD_FREQUENCIES_HZ,  # noqa: F401 - compatibilidad de API para consumidores/tests
    RT30_MEAN_FREQUENCIES_HZ,
    estimate_room_rt30_s,
)
from acoustic_orchestrator.experiment.acoustic_geometry import (
    build_polygon_acoustic_geometry,
    get_acoustic_geometry,
)
from acoustic_orchestrator.experiment.reverberation_sampling import (
    PROPOSAL_STRATEGY_VERSION,
    build_bins,
    build_global_plan,
    classify,
)
from acoustic_orchestrator.experiment.treatment_catalog import get_preset, mix_absorption, preset_ids
from acoustic_orchestrator.experiment.geometry import (
    ABS_COORD_TOL_M,
    build_l_shape_footprint,
    build_shoebox_footprint,
    build_trapezoid_footprint,
    canonical_wall_ids,
    edge_lengths,
    inward_normals,
    inset_polygon,
    point_in_polygon,
    sample_uniform_point,
    wall_clearance,
)


SOURCE_WALL_CLEARANCE_M = 0.5
SOURCE_RECEIVER_CLEARANCE_M = 0.5
T = TypeVar("T")
FixedPositionCase = tuple[float, float, float]


class SceneSamplingSkipped(RuntimeError):
    """Raised when the current scene attempt must be skipped entirely."""


class SceneSamplingFailure(SceneSamplingSkipped):
    def __init__(self, diagnostics: dict) -> None:
        self.diagnostics = diagnostics
        super().__init__(
            "No se pudo generar una escena válida: "
            f"scene_index={diagnostics['scene_index']}; "
            f"stage={diagnostics['stage']}; "
            f"intentos={diagnostics['scene_attempts']}"
        )


@dataclass(frozen=True)
class PlannedSource:
    source_type: SourceTypeConfig
    is_required: bool


def sample_static_scene(
    config: AppConfig,
    rng: random.Random | None,
    scene_index: int,
    *,
    start_scene_attempt: int = 1,
    prior_rejections: dict[str, int] | None = None,
) -> dict:
    del rng  # Scene sampling is intentionally independent from run order.
    effective_seed = _derive_stable_seed(
        config.experiment.random_seed,
        f"scene:{scene_index}",
    )
    if start_scene_attempt < 1:
        raise ValueError("start_scene_attempt debe ser mayor o igual que 1")
    shape_rng = random.Random(_derive_stable_seed(effective_seed, "shape"))
    shape = _sample_room_shape(config, shape_rng)
    distribution = config.room_sampling.reverberation.distribution
    bins = build_bins(distribution.range_s.min, distribution.range_s.max, distribution.bin_width_s)
    _, target_sequence = build_global_plan(max(config.execution.num_simulations, scene_index + 1), bins, config.experiment.random_seed)
    target_bin = bins[target_sequence[scene_index]]
    best_estimate_s: float | None = None
    last_stage = "geometry"
    last_reason = "sin intentos"
    last_parameters: dict = {}
    rejections: dict[str, int] = dict(prior_rejections or {})
    fallback: dict | None = None
    fallback_count = 0

    for scene_attempt in range(start_scene_attempt, config.scene_validation.max_scene_attempts + 1):
        # Each complete scene attempt owns an independent deterministic stream.
        # This lets the render pipeline continue after a late RAVEN rejection
        # without replaying or changing earlier proposals.
        scene_rng = random.Random(
            _derive_stable_seed(effective_seed, f"scene-attempt:{scene_attempt}")
        )
        try:
            room = _sample_room(config, shape, scene_rng, scene_index)
            last_parameters = dict(room["geometry"]["generated_from"])
        except (ValueError, RuntimeError) as exc:
            last_stage = "geometry"
            last_reason = str(exc)
            rejections["geometry"] = rejections.get("geometry", 0) + 1
            continue

        try:
            _apply_virtual_treatments(config, room, target_bin.index, len(bins), scene_rng)
        except ValueError as exc:
            last_stage = "treatment"
            last_reason = str(exc)
            rejections["treatment"] = rejections.get("treatment", 0) + 1
            continue

        try:
            estimate = estimate_room_rt30_s(room)
        except ValueError as exc:
            last_stage = "rt30"
            last_reason = str(exc)
            rejections["rt30_invalid"] = rejections.get("rt30_invalid", 0) + 1
            continue

        estimated_rt30_s = estimate["estimated_rt30_s"]
        if best_estimate_s is None or estimated_rt30_s < best_estimate_s:
            best_estimate_s = estimated_rt30_s
        obtained_bin = classify(estimated_rt30_s, bins)
        if obtained_bin is None:
            last_stage = "rt30"
            last_reason = f"RT30 estimado {estimated_rt30_s:.6g} fuera del rango configurado"
            rejections["rt30_out_of_range"] = rejections.get("rt30_out_of_range", 0) + 1
            continue
        for receiver_attempt in range(1, config.scene_validation.max_receiver_attempts + 1):
            receiver = _sample_receiver(config, room, scene_rng)
            try:
                sources = _sample_sources(config, room, receiver, scene_rng, scene_index)
            except SceneSamplingSkipped as exc:
                last_stage = "fixed_position" if "fixed_position" in str(exc) else "sources"
                last_reason = str(exc)
                rejections[last_stage] = rejections.get(last_stage, 0) + 1
                continue
            if not _is_valid_scene(config, room, receiver, sources):
                last_stage = "sources"
                last_reason = "receptor o fuentes no cumplen las restricciones geométricas"
                rejections["sources"] = rejections.get("sources", 0) + 1
                continue

            hrtfs = _sample_hrtfs(config, scene_rng)
            candidate = {
                "room": room,
                "receiver": receiver | {"hrtfs": hrtfs},
                "sources": sources,
                "reverberation_sampling": _build_reverberation_sampling(
                    config, room, estimate, target_bin, obtained_bin, scene_attempt,
                    obtained_bin.index != target_bin.index, effective_seed, scene_index, rejections,
                ),
                "sampling": {
                    "effective_seed": effective_seed,
                    "scene_attempt": scene_attempt,
                    "receiver_attempt": receiver_attempt,
                    "shape_type": shape.type,
                },
            }
            if obtained_bin.index == target_bin.index:
                return candidate
            rejections["rt30_other_bin"] = rejections.get("rt30_other_bin", 0) + 1
            fallback_count += 1
            if scene_rng.randrange(fallback_count) == 0:
                fallback = candidate
            break

        last_stage = "receiver" if last_stage not in {"sources", "fixed_position"} else last_stage

    if fallback is not None:
        fallback["reverberation_sampling"]["attempts"] = config.scene_validation.max_scene_attempts
        fallback["reverberation_sampling"]["rejections_by_reason"] = dict(sorted(rejections.items()))
        return fallback

    raise SceneSamplingFailure(
        {
            "scene_index": scene_index,
            "effective_seed": effective_seed,
            "shape_type": shape.type,
            "stage": last_stage,
            "scene_attempts": config.scene_validation.max_scene_attempts,
            "first_scene_attempt": start_scene_attempt,
            "receiver_attempts_per_scene": config.scene_validation.max_receiver_attempts,
            "last_parameters": last_parameters,
            "last_reason": last_reason,
            "best_estimated_rt30_s": best_estimate_s,
            "requested_bin": target_bin.as_dict(),
            "rejections_by_reason": dict(sorted(rejections.items())),
        }
    )


def _build_reverberation_sampling(
    config: AppConfig, room: dict, estimate: dict, requested_bin, obtained_bin, sampling_attempt: int,
    fallback_used: bool, effective_seed: int, scene_index: int, rejections: dict[str, int],
) -> dict:
    return {
        "requested_bin": requested_bin.as_dict(),
        "obtained_bin": obtained_bin.as_dict(),
        "matched_requested_bin": requested_bin.index == obtained_bin.index,
        "fallback_used": fallback_used,
        "attempts": sampling_attempt,
        "estimator": "sabine",
        "estimator_version": "sabine_polygon_octaves_v4",
        "aggregation": "arithmetic_mean",
        "mean_bands_hz": list(RT30_MEAN_FREQUENCIES_HZ),
        "estimated_mean_rt30_s": estimate["estimated_rt30_s"],
        "rt30_by_band_s": {
            str(frequency): value
            for frequency, value in estimate["rt30_by_band_s"].items()
        },
        "geometry": _estimate_geometry_metadata(room, estimate),
        "treatment_catalog_version": config.room_sampling.reverberation.treatment.catalog_version,
        "absorption_mix_model": "area_weighted_linear",
        "absorption_mix_model_version": 1,
        "proposal_strategy_version": PROPOSAL_STRATEGY_VERSION,
        "effective_seed": effective_seed,
        "scene_index": scene_index,
        "rejections_by_reason": dict(sorted(rejections.items())),
    }


def _estimate_geometry_metadata(room: dict, estimate: dict) -> dict:
    acoustic_geometry = get_acoustic_geometry(room)
    floor_area = estimate.get("floor_area_m2", acoustic_geometry["floor_area_m2"])
    surface_areas = estimate.get("surface_areas_m2")
    if surface_areas is None:
        surface_areas = {
            surface_id: surface["area_m2"]
            for surface_id, surface in acoustic_geometry["surfaces"].items()
        }
    return {
        "floor_area_m2": floor_area,
        "volume_m3": estimate.get("volume_m3", acoustic_geometry["volume_m3"]),
        "surface_areas_m2": surface_areas,
    }


def _apply_virtual_treatments(config: AppConfig, room: dict, target_index: int, bin_count: int, rng: random.Random) -> None:
    treatment_config = config.room_sampling.reverberation.treatment
    acoustic_geometry = get_acoustic_geometry(room)
    surface_contract = acoustic_geometry["surfaces"]
    if set(surface_contract) != set(room["material_files"]):
        raise ValueError("material_files no coincide con acoustic_geometry.surfaces")
    # Fixed v1 proposal tiers: dry / middle / reverberant. Draws are never
    # changed in response to an observed RT30 error.
    dryness = 1.0 - (target_index / max(1, bin_count - 1))
    ids = preset_ids()
    acoustic_surfaces = {}
    for surface_id, material in room["material_files"].items():
        material_path = Path(material["material_path"])
        base_absorption = load_material_absorption_coefficients(material_path)
        base_scattering = load_material_scattering_coefficients(material_path)
        surface_type = surface_contract[surface_id]["surface_type"]
        eligible = surface_type in treatment_config.eligible_surface_types
        preset = None
        coverage = 0.0
        if eligible:
            coverage_range = treatment_config.ceiling_coverage if surface_type == "ceiling" else treatment_config.wall_coverage
            treat_probability = 0.15 + 0.8 * dryness
            if not treatment_config.allow_none or rng.random() < treat_probability:
                weights = ([1, 3, 7] if dryness >= 2 / 3 else [2, 6, 2] if dryness >= 1 / 3 else [7, 2, 1])
                preset_id = rng.choices(ids, weights=weights, k=1)[0]
                preset = get_preset(preset_id, treatment_config.catalog_version)
                raw = rng.random()
                shaped = raw ** (0.5 if dryness >= 2 / 3 else 1.0 if dryness >= 1 / 3 else 2.0)
                coverage = coverage_range.min + shaped * (coverage_range.max - coverage_range.min)
        treatment_absorption = tuple(preset["absorption"]) if preset else base_absorption
        effective_absorption = mix_absorption(base_absorption, treatment_absorption, coverage)
        surface_area = float(surface_contract[surface_id]["area_m2"])
        acoustic_surfaces[surface_id] = {
            "surface_area_m2": surface_area,
            "base_material": {"material_id": material["material_id"], "material_path": material_path, "absorption": list(base_absorption), "scattering": list(base_scattering)},
            "treatment": {"preset_id": preset["preset_id"] if preset else "none", "catalog_version": treatment_config.catalog_version, "coverage": coverage, "treated_area_m2": surface_area * coverage, "absorption": list(treatment_absorption)},
            "effective_absorption": list(effective_absorption),
            "effective_scattering": list(base_scattering),
        }
    room["acoustic_surfaces"] = acoustic_surfaces


def build_background_noise_plan(config: AppConfig, num_scenes: int) -> list[dict]:
    background_noise = config.background_noise
    if not background_noise.enabled:
        return [_disabled_background_noise() for _ in range(num_scenes)]

    strategies = list(enumerate(background_noise.strategies))
    if not strategies:
        return [_disabled_background_noise() for _ in range(num_scenes)]

    rng = random.Random(_derive_stable_seed(config.experiment.random_seed, "background_noise"))
    plans: list[dict[str, Any]] = [
        {"enabled": True, "layers": []} for _ in range(num_scenes)
    ]

    if background_noise.allow_multiple_layers:
        for strategy_index, strategy in strategies:
            variants = _balanced_sequence(_strategy_variants(strategy), num_scenes, rng)
            for scene_index, variant in enumerate(variants):
                plans[scene_index]["layers"].append(
                    _build_background_noise_layer(strategy, variant, len(plans[scene_index]["layers"]) + 1, background_noise.snr_db)
                )
        return plans

    strategy_sequence = _balanced_sequence(strategies, num_scenes, rng)
    selected_counts = {strategy_index: 0 for strategy_index, _ in strategies}
    for strategy_index, _ in strategy_sequence:
        selected_counts[strategy_index] += 1

    variant_sequences = {
        strategy_index: _balanced_sequence(_strategy_variants(strategy), selected_counts[strategy_index], rng)
        for strategy_index, strategy in strategies
    }
    variant_offsets = {strategy_index: 0 for strategy_index, _ in strategies}

    for scene_index, (strategy_index, strategy) in enumerate(strategy_sequence):
        variant_index = variant_offsets[strategy_index]
        variant_offsets[strategy_index] += 1
        plans[scene_index]["layers"].append(
            _build_background_noise_layer(
                strategy,
                variant_sequences[strategy_index][variant_index],
                1,
                background_noise.snr_db,
            )
        )

    return plans


def _disabled_background_noise() -> dict:
    return {"enabled": False, "layers": []}


def _derive_stable_seed(seed: int, label: str) -> int:
    digest = hashlib.sha256(f"{seed}:{label}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _balanced_sequence(items: list[T], count: int, rng: random.Random) -> list[T]:
    if count <= 0:
        return []
    sequence = [items[index % len(items)] for index in range(count)]
    rng.shuffle(sequence)
    return sequence


def _strategy_variants(strategy: BackgroundNoiseStrategyConfig) -> list[str | Path]:
    if strategy.type == "colored":
        variants: list[str | Path] = list(strategy.colors)
        return variants
    audio_variants: list[str | Path] = list(resolve_background_audio_candidates(strategy))
    return audio_variants


def _build_background_noise_layer(
    strategy: BackgroundNoiseStrategyConfig,
    variant: str | Path,
    layer_index: int,
    snr_db: float,
) -> dict:
    layer = {
        "noise_id": f"noise_{layer_index:04d}",
        "strategy": strategy.type,
        "type": str(variant) if strategy.type == "colored" else strategy.noise_type,
        "snr_db": snr_db,
    }
    if strategy.type == "colored":
        layer["color"] = str(variant)
    else:
        layer["path"] = Path(variant).resolve().as_posix()
    return layer


def _sample_room_shape(config: AppConfig, rng: random.Random) -> RoomShapeConfig:
    threshold = rng.random()
    cumulative = 0.0
    for shape in config.room_sampling.geometry.shape_mix:
        cumulative += shape.probability
        if threshold <= cumulative:
            return shape
    return config.room_sampling.geometry.shape_mix[-1]


def _sample_room(
    config: AppConfig,
    shape: RoomShapeConfig,
    rng: random.Random,
    scene_index: int,
) -> dict:
    geometry = _sample_room_geometry(config, shape, rng)
    # Geometry rejection happens before material draws so rejected parameter
    # samples consume RNG in one stable, documented order.
    inset_polygon(
        [tuple(vertex) for vertex in geometry["footprint_vertices_m"]],
        SOURCE_WALL_CLEARANCE_M,
    )
    materials_root = derive_materials_root(config)
    if materials_root is None:
        raise RuntimeError("No se pudo derivar assets/materials para el muestreo de materiales")

    materials = _sample_surface_materials(config, geometry["wall_ids"], rng)
    return {
        "room_id": f"room_{scene_index + 1:04d}",
        "geometry": geometry,
        "acoustic_geometry": build_polygon_acoustic_geometry(geometry),
        "materials": materials,
        "material_files": {
            surface_id: {
                "material_id": material_id,
                "material_path": _select_material_file(materials_root, material_id, rng),
            }
            for surface_id, material_id in materials.items()
        },
    }


def _sample_room_geometry(
    config: AppConfig,
    shape: RoomShapeConfig,
    rng: random.Random,
) -> dict:
    height_m = _uniform(
        rng,
        config.room_sampling.geometry.height_m.min,
        config.room_sampling.geometry.height_m.max,
    )
    generated_from: dict[str, float | str]
    if isinstance(shape, ShoeboxGeometryConfig):
        length_m = _uniform(rng, shape.length_m.min, shape.length_m.max)
        width_m = _uniform(rng, shape.width_m.min, shape.width_m.max)
        footprint = build_shoebox_footprint(length_m, width_m)
        generated_from = {"length_m": length_m, "width_m": width_m}
    elif isinstance(shape, TrapezoidGeometryConfig):
        base_a_m = _uniform(rng, shape.base_a_m.min, shape.base_a_m.max)
        base_b_m = _uniform(rng, shape.base_b_m.min, shape.base_b_m.max)
        depth_m = _uniform(rng, shape.depth_m.min, shape.depth_m.max)
        top_offset_m = _uniform(rng, shape.top_offset_m.min, shape.top_offset_m.max)
        footprint = build_trapezoid_footprint(base_a_m, base_b_m, depth_m, top_offset_m)
        generated_from = {
            "base_a_m": base_a_m,
            "base_b_m": base_b_m,
            "depth_m": depth_m,
            "top_offset_m": top_offset_m,
        }
    else:
        assert isinstance(shape, LShapeGeometryConfig)
        outer_length_m = _uniform(rng, shape.outer_length_m.min, shape.outer_length_m.max)
        outer_width_m = _uniform(rng, shape.outer_width_m.min, shape.outer_width_m.max)
        cutout_length_m = _uniform(rng, shape.cutout_length_m.min, shape.cutout_length_m.max)
        cutout_width_m = _uniform(rng, shape.cutout_width_m.min, shape.cutout_width_m.max)
        removed_corner = rng.choice(shape.removed_corners)
        if cutout_length_m >= outer_length_m or cutout_width_m >= outer_width_m:
            raise ValueError("El recorte L debe ser menor que el rectángulo exterior")
        if (
            outer_length_m - cutout_length_m <= SOURCE_WALL_CLEARANCE_M * 2
            or outer_width_m - cutout_width_m <= SOURCE_WALL_CLEARANCE_M * 2
        ):
            raise ValueError("La sala L no deja brazos útiles mayores que 1.0 m")
        footprint = build_l_shape_footprint(
            outer_length_m,
            outer_width_m,
            cutout_length_m,
            cutout_width_m,
            removed_corner,
        )
        generated_from = {
            "outer_length_m": outer_length_m,
            "outer_width_m": outer_width_m,
            "cutout_length_m": cutout_length_m,
            "cutout_width_m": cutout_width_m,
            "removed_corner": removed_corner,
        }

    return {
        "type": shape.type,
        "height_m": height_m,
        "footprint_vertices_m": [[x, z] for x, z in footprint],
        "wall_ids": canonical_wall_ids(footprint),
        "generated_from": generated_from,
    }


def _sample_surface_materials(
    config: AppConfig,
    wall_ids: list[str],
    rng: random.Random,
) -> dict[str, str]:
    materials = {
        wall_id: rng.choice(config.room_sampling.materials.walls)
        for wall_id in wall_ids
    }
    materials["floor"] = rng.choice(config.room_sampling.materials.floor)
    materials["ceiling"] = rng.choice(config.room_sampling.materials.ceiling)
    return materials


def _select_material_file(materials_root: Path, material_id: str, rng: random.Random) -> Path:
    candidates = resolve_material_candidates(materials_root, material_id)
    if not candidates:
        raise RuntimeError(
            f"No se encontraron archivos .mat para room_sampling.materials.{material_id}: {materials_root / material_id}"
        )

    invalid_candidates: list[str] = []
    for candidate in rng.sample(candidates, k=len(candidates)):
        candidate_errors = inspect_material_file(candidate)
        if not candidate_errors:
            return candidate
        invalid_candidates.append(f"{candidate}: {'; '.join(candidate_errors)}")

    raise RuntimeError(
        f"No hay materiales válidos para material_id={material_id} en {materials_root / material_id}. "
        f"Candidatos inválidos: {' | '.join(invalid_candidates)}"
    )


def _sample_receiver(config: AppConfig, room: dict, rng: random.Random) -> dict:
    margins = config.receiver_sampling.position_strategy.margin_m
    orientation_strategy = config.receiver_sampling.orientation_strategy
    footprint = _room_footprint(room)
    usable_footprint = inset_polygon(footprint, max(margins.x, margins.z))
    x, z = sample_uniform_point(usable_footprint, rng)
    position = [
        round(x, 6),
        _uniform(
            rng,
            config.receiver_sampling.position_strategy.fixed_height_m.min,
            config.receiver_sampling.position_strategy.fixed_height_m.max,
        ),
        round(z, 6),
    ]

    yaw = _uniform(
        rng,
        orientation_strategy.yaw_deg.min,
        orientation_strategy.yaw_deg.max,
    )
    if orientation_strategy.type == "random_yaw":
        pitch = orientation_strategy.pitch_deg.fixed
        roll = orientation_strategy.roll_deg.fixed
    else:
        pitch = _uniform(
            rng,
            orientation_strategy.pitch_deg.min,
            orientation_strategy.pitch_deg.max,
        )
        roll = _uniform(
            rng,
            orientation_strategy.roll_deg.min,
            orientation_strategy.roll_deg.max,
        )

    return {
        "receiver_id": "listener_001",
        "position_m": position,
        "orientation_deg": {
            "yaw": yaw,
            "pitch": pitch,
            "roll": roll,
        },
    }


def _sample_hrtfs(config: AppConfig, rng: random.Random) -> list[dict]:
    hrtfs: list[dict] = []
    for output_name, output in _enabled_outputs(config).items():
        matches = sorted(output.ir_catalog_path.glob(output.file_pattern))
        selected_files = [rng.choice(matches)] if output.num_hrtfs == 1 else rng.sample(matches, output.num_hrtfs)
        for index, selected in enumerate(selected_files, start=1):
            hrtfs.append(
                {
                    "hrtf_id": output_name if output.num_hrtfs == 1 else f"{output_name}__{index}",
                    "hrtf_path": selected,
                }
            )
    return hrtfs


def _sample_sources(
    config: AppConfig,
    room: dict,
    receiver: dict,
    rng: random.Random,
    scene_index: int = 0,
) -> list[dict]:
    target_count = rng.randint(config.source_sampling.min_sources, config.source_sampling.max_sources)
    planned_sources = _choose_source_types(config, target_count, rng)
    audio_pools = _build_audio_pools([planned.source_type for planned in planned_sources])
    fixed_positions = _assign_fixed_positions(config, planned_sources, room, receiver, scene_index)
    fixed_indexes: dict[str, int] = {}
    sources: list[dict] = []

    for planned_source in planned_sources:
        source_type = planned_source.source_type
        placement: tuple[list[float], str | None, tuple[float, float] | None] | None
        if source_type.spatial_policy.type == "fixed_position":
            fixed_index = fixed_indexes.get(source_type.event_type, 0)
            fixed_indexes[source_type.event_type] = fixed_index + 1
            placement = (fixed_positions[source_type.event_type][fixed_index], None, None)
        else:
            placement = _sample_source_position(config, source_type, room, receiver, rng)
        if placement is None:
            if planned_source.is_required:
                raise SceneSamplingSkipped(
                    f"No se pudo ubicar la fuente requerida {source_type.event_type} respetando min_radius_from_receiver_m"
                )
            continue

        position, wall_id, wall_normal = placement
        orientation = _sample_source_orientation(config, source_type, rng, wall_normal)
        audio_path = _draw_audio_path(source_type, audio_pools, rng)
        sources.append(
            {
                "source_id": f"src_{len(sources) + 1:04d}",
                "event_type": source_type.event_type,
                "audio_path": audio_path,
                "position_m": position,
                "orientation_deg": orientation,
                "gain_db": _uniform(rng, config.source_sampling.gain_db.min, config.source_sampling.gain_db.max),
                "start_time_s": _sample_start_time_s(config, audio_path, rng),
                **({"target_wall_id": wall_id} if wall_id is not None else {}),
                **({"directivity": source_type.directivity} if source_type.directivity is not None else {}),
            }
        )

    if not sources:
        raise SceneSamplingSkipped("La escena generada debe contener al menos una fuente")

    return sources


def _choose_source_types(config: AppConfig, target_count: int, rng: random.Random) -> list[PlannedSource]:
    planned_counts = _plan_source_type_counts(config, target_count, rng)
    selected: list[PlannedSource] = []

    for source_type in config.source_sampling.source_types:
        for index in range(planned_counts[source_type.event_type]):
            selected.append(PlannedSource(source_type=source_type, is_required=index < source_type.min_count))

    rng.shuffle(selected)
    return selected


def _plan_source_type_counts(config: AppConfig, target_count: int, rng: random.Random) -> dict[str, int]:
    source_types = config.source_sampling.source_types
    planned_counts = {source_type.event_type: source_type.min_count for source_type in source_types}
    mandatory_total = sum(planned_counts.values())
    reachable_max = sum(source_type.max_count for source_type in source_types)
    effective_max_total = min(config.source_sampling.max_sources, reachable_max)

    if mandatory_total > effective_max_total:
        raise RuntimeError("No se pudo planificar la escena: los mínimos obligatorios exceden el máximo permitido")

    required_total = max(mandatory_total, config.source_sampling.min_sources)
    if required_total > effective_max_total:
        raise RuntimeError("No se pudo planificar la escena: la configuración global exige más fuentes que las alcanzables")

    desired_total = min(max(target_count, mandatory_total), effective_max_total)
    remaining_optional_slots = desired_total - mandatory_total

    optional_requests: dict[str, int] = {}
    for source_type in source_types:
        extra_capacity = source_type.max_count - source_type.min_count
        if extra_capacity <= 0:
            continue
        if rng.random() > source_type.probability:
            continue
        optional_requests[source_type.event_type] = rng.randint(1, extra_capacity)

    if remaining_optional_slots > 0 and optional_requests:
        requested_events = [
            event_type
            for event_type, requested_count in optional_requests.items()
            for _ in range(requested_count)
        ]
        rng.shuffle(requested_events)
        for event_type in requested_events[:remaining_optional_slots]:
            planned_counts[event_type] += 1

    while sum(planned_counts.values()) < required_total:
        remaining_candidates = [
            source_type
            for source_type in source_types
            if planned_counts[source_type.event_type] < source_type.max_count
        ]
        if not remaining_candidates:
            raise RuntimeError("No se pudo completar el mínimo global de fuentes sin violar los máximos por tipo")
        chosen = rng.choice(remaining_candidates)
        planned_counts[chosen.event_type] += 1

    if sum(planned_counts.values()) > config.source_sampling.max_sources:
        raise RuntimeError("No se pudo planificar la escena sin exceder source_sampling.max_sources")

    return planned_counts


def _build_audio_pools(selected_types: list[SourceTypeConfig]) -> dict[str, dict[str, list[Path]]]:
    audio_pools: dict[str, dict[str, list[Path]]] = {}
    for source_type in selected_types:
        if source_type.event_type in audio_pools:
            continue
        files = _list_files(source_type.audio_dir)
        if not files:
            raise ValueError(f"No hay archivos WAV en audio_dir: {source_type.audio_dir}")
        audio_pools[source_type.event_type] = {
            "all_files": files,
            "unused_files": list(files),
        }
    return audio_pools


def _draw_audio_path(
    source_type: SourceTypeConfig,
    audio_pools: dict[str, dict[str, list[Path]]],
    rng: random.Random,
) -> Path:
    pool = audio_pools[source_type.event_type]
    if not pool["unused_files"]:
        pool["unused_files"] = list(pool["all_files"])

    chosen = rng.choice(pool["unused_files"])
    pool["unused_files"].remove(chosen)
    return chosen


def _sample_source_position(
    config: AppConfig,
    source_type: SourceTypeConfig,
    room: dict,
    receiver: dict,
    rng: random.Random,
) -> tuple[list[float], str | None, tuple[float, float] | None] | None:
    policy_type = source_type.spatial_policy.type
    if policy_type == "fixed_position":
        raise RuntimeError("fixed_position debe asignarse conjuntamente para preservar cobertura y separación")
    if policy_type == "weighted_targets":
        target = _weighted_choice(source_type.spatial_policy.targets or {}, rng)
        if target == "wall":
            return _sample_wall_position(room, rng)
        return _sample_random_position(room, rng), None, None

    if policy_type == "random_valid_away_from_receiver":
        position = _sample_random_position_away_from_receiver(config, source_type, room, receiver, rng)
        if position is None:
            return None
        return position, None, None

    return _sample_random_position(room, rng), None, None


def _assign_fixed_positions(
    config: AppConfig,
    planned_sources: list[PlannedSource],
    room: dict,
    receiver: dict,
    scene_index: int,
) -> dict[str, list[list[float]]]:
    counts: dict[str, int] = {}
    source_types: dict[str, SourceTypeConfig] = {}
    for planned_source in planned_sources:
        source_type = planned_source.source_type
        if source_type.spatial_policy.type != "fixed_position":
            continue
        counts[source_type.event_type] = counts.get(source_type.event_type, 0) + 1
        source_types[source_type.event_type] = source_type

    if not counts:
        return {}

    slots: list[tuple[str, int, list[FixedPositionCase]]] = []
    cases_by_event: dict[str, list[FixedPositionCase]] = {}

    # Prefer the scheduled case for every policy. If scheduled cases from
    # different fixed policies conflict, backtracking may choose a deterministic
    # alternative for this scene; that local exception can reduce block coverage.
    for event_type, source_type in source_types.items():
        cases = _fixed_position_cases(source_type)
        count = counts[event_type]
        if count > len(cases):
            raise RuntimeError(
                f"{event_type}: la escena requiere {count} fuentes fixed_position, "
                f"pero solo hay {len(cases)} puntos únicos"
            )
        cases_by_event[event_type] = cases
        scheduled = _scheduled_fixed_case(config, event_type, cases, scene_index)
        alternatives = [candidate for candidate in cases if candidate != scheduled]
        _fixed_rng(config, event_type, scene_index, "primary-alternatives").shuffle(alternatives)
        slots.append((event_type, 0, [scheduled, *alternatives]))

    for event_type, count in counts.items():
        cases = cases_by_event[event_type]
        for slot_index in range(1, count):
            candidates = list(cases)
            _fixed_rng(config, event_type, scene_index, f"extra-{slot_index}").shuffle(candidates)
            slots.append((event_type, slot_index, candidates))

    chosen_cases: dict[str, set[FixedPositionCase]] = {event_type: set() for event_type in counts}
    chosen: list[tuple[str, int, FixedPositionCase, list[float]]] = []
    minimum_separation = config.scene_validation.min_distance_between_sources_m

    def search(slot_index: int) -> bool:
        if slot_index == len(slots):
            return True

        event_type, source_index, candidates = slots[slot_index]
        for candidate in candidates:
            if candidate in chosen_cases[event_type]:
                continue
            position = _fixed_position_relative_to_receiver(receiver, candidate)
            if any(
                _same_position(position, other_position)
                or _distance(position, other_position) < minimum_separation
                for _, _, _, other_position in chosen
            ):
                continue

            chosen_cases[event_type].add(candidate)
            chosen.append((event_type, source_index, candidate, position))
            if search(slot_index + 1):
                return True
            chosen.pop()
            chosen_cases[event_type].remove(candidate)
        return False

    if not search(0):
        raise RuntimeError(
            "No existe una asignación de puntos fixed_position distintos que respete "
            "scene_validation.min_distance_between_sources_m"
        )

    positions: dict[str, list[list[float]]] = {
        event_type: [[0.0, 0.0, 0.0] for _ in range(count)]
        for event_type, count in counts.items()
    }
    for event_type, source_index, _, position in chosen:
        _validate_assigned_fixed_position(config, event_type, room, receiver, position, scene_index)
        positions[event_type][source_index] = position
    return positions


def _fixed_position_cases(source_type: SourceTypeConfig) -> list[FixedPositionCase]:
    policy = source_type.spatial_policy
    if not policy.azimuths_deg or not policy.elevations_deg or not policy.distances_m:
        raise RuntimeError(f"{source_type.event_type}: configuración fixed_position incompleta")
    return list(product(policy.azimuths_deg, policy.elevations_deg, policy.distances_m))


def _scheduled_fixed_case(
    config: AppConfig,
    event_type: str,
    cases: list[FixedPositionCase],
    scene_index: int,
) -> FixedPositionCase:
    block_index, offset = divmod(scene_index, len(cases))
    shuffled = list(cases)
    _fixed_rng(config, event_type, block_index, "block").shuffle(shuffled)
    return shuffled[offset]


def _fixed_rng(config: AppConfig, event_type: str, index: int, purpose: str) -> random.Random:
    seed_material = (
        f"{config.experiment.random_seed}|fixed_position|{event_type}|{purpose}|{index}"
    ).encode("utf-8")
    seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
    return random.Random(seed)


def _fixed_position_relative_to_receiver(
    receiver: dict,
    fixed_case: FixedPositionCase,
) -> list[float]:
    azimuth_deg, elevation_deg, distance_m = fixed_case
    orientation = receiver["orientation_deg"]
    yaw = math.radians(orientation["yaw"] + azimuth_deg)
    pitch = math.radians(orientation["pitch"] + elevation_deg)

    # Manifests use canonical XYZ and the runtime adapter flips Z for RAVEN.
    # Therefore this is MATLAB's [+X forward, +Y up, +Z at positive yaw]
    # convention expressed before that adapter. Receiver roll is intentionally ignored.
    offset = (
        math.cos(pitch) * math.cos(yaw),
        math.sin(pitch),
        -math.cos(pitch) * math.sin(yaw),
    )
    return [
        coordinate + distance_m * direction
        for coordinate, direction in zip(receiver["position_m"], offset, strict=True)
    ]


def _validate_assigned_fixed_position(
    config: AppConfig,
    event_type: str,
    room: dict,
    receiver: dict,
    position: list[float],
    scene_index: int,
) -> None:
    invalid_reason: str | None = None
    if not _inside_room(position, room):
        invalid_reason = "queda fuera de la sala"
    elif _source_wall_clearance(position, room) < SOURCE_WALL_CLEARANCE_M - ABS_COORD_TOL_M:
        invalid_reason = f"no respeta {SOURCE_WALL_CLEARANCE_M:g} m de margen a paredes"
    elif _distance(position, receiver["position_m"]) < max(
        config.scene_validation.min_distance_source_to_receiver_m,
        SOURCE_RECEIVER_CLEARANCE_M,
    ):
        invalid_reason = "no respeta la distancia mínima al receptor"

    if invalid_reason is not None:
        raise SceneSamplingSkipped(
            f"{event_type}: el punto fixed_position asignado en scene_index={scene_index} {invalid_reason}: {position}"
        )


def _same_position(point_a: list[float], point_b: list[float]) -> bool:
    return _distance(point_a, point_b) <= 1e-12


def _sample_random_position_away_from_receiver(
    config: AppConfig,
    source_type: SourceTypeConfig,
    room: dict,
    receiver: dict,
    rng: random.Random,
) -> list[float] | None:
    radius = source_type.spatial_policy.min_radius_from_receiver_m
    if radius is None:
        raise RuntimeError("random_valid_away_from_receiver requiere min_radius_from_receiver_m")

    receiver_position = receiver["position_m"]
    for _ in range(config.scene_validation.max_receiver_attempts):
        position = _sample_random_position(room, rng)
        if _distance(position, receiver_position) >= radius:
            return position

    fallback = _farthest_inset_box_corner(room, receiver_position)
    if _distance(fallback, receiver_position) >= radius:
        return fallback
    return None


def _farthest_inset_box_corner(room: dict, receiver_position: list[float]) -> list[float]:
    footprint = inset_polygon(_room_footprint(room), SOURCE_WALL_CLEARANCE_M)
    height_m = _room_height(room)
    candidates = [
        [x, y, z]
        for x, z in footprint
        for y in (SOURCE_WALL_CLEARANCE_M, height_m - SOURCE_WALL_CLEARANCE_M)
    ]
    return max(candidates, key=lambda candidate: _distance(candidate, receiver_position))


def _sample_random_position(room: dict, rng: random.Random) -> list[float]:
    x, z = sample_uniform_point(
        inset_polygon(_room_footprint(room), SOURCE_WALL_CLEARANCE_M),
        rng,
    )
    height_m = _room_height(room)
    return [
        round(x, 6),
        _uniform(rng, SOURCE_WALL_CLEARANCE_M, height_m - SOURCE_WALL_CLEARANCE_M),
        round(z, 6),
    ]


def _sample_wall_position(
    room: dict,
    rng: random.Random,
) -> tuple[list[float], str, tuple[float, float]] | None:
    footprint = _room_footprint(room)
    lengths = edge_lengths(footprint)
    threshold = rng.random() * sum(lengths)
    cumulative = 0.0
    selected_index = len(lengths) - 1
    for index, length in enumerate(lengths):
        cumulative += length
        if threshold <= cumulative:
            selected_index = index
            break

    start = footprint[selected_index]
    end = footprint[(selected_index + 1) % len(footprint)]
    normal = inward_normals(footprint)[selected_index]
    interpolation = rng.random()
    x = start[0] + interpolation * (end[0] - start[0]) + SOURCE_WALL_CLEARANCE_M * normal[0]
    z = start[1] + interpolation * (end[1] - start[1]) + SOURCE_WALL_CLEARANCE_M * normal[1]
    position = [
        round(x, 6),
        _uniform(rng, SOURCE_WALL_CLEARANCE_M, _room_height(room) - SOURCE_WALL_CLEARANCE_M),
        round(z, 6),
    ]
    if not _inside_room(position, room):
        return None
    if _source_wall_clearance(position, room) < SOURCE_WALL_CLEARANCE_M - ABS_COORD_TOL_M:
        return None
    return position, room["geometry"]["wall_ids"][selected_index], normal


def _sample_source_orientation(
    config: AppConfig,
    source_type: SourceTypeConfig,
    rng: random.Random,
    wall_normal: tuple[float, float] | None,
) -> dict:
    strategy = source_type.orientation_strategy or config.source_sampling.default_orientation_strategy
    if strategy.type == "facing_surface_normal" and wall_normal is not None:
        return _wall_normal_orientation(wall_normal)

    return _random_orientation(strategy, rng)


def _random_orientation(strategy: SourceOrientationStrategy, rng: random.Random) -> dict:
    return {
        "yaw": _uniform(rng, strategy.yaw_deg.min, strategy.yaw_deg.max) if strategy.yaw_deg else 0.0,
        "pitch": strategy.pitch_deg.fixed if strategy.pitch_deg else 0.0,
        "roll": strategy.roll_deg.fixed if strategy.roll_deg else 0.0,
    }


def _wall_normal_orientation(normal: tuple[float, float]) -> dict:
    yaw = math.degrees(math.atan2(-normal[1], normal[0]))
    return {"yaw": round(yaw, 6), "pitch": 0.0, "roll": 0.0}


def _is_valid_scene(config: AppConfig, room: dict, receiver: dict, sources: list[dict]) -> bool:
    if not sources:
        return False

    if config.scene_validation.require_receiver_inside_room and not _inside_room(receiver["position_m"], room):
        return False

    if config.scene_validation.require_sources_inside_room:
        if any(not _inside_room(source["position_m"], room) for source in sources):
            return False

    if any(
        _distance(source["position_m"], receiver["position_m"])
        < max(config.scene_validation.min_distance_source_to_receiver_m, SOURCE_RECEIVER_CLEARANCE_M)
        for source in sources
    ):
        return False

    if any(
        _source_wall_clearance(source["position_m"], room)
        < SOURCE_WALL_CLEARANCE_M - ABS_COORD_TOL_M
        for source in sources
    ):
        return False

    for index, source in enumerate(sources):
        for other_source in sources[index + 1 :]:
            if (
                _distance(source["position_m"], other_source["position_m"])
                < config.scene_validation.min_distance_between_sources_m
            ):
                return False

    return True


def _inside_room(position: list[float], room: dict) -> bool:
    return (
        -ABS_COORD_TOL_M <= position[1] <= _room_height(room) + ABS_COORD_TOL_M
        and point_in_polygon((position[0], position[2]), _room_footprint(room))
    )


def _distance(point_a: list[float], point_b: list[float]) -> float:
    return math.sqrt(sum((coord_a - coord_b) ** 2 for coord_a, coord_b in zip(point_a, point_b, strict=True)))


def _source_wall_clearance(position: list[float], room: dict) -> float:
    height_m = _room_height(room)
    return min(
        wall_clearance((position[0], position[2]), _room_footprint(room)),
        position[1],
        height_m - position[1],
    )


def _room_footprint(room: dict) -> list[tuple[float, float]]:
    return [tuple(vertex) for vertex in room["geometry"]["footprint_vertices_m"]]


def _room_height(room: dict) -> float:
    return room["geometry"]["height_m"]


def _weighted_choice(weights: dict[str, float], rng: random.Random) -> str:
    threshold = rng.random()
    cumulative = 0.0
    for name, weight in weights.items():
        cumulative += weight
        if threshold <= cumulative:
            return name
    return next(reversed(weights))


def _enabled_outputs(config: AppConfig) -> dict[str, ReceiverOutputConfig]:
    outputs = {
        output_name: output
        for output_name, output in {
            "binaural_hrtf": config.receiver_outputs.binaural_hrtf,
            "bte_rear_hartf": config.receiver_outputs.bte_rear_hartf,
            "bte_front_hartf": config.receiver_outputs.bte_front_hartf,
        }.items()
        if output is not None
    }
    return {name: output for name, output in outputs.items() if output.enabled}


def _list_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".wav")


def _sample_start_time_s(config: AppConfig, audio_path: Path, rng: random.Random) -> float:
    # Fixed-duration renders also read/trim audio when offsets are disabled.
    effective_max = _max_allowed_start_time_s(config, audio_path)
    if not config.source_sampling.timing.allow_offsets:
        return 0.0

    configured_min = config.source_sampling.timing.start_time_s.min
    configured_max = config.source_sampling.timing.start_time_s.max
    allowed_max = min(configured_max, effective_max)

    if allowed_max <= configured_min:
        return round(max(0.0, allowed_max), 6)

    return _uniform(rng, configured_min, allowed_max)


def _max_allowed_start_time_s(config: AppConfig, audio_path: Path) -> float:
    total_duration_s = config.source_sampling.timing.total_duration_s
    if total_duration_s is None:
        return config.source_sampling.timing.start_time_s.max

    source_duration_s = _wav_duration_s(audio_path)
    effective_duration_s = min(source_duration_s, total_duration_s)
    return max(0.0, total_duration_s - effective_duration_s)


def _wav_duration_s(audio_path: Path) -> float:
    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            return wav_file.getnframes() / wav_file.getframerate()
    except (OSError, EOFError, wave.Error) as exc:
        cause = str(exc) or type(exc).__name__
        raise ValueError(
            f"No se pudo leer la duracion del audio WAV {audio_path}: {cause}. "
            "El calculo de duracion y recorte requiere WAV PCM sin compresion."
        ) from exc


def _uniform(rng: random.Random, minimum: float, maximum: float) -> float:
    return round(rng.uniform(minimum, maximum), 6)
