import hashlib
import math
import random
from dataclasses import dataclass
from pathlib import Path
import wave
from typing import TypeVar

from acoustic_orchestrator.config.models import (
    AppConfig,
    BackgroundNoiseStrategyConfig,
    ReceiverOutputConfig,
    SourceOrientationStrategy,
    SourceTypeConfig,
)
from acoustic_orchestrator.config.validator import (
    derive_materials_root,
    inspect_material_file,
    resolve_background_audio_candidates,
    resolve_material_candidates,
)


SURFACE_ORDER = ("north_wall", "south_wall", "east_wall", "west_wall", "floor", "ceiling")
SOURCE_WALL_CLEARANCE_M = 0.5
SOURCE_RECEIVER_CLEARANCE_M = 0.5
T = TypeVar("T")


class SceneSamplingSkipped(RuntimeError):
    """Raised when the current scene attempt must be skipped entirely."""


@dataclass(frozen=True)
class PlannedSource:
    source_type: SourceTypeConfig
    is_required: bool


def sample_static_scene(config: AppConfig, rng: random.Random, scene_index: int) -> dict:
    room = _sample_room(config, rng)
    receiver = _sample_receiver(config, room, rng)
    hrtfs = _sample_hrtfs(config, rng)

    sources = None
    attempts = config.scene_validation.max_sampling_attempts_per_scene
    for _ in range(attempts):
        sources = _sample_sources(config, room, receiver, rng)
        if _is_valid_scene(config, room, receiver, sources):
            break
    else:
        raise RuntimeError(f"No se pudo generar una escena válida tras {attempts} intentos para scene_index={scene_index}")

    return {
        "room": room,
        "receiver": receiver | {"hrtfs": hrtfs},
        "sources": sources,
    }


def build_background_noise_plan(config: AppConfig, num_scenes: int) -> list[dict]:
    background_noise = config.background_noise
    if not background_noise.enabled:
        return [_disabled_background_noise() for _ in range(num_scenes)]

    strategies = list(enumerate(background_noise.strategies))
    if not strategies:
        return [_disabled_background_noise() for _ in range(num_scenes)]

    rng = random.Random(_derive_stable_seed(config.experiment.random_seed, "background_noise"))
    plans = [{"enabled": True, "layers": []} for _ in range(num_scenes)]

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
        return list(strategy.colors)
    return resolve_background_audio_candidates(strategy)


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


def _sample_room(config: AppConfig, rng: random.Random) -> dict:
    dimensions = {
        "length": _uniform(rng, config.room_sampling.dimensions_m.length.min, config.room_sampling.dimensions_m.length.max),
        "width": _uniform(rng, config.room_sampling.dimensions_m.width.min, config.room_sampling.dimensions_m.width.max),
        "height": _uniform(rng, config.room_sampling.dimensions_m.height.min, config.room_sampling.dimensions_m.height.max),
    }

    materials_root = derive_materials_root(config)
    if materials_root is None:
        raise RuntimeError("No se pudo derivar assets/materials para el muestreo de materiales")

    materials = _sample_surface_materials(config, rng)
    return {
        "room_id": f"room_{rng.randint(1, 9999):04d}",
        "dimensions": dimensions,
        "materials": materials,
        "material_files": {
            surface_id: {
                "material_id": material_id,
                "material_path": _select_material_file(materials_root, material_id, rng),
            }
            for surface_id, material_id in materials.items()
        },
    }


def _sample_surface_materials(config: AppConfig, rng: random.Random) -> dict[str, str]:
    materials: dict[str, str] = {}
    semantic_surfaces = config.room_sampling.semantic_surfaces

    if semantic_surfaces.enable_walls:
        for surface_id in SURFACE_ORDER[:4]:
            materials[surface_id] = rng.choice(config.room_sampling.materials.walls)

    if semantic_surfaces.enable_floor:
        materials["floor"] = rng.choice(config.room_sampling.materials.floor)

    if semantic_surfaces.enable_ceiling:
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
    dimensions = room["dimensions"]
    position = [
        _uniform(rng, margins.x, dimensions["length"] - margins.x),
        _uniform(
            rng,
            config.receiver_sampling.position_strategy.fixed_height_m.min,
            config.receiver_sampling.position_strategy.fixed_height_m.max,
        ),
        _uniform(rng, margins.z, dimensions["width"] - margins.z),
    ]

    return {
        "receiver_id": "listener_001",
        "position_m": position,
        "orientation_deg": {
            "yaw": _uniform(
                rng,
                config.receiver_sampling.orientation_strategy.yaw_deg.min,
                config.receiver_sampling.orientation_strategy.yaw_deg.max,
            ),
            "pitch": config.receiver_sampling.orientation_strategy.pitch_deg.fixed,
            "roll": config.receiver_sampling.orientation_strategy.roll_deg.fixed,
        },
    }


def _sample_hrtfs(config: AppConfig, rng: random.Random) -> list[dict]:
    hrtfs: list[dict] = []
    for output_name, output in _enabled_outputs(config).items():
        matches = sorted(output.ir_catalog_path.glob(output.file_pattern))
        selected = rng.choice(matches)
        hrtfs.append(
            {
                "hrtf_id": output_name,
                "hrtf_path": selected,
            }
        )
    return hrtfs


def _sample_sources(config: AppConfig, room: dict, receiver: dict, rng: random.Random) -> list[dict]:
    target_count = rng.randint(config.source_sampling.min_sources, config.source_sampling.max_sources)
    planned_sources = _choose_source_types(config, target_count, rng)
    audio_pools = _build_audio_pools([planned.source_type for planned in planned_sources])
    sources: list[dict] = []

    for planned_source in planned_sources:
        source_type = planned_source.source_type
        placement = _sample_source_position(config, source_type, room, receiver, rng)
        if placement is None:
            if planned_source.is_required:
                raise SceneSamplingSkipped(
                    f"No se pudo ubicar la fuente requerida {source_type.event_type} respetando min_radius_from_receiver_m"
                )
            continue

        position, wall_name = placement
        orientation = _sample_source_orientation(config, source_type, rng, wall_name)
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
                **({"directivity": source_type.directivity} if source_type.directivity is not None else {}),
            }
        )

    if len(sources) <= 1:
        raise SceneSamplingSkipped("La escena generada debe contener más de una fuente")

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
) -> tuple[list[float], str | None] | None:
    policy_type = source_type.spatial_policy.type
    if policy_type == "weighted_targets":
        target = _weighted_choice(source_type.spatial_policy.targets or {}, rng)
        if target == "wall":
            return _sample_wall_position(room, rng)
        return _sample_random_position(room, rng), None

    if policy_type == "random_valid_away_from_receiver":
        position = _sample_random_position_away_from_receiver(config, source_type, room, receiver, rng)
        if position is None:
            return None
        return position, None

    return _sample_random_position(room, rng), None


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
    for _ in range(config.scene_validation.max_sampling_attempts_per_scene):
        position = _sample_random_position(room, rng)
        if _distance(position, receiver_position) >= radius:
            return position

    fallback = _farthest_inset_box_corner(room, receiver_position)
    if _distance(fallback, receiver_position) >= radius:
        return fallback
    return None


def _farthest_inset_box_corner(room: dict, receiver_position: list[float]) -> list[float]:
    dimensions = room["dimensions"]
    candidates = [
        [x, y, z]
        for x in (SOURCE_WALL_CLEARANCE_M, dimensions["length"] - SOURCE_WALL_CLEARANCE_M)
        for y in (SOURCE_WALL_CLEARANCE_M, dimensions["height"] - SOURCE_WALL_CLEARANCE_M)
        for z in (SOURCE_WALL_CLEARANCE_M, dimensions["width"] - SOURCE_WALL_CLEARANCE_M)
    ]
    return max(candidates, key=lambda candidate: _distance(candidate, receiver_position))


def _sample_random_position(room: dict, rng: random.Random) -> list[float]:
    dimensions = room["dimensions"]
    return [
        _uniform(rng, SOURCE_WALL_CLEARANCE_M, dimensions["length"] - SOURCE_WALL_CLEARANCE_M),
        _uniform(rng, SOURCE_WALL_CLEARANCE_M, dimensions["height"] - SOURCE_WALL_CLEARANCE_M),
        _uniform(rng, SOURCE_WALL_CLEARANCE_M, dimensions["width"] - SOURCE_WALL_CLEARANCE_M),
    ]


def _sample_wall_position(room: dict, rng: random.Random) -> tuple[list[float], str]:
    dimensions = room["dimensions"]
    wall = rng.choice(["north_wall", "south_wall", "east_wall", "west_wall"])
    if wall == "north_wall":
        return [
            _uniform(rng, SOURCE_WALL_CLEARANCE_M, dimensions["length"] - SOURCE_WALL_CLEARANCE_M),
            _uniform(rng, SOURCE_WALL_CLEARANCE_M, dimensions["height"] - SOURCE_WALL_CLEARANCE_M),
            dimensions["width"] - SOURCE_WALL_CLEARANCE_M,
        ], wall
    if wall == "south_wall":
        return [
            _uniform(rng, SOURCE_WALL_CLEARANCE_M, dimensions["length"] - SOURCE_WALL_CLEARANCE_M),
            _uniform(rng, SOURCE_WALL_CLEARANCE_M, dimensions["height"] - SOURCE_WALL_CLEARANCE_M),
            SOURCE_WALL_CLEARANCE_M,
        ], wall
    if wall == "east_wall":
        return [
            dimensions["length"] - SOURCE_WALL_CLEARANCE_M,
            _uniform(rng, SOURCE_WALL_CLEARANCE_M, dimensions["height"] - SOURCE_WALL_CLEARANCE_M),
            _uniform(rng, SOURCE_WALL_CLEARANCE_M, dimensions["width"] - SOURCE_WALL_CLEARANCE_M),
        ], wall
    return [
        SOURCE_WALL_CLEARANCE_M,
        _uniform(rng, SOURCE_WALL_CLEARANCE_M, dimensions["height"] - SOURCE_WALL_CLEARANCE_M),
        _uniform(rng, SOURCE_WALL_CLEARANCE_M, dimensions["width"] - SOURCE_WALL_CLEARANCE_M),
    ], wall


def _sample_source_orientation(
    config: AppConfig,
    source_type: SourceTypeConfig,
    rng: random.Random,
    wall_name: str | None,
) -> dict:
    strategy = source_type.orientation_strategy or config.source_sampling.default_orientation_strategy
    if strategy.type == "facing_surface_normal" and wall_name:
        return _wall_normal_orientation(wall_name)

    return _random_orientation(strategy, rng)


def _random_orientation(strategy: SourceOrientationStrategy, rng: random.Random) -> dict:
    return {
        "yaw": _uniform(rng, strategy.yaw_deg.min, strategy.yaw_deg.max) if strategy.yaw_deg else 0.0,
        "pitch": strategy.pitch_deg.fixed if strategy.pitch_deg else 0.0,
        "roll": strategy.roll_deg.fixed if strategy.roll_deg else 0.0,
    }


def _wall_normal_orientation(wall_name: str) -> dict:
    yaw_by_wall = {
        "north_wall": -90.0,
        "south_wall": 90.0,
        "east_wall": 180.0,
        "west_wall": 0.0,
    }
    return {"yaw": yaw_by_wall[wall_name], "pitch": 0.0, "roll": 0.0}


def _is_valid_scene(config: AppConfig, room: dict, receiver: dict, sources: list[dict]) -> bool:
    dimensions = room["dimensions"]
    if len(sources) <= 1:
        return False

    if config.scene_validation.require_receiver_inside_room and not _inside_room(receiver["position_m"], dimensions):
        return False

    if config.scene_validation.require_sources_inside_room:
        if any(not _inside_room(source["position_m"], dimensions) for source in sources):
            return False

    if any(
        _distance(source["position_m"], receiver["position_m"])
        < max(config.scene_validation.min_distance_source_to_receiver_m, SOURCE_RECEIVER_CLEARANCE_M)
        for source in sources
    ):
        return False

    if any(_source_wall_clearance(source["position_m"], dimensions) < SOURCE_WALL_CLEARANCE_M for source in sources):
        return False

    for index, source in enumerate(sources):
        for other_source in sources[index + 1 :]:
            if (
                _distance(source["position_m"], other_source["position_m"])
                < config.scene_validation.min_distance_between_sources_m
            ):
                return False

    return True


def _inside_room(position: list[float], dimensions: dict) -> bool:
    return (
        0.0 <= position[0] <= dimensions["length"]
        and 0.0 <= position[1] <= dimensions["height"]
        and 0.0 <= position[2] <= dimensions["width"]
    )


def _distance(point_a: list[float], point_b: list[float]) -> float:
    return math.sqrt(sum((coord_a - coord_b) ** 2 for coord_a, coord_b in zip(point_a, point_b, strict=True)))


def _source_wall_clearance(position: list[float], dimensions: dict) -> float:
    return min(
        position[0],
        dimensions["length"] - position[0],
        position[1],
        dimensions["height"] - position[1],
        position[2],
        dimensions["width"] - position[2],
    )


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
    return sorted(path for path in directory.iterdir() if path.is_file())


def _sample_start_time_s(config: AppConfig, audio_path: Path, rng: random.Random) -> float:
    if not config.source_sampling.timing.allow_offsets:
        return 0.0

    configured_min = config.source_sampling.timing.start_time_s.min
    configured_max = config.source_sampling.timing.start_time_s.max
    effective_max = _max_allowed_start_time_s(config, audio_path)
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
    with wave.open(str(audio_path), "rb") as wav_file:
        return wav_file.getnframes() / wav_file.getframerate()


def _uniform(rng: random.Random, minimum: float, maximum: float) -> float:
    return round(rng.uniform(minimum, maximum), 6)
