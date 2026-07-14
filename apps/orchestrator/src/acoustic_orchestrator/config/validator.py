from functools import lru_cache
from math import isclose, isfinite
from pathlib import Path, PurePath
import re
from typing import Any, TypedDict

import yaml

from .models import AppConfig, AudioFolderNoiseStrategyConfig, ClarityRunnerConfig, ReceiverOutputConfig, SourceTypeConfig
from acoustic_orchestrator.pipeline.output_paths import find_unsupported_placeholders, is_safe_output_subdir


MIN_SOURCE_WALL_CLEARANCE_M = 0.5
MIN_SOURCE_RECEIVER_DISTANCE_M = 0.5
HEARING_PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SUPPORTED_BACKGROUND_AUDIO_SUFFIXES = {".wav"}


class HearingProfileDefinition(TypedDict):
    hearing_profile_id: str
    left_loss_db_by_band: dict[str, float]
    right_loss_db_by_band: dict[str, float]


def validate_config(config: AppConfig) -> None:
    errors: list[str] = []
    materials_root: Path | None = None

    if config.experiment.scene_type != "static":
        errors.append("Solo se soporta experiment.scene_type=static en esta versión")

    if config.execution.num_simulations <= 0:
        errors.append("execution.num_simulations debe ser > 0")

    if config.execution.num_workers <= 0:
        errors.append("execution.num_workers debe ser > 0")

    if config.render.sample_rate_hz <= 0:
        errors.append("render.sample_rate_hz debe ser > 0")

    _validate_background_noise(errors, config)

    if config.scene_validation.max_sampling_attempts_per_scene <= 0:
        errors.append("scene_validation.max_sampling_attempts_per_scene debe ser > 0")

    if not config.receiver_sampling.one_receiver_per_scene:
        errors.append("Este MVP requiere receiver_sampling.one_receiver_per_scene=true")

    _validate_range(errors, "room_sampling.dimensions_m.length", config.room_sampling.dimensions_m.length.min, config.room_sampling.dimensions_m.length.max)
    _validate_range(errors, "room_sampling.dimensions_m.width", config.room_sampling.dimensions_m.width.min, config.room_sampling.dimensions_m.width.max)
    _validate_range(errors, "room_sampling.dimensions_m.height", config.room_sampling.dimensions_m.height.min, config.room_sampling.dimensions_m.height.max)
    _validate_range(errors, "receiver_sampling.position_strategy.fixed_height_m", config.receiver_sampling.position_strategy.fixed_height_m.min, config.receiver_sampling.position_strategy.fixed_height_m.max)
    _validate_range(errors, "source_sampling.timing.start_time_s", config.source_sampling.timing.start_time_s.min, config.source_sampling.timing.start_time_s.max)
    _validate_range(errors, "source_sampling.gain_db", config.source_sampling.gain_db.min, config.source_sampling.gain_db.max)
    _validate_range(errors, "receiver_sampling.orientation_strategy.yaw_deg", config.receiver_sampling.orientation_strategy.yaw_deg.min, config.receiver_sampling.orientation_strategy.yaw_deg.max)

    if (
        config.source_sampling.timing.total_duration_s is not None
        and config.source_sampling.timing.total_duration_s <= 0
    ):
        errors.append("source_sampling.timing.total_duration_s debe ser > 0")

    if config.receiver_sampling.position_strategy.margin_m.x * 2 >= config.room_sampling.dimensions_m.length.max:
        errors.append("receiver_sampling.position_strategy.margin_m.x es demasiado grande para la longitud máxima del cuarto")

    if config.receiver_sampling.position_strategy.margin_m.z * 2 >= config.room_sampling.dimensions_m.width.max:
        errors.append("receiver_sampling.position_strategy.margin_m.z es demasiado grande para el ancho máximo del cuarto")

    if config.receiver_sampling.position_strategy.fixed_height_m.max > config.room_sampling.dimensions_m.height.max:
        errors.append("receiver_sampling.position_strategy.fixed_height_m.max no puede exceder room_sampling.dimensions_m.height.max")

    if config.source_sampling.min_sources > config.source_sampling.max_sources:
        errors.append("source_sampling.min_sources no puede ser mayor que max_sources")

    if config.source_sampling.min_sources <= 1:
        errors.append("source_sampling.min_sources debe ser > 1")

    for axis_name, dimension_range in {
        "length": config.room_sampling.dimensions_m.length,
        "width": config.room_sampling.dimensions_m.width,
        "height": config.room_sampling.dimensions_m.height,
    }.items():
        if dimension_range.min < MIN_SOURCE_WALL_CLEARANCE_M * 2:
            errors.append(
                f"room_sampling.dimensions_m.{axis_name}.min debe ser >= {MIN_SOURCE_WALL_CLEARANCE_M * 2:.1f} "
                "para mantener 0.5 m de separación mínima entre fuentes y paredes"
            )

    if config.scene_validation.min_distance_source_to_receiver_m < MIN_SOURCE_RECEIVER_DISTANCE_M:
        errors.append(
            "scene_validation.min_distance_source_to_receiver_m debe ser >= 0.5 "
            "para mantener la separación mínima entre fuentes y receptor"
        )

    defined_outputs = {
        output_name: output
        for output_name, output in {
            "binaural_hrtf": config.receiver_outputs.binaural_hrtf,
            "bte_rear_hartf": config.receiver_outputs.bte_rear_hartf,
            "bte_front_hartf": config.receiver_outputs.bte_front_hartf,
        }.items()
        if output is not None
    }

    enabled_outputs = {
        output_name: output
        for output_name, output in defined_outputs.items()
        if output.enabled
    }

    if not enabled_outputs:
        errors.append("Debe haber al menos un receiver_output habilitado")

    for output_name, output in defined_outputs.items():
        if output.required and not output.enabled:
            errors.append(f"receiver_outputs.{output_name} no puede ser required=true si enabled=false")
        _validate_receiver_output(errors, output_name, output)

    _validate_output_naming(errors, config, enabled_outputs)

    for target_name in config.hearing_degradation.input_targets:
        if target_name not in defined_outputs:
            errors.append(f"hearing_degradation.input_targets contiene target desconocido: {target_name}")

    if config.hearing_degradation.enabled:
        if not config.hearing_degradation.input_targets:
            errors.append("hearing_degradation.input_targets debe incluir al menos un target cuando enabled=true")

        for target_name in config.hearing_degradation.input_targets:
            if target_name in defined_outputs and not defined_outputs[target_name].enabled:
                errors.append(
                    f"hearing_degradation.input_targets no puede apuntar a receiver_outputs.{target_name} si enabled=false"
                )

        _validate_clarity_runner(errors, config.hearing_degradation.runner)

    min_total_sources = 0
    max_total_sources = 0
    for source_type in config.source_sampling.source_types:
        _validate_source_type(errors, source_type)
        min_total_sources += source_type.min_count
        max_total_sources += source_type.max_count

    if min_total_sources > config.source_sampling.max_sources:
        errors.append("La suma de source_types.min_count excede source_sampling.max_sources")

    if max_total_sources < config.source_sampling.min_sources:
        errors.append("La suma de source_types.max_count no alcanza source_sampling.min_sources")

    _validate_existing_file(errors, "raven.base_rpf_file", config.raven.base_rpf_file)

    _validate_run_name(errors, config.outputs.run_name)

    _validate_parent_directory(errors, "outputs.artifact_root", config.outputs.artifact_root)
    if config.hearing_degradation.output_dir is not None:
        _validate_parent_directory(
            errors,
            "hearing_degradation.output_dir",
            config.hearing_degradation.output_dir,
        )

    if config.hearing_degradation.enabled:
        _validate_existing_file(
            errors,
            "hearing_degradation.hearing_profiles_path",
            config.hearing_degradation.hearing_profiles_path,
        )
        if config.hearing_degradation.hearing_profiles_path.is_file():
            try:
                load_hearing_profile_catalog(config.hearing_degradation.hearing_profiles_path)
            except ValueError as exc:
                errors.extend(
                    f"hearing_degradation.hearing_profiles_path {issue}"
                    for issue in str(exc).splitlines()
                    if issue.strip()
                )

    materials_root = derive_materials_root(config, errors)
    if materials_root is not None:
        for material_id in _configured_material_ids(config):
            _validate_material_directory(errors, materials_root, material_id)

    if errors:
        raise ValueError("\n".join(errors))


def derive_materials_root(config: AppConfig, errors: list[str] | None = None) -> Path | None:
    asset_roots = {
        assets_root
        for path in [
            *[output.ir_catalog_path for output in _defined_outputs(config)],
            *[source_type.audio_dir for source_type in config.source_sampling.source_types],
        ]
        if (assets_root := _find_assets_root(path)) is not None
    }

    if not asset_roots:
        if errors is not None:
            errors.append("No se pudo derivar el directorio compartido assets/ para materiales")
        return None

    if len(asset_roots) > 1:
        if errors is not None:
            errors.append(
                "Se encontraron múltiples directorios assets incompatibles para resolver materiales: "
                + ", ".join(sorted(root.as_posix() for root in asset_roots))
            )
        return None

    return next(iter(asset_roots)) / "materials"


def resolve_material_candidates(materials_root: Path, material_id: str) -> list[Path]:
    material_dir = materials_root / material_id
    return sorted(path.resolve() for path in material_dir.glob("*.mat") if path.is_file())


def resolve_background_audio_candidates(strategy: AudioFolderNoiseStrategyConfig) -> list[Path]:
    return sorted(
        (path.resolve() for path in strategy.audio_dir.glob(strategy.file_pattern) if path.is_file()),
        key=lambda path: path.as_posix().lower(),
    )


def inspect_material_file(material_path: Path) -> list[str]:
    return list(_inspect_material_file_cached(material_path.resolve()))


def load_hearing_profile_catalog(hearing_profiles_path: Path) -> list[HearingProfileDefinition]:
    try:
        raw_catalog = yaml.safe_load(hearing_profiles_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"no se pudo leer el archivo ({exc})") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"no es YAML válido ({exc})") from exc

    if not isinstance(raw_catalog, dict):
        raise ValueError("debe contener un objeto raíz")

    raw_profiles = raw_catalog.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ValueError("profiles debe ser una lista no vacía")

    profiles: list[HearingProfileDefinition] = []
    seen_profile_ids: set[str] = set()
    issues: list[str] = []

    for index, raw_profile in enumerate(raw_profiles, start=1):
        profile_path = f"profiles[{index}]"
        if not isinstance(raw_profile, dict):
            issues.append(f"{profile_path} debe ser un objeto")
            continue

        hearing_profile_id = raw_profile.get("hearing_profile_id")
        if not isinstance(hearing_profile_id, str) or hearing_profile_id.strip() == "":
            issues.append(f"{profile_path}.hearing_profile_id debe ser un string no vacío")
            continue
        if not HEARING_PROFILE_ID_PATTERN.fullmatch(hearing_profile_id):
            issues.append(f"{profile_path}.hearing_profile_id debe ser path-safe")
            continue
        is_duplicate_profile_id = hearing_profile_id in seen_profile_ids
        if is_duplicate_profile_id:
            issues.append(f"{profile_path}.hearing_profile_id está duplicado: {hearing_profile_id}")
        else:
            seen_profile_ids.add(hearing_profile_id)

        try:
            left_loss_db_by_band = _parse_loss_db_by_band(raw_profile, profile_path, "left")
            right_loss_db_by_band = _parse_loss_db_by_band(raw_profile, profile_path, "right")
        except ValueError as exc:
            issues.extend(issue for issue in str(exc).splitlines() if issue.strip())
            continue

        if is_duplicate_profile_id:
            continue

        profiles.append(
            {
                "hearing_profile_id": hearing_profile_id,
                "left_loss_db_by_band": left_loss_db_by_band,
                "right_loss_db_by_band": right_loss_db_by_band,
            }
        )

    if issues:
        raise ValueError("\n".join(issues))

    return profiles


def _validate_receiver_output(errors: list[str], output_name: str, output: ReceiverOutputConfig) -> None:
    if output.num_channels <= 0:
        errors.append(f"receiver_outputs.{output_name}.num_channels debe ser > 0")

    if not output.enabled:
        return

    _validate_existing_directory(errors, f"receiver_outputs.{output_name}.ir_catalog_path", output.ir_catalog_path)
    if output.ir_catalog_path.exists():
        matching_files = sorted(output.ir_catalog_path.glob(output.file_pattern))
        if not matching_files:
            errors.append(
                f"receiver_outputs.{output_name}.ir_catalog_path no contiene archivos para el patrón {output.file_pattern}"
            )

    if not is_safe_output_subdir(output.output_subdir):
        errors.append(
            f"receiver_outputs.{output_name}.output_subdir debe ser un subdirectorio relativo no vacío y sin segmentos inseguros"
        )


def _validate_background_noise(errors: list[str], config: AppConfig) -> None:
    background_noise = config.background_noise
    if not isfinite(background_noise.snr_db):
        errors.append("background_noise.snr_db debe ser finito")

    if not background_noise.enabled:
        return

    if not background_noise.strategies:
        errors.append("background_noise.strategies debe incluir al menos una estrategia cuando enabled=true")

    for index, strategy in enumerate(background_noise.strategies):
        strategy_path = f"background_noise.strategies[{index}]"
        if strategy.type == "colored":
            if not strategy.colors:
                errors.append(f"{strategy_path}.colors debe incluir al menos un color")
            continue

        if strategy.noise_type.strip() == "":
            errors.append(f"{strategy_path}.noise_type no puede estar vacío")
        if strategy.file_pattern.strip() == "":
            errors.append(f"{strategy_path}.file_pattern no puede estar vacío")

        _validate_existing_directory(errors, f"{strategy_path}.audio_dir", strategy.audio_dir)
        if not strategy.audio_dir.exists() or not strategy.audio_dir.is_dir():
            continue

        candidates = resolve_background_audio_candidates(strategy)
        if not candidates:
            errors.append(f"{strategy_path}.audio_dir no contiene archivos para el patrón {strategy.file_pattern}")
            continue

        unsupported = [path for path in candidates if path.suffix.lower() not in SUPPORTED_BACKGROUND_AUDIO_SUFFIXES]
        if unsupported:
            errors.append(
                f"{strategy_path}.audio_dir contiene candidatos no soportados; solo se admite .wav: "
                + ", ".join(path.name for path in unsupported)
            )


def _validate_output_naming(
    errors: list[str],
    config: AppConfig,
    enabled_outputs: dict[str, ReceiverOutputConfig],
) -> None:
    for field_name, pattern in {
        "outputs.naming.wav_pattern": config.outputs.naming.wav_pattern,
        "outputs.naming.metadata_pattern": config.outputs.naming.metadata_pattern,
    }.items():
        unsupported = sorted(find_unsupported_placeholders(pattern))
        if unsupported:
            errors.append(
                f"{field_name} contiene placeholders no soportados: {', '.join(unsupported)}. "
                "Solo se permiten {scene_id} y {output_type}"
            )

    if config.execution.save_render_metadata and len(enabled_outputs) > 1:
        metadata_pattern = config.outputs.naming.metadata_pattern
        if "{output_type}" not in metadata_pattern:
            errors.append(
                "outputs.naming.metadata_pattern debe incluir {output_type} cuando save_render_metadata=true "
                "y hay múltiples receiver_outputs habilitados"
            )


def _validate_clarity_runner(errors: list[str], runner: ClarityRunnerConfig) -> None:
    if runner.entrypoint.strip() == "":
        errors.append("hearing_degradation.runner.entrypoint no puede estar vacío")

    backend_project_path = runner.backend_project_path
    if backend_project_path is not None and backend_project_path.exists() and not backend_project_path.is_dir():
        errors.append(
            f"hearing_degradation.runner.backend_project_path debe ser un directorio: {backend_project_path}"
        )

    if runner.auto_submit and backend_project_path is None:
        errors.append(
            "hearing_degradation.runner.backend_project_path es obligatorio cuando hearing_degradation.runner.auto_submit=true"
        )

    if runner.auto_submit and runner.use_uv and backend_project_path is not None:
        pyproject_path = backend_project_path / "pyproject.toml"
        if backend_project_path.exists() and not pyproject_path.is_file():
            errors.append(
                "hearing_degradation.runner.backend_project_path debe apuntar a un proyecto uv válido con pyproject.toml"
            )


def _validate_source_type(errors: list[str], source_type: SourceTypeConfig) -> None:
    if not (0.0 <= source_type.probability <= 1.0):
        errors.append(f"{source_type.event_type}: probability debe estar entre 0 y 1")

    if source_type.min_count > source_type.max_count:
        errors.append(f"{source_type.event_type}: min_count no puede ser mayor que max_count")

    if source_type.min_count < 0:
        errors.append(f"{source_type.event_type}: min_count debe ser >= 0")

    _validate_existing_directory(errors, f"source_types.{source_type.event_type}.audio_dir", source_type.audio_dir)
    if source_type.audio_dir.exists() and not any(path.is_file() for path in source_type.audio_dir.iterdir()):
        errors.append(f"{source_type.event_type}: audio_dir no contiene archivos de audio")

    if source_type.directivity is not None:
        directivity_field = f"source_types.{source_type.event_type}.directivity"
        _validate_existing_file(errors, directivity_field, source_type.directivity)
        if source_type.directivity.suffix.lower() != ".daff":
            errors.append(f"{directivity_field} debe apuntar a un archivo .daff")

    if source_type.spatial_policy.type == "weighted_targets":
        if not source_type.spatial_policy.targets:
            errors.append(f"{source_type.event_type}: weighted_targets requiere 'targets'")
        else:
            total = sum(source_type.spatial_policy.targets.values())
            if not isclose(total, 1.0, rel_tol=1e-6, abs_tol=1e-6):
                errors.append(f"{source_type.event_type}: targets debe sumar 1.0 y suma {total}")

    radius = source_type.spatial_policy.min_radius_from_receiver_m
    if source_type.spatial_policy.type == "random_valid_away_from_receiver":
        if radius is None or not isfinite(radius) or radius < 0.0:
            errors.append(
                f"{source_type.event_type}: random_valid_away_from_receiver requiere "
                "min_radius_from_receiver_m finito y >= 0"
            )
    elif radius is not None:
        errors.append(
            f"{source_type.event_type}: min_radius_from_receiver_m solo se permite con "
            "random_valid_away_from_receiver"
        )


def _validate_run_name(errors: list[str], run_name: str | None) -> None:
    if run_name is None:
        errors.append("outputs.run_name no puede ser None después de cargar la configuración")
        return

    if run_name.strip() == "":
        errors.append("outputs.run_name no puede estar vacío")
        return

    run_path = PurePath(run_name)
    if run_path.is_absolute() or len(run_path.parts) != 1 or run_path.parts[0] in {".", ".."}:
        errors.append("outputs.run_name debe ser un nombre simple de run sin separadores inseguros")


def _configured_material_ids(config: AppConfig) -> list[str]:
    return sorted(
        {
            *config.room_sampling.materials.walls,
            *config.room_sampling.materials.floor,
            *config.room_sampling.materials.ceiling,
        }
    )


def _validate_material_directory(errors: list[str], materials_root: Path, material_id: str) -> None:
    material_dir = materials_root / material_id
    if not material_dir.exists():
        errors.append(f"room_sampling.materials.{material_id} no existe en assets/materials: {material_dir}")
        return

    if not material_dir.is_dir():
        errors.append(f"room_sampling.materials.{material_id} debe ser un directorio: {material_dir}")
        return

    candidates = resolve_material_candidates(materials_root, material_id)
    if not candidates:
        errors.append(f"room_sampling.materials.{material_id} no contiene archivos .mat: {material_dir}")
        return

    invalid_candidates: list[str] = []
    for candidate in candidates:
        candidate_errors = inspect_material_file(candidate)
        if not candidate_errors:
            return
        invalid_candidates.append(f"{candidate}: {'; '.join(candidate_errors)}")

    errors.append(
        f"room_sampling.materials.{material_id} no tiene materiales válidos en {material_dir}. "
        f"Candidatos inválidos: {' | '.join(invalid_candidates)}"
    )


@lru_cache(maxsize=None)
def _inspect_material_file_cached(material_path: Path) -> tuple[str, ...]:
    try:
        content = material_path.read_text(encoding="utf-8")
    except OSError as exc:
        return (f"no se pudo leer el archivo ({exc})",)

    errors: list[str] = []
    for field_name in ("absorp", "scatter"):
        values_raw = _extract_material_field(content, field_name)
        if values_raw is None:
            errors.append(f"falta {field_name}")
            continue

        try:
            values = _parse_material_values(values_raw)
        except ValueError as exc:
            errors.append(f"{field_name}: {exc}")
            continue

        if len(values) != 31:
            errors.append(f"{field_name}: debe tener 31 valores y tiene {len(values)}")
            continue

        invalid_indexes = [
            index
            for index, value in enumerate(values, start=1)
            if not isfinite(value) or value < 0.0 or value > 1.0
        ]
        if invalid_indexes:
            formatted_indexes = ", ".join(str(index) for index in invalid_indexes)
            errors.append(f"{field_name}: valores fuera de rango [0,1] o no finitos en posiciones {formatted_indexes}")

    return tuple(errors)


def _extract_material_field(content: str, field_name: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(field_name)}\s*=\s*(.+?)\s*$", content, flags=re.MULTILINE)
    if match is None:
        return None
    return match.group(1).strip()


def _parse_material_values(raw_values: str) -> list[float]:
    parts = [part.strip() for part in raw_values.split(",")]
    if any(part == "" for part in parts):
        raise ValueError("contiene valores vacíos")

    try:
        return [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError("contiene valores no numéricos") from exc


def _find_assets_root(path: Path) -> Path | None:
    resolved_path = path.resolve()
    candidates = [resolved_path, *resolved_path.parents]
    for candidate in candidates:
        if candidate.name == "assets":
            return candidate
    return None


def _parse_loss_db_by_band(raw_profile: dict[str, Any], profile_path: str, ear_name: str) -> dict[str, float]:
    ears = raw_profile.get("ears")
    if not isinstance(ears, dict):
        raise ValueError(f"{profile_path}.ears debe ser un objeto")

    ear = ears.get(ear_name)
    if not isinstance(ear, dict):
        raise ValueError(f"{profile_path}.ears.{ear_name} debe ser un objeto")

    loss_db_by_band = ear.get("loss_db_by_band")
    if not isinstance(loss_db_by_band, dict) or not loss_db_by_band:
        raise ValueError(f"{profile_path}.ears.{ear_name}.loss_db_by_band debe ser un objeto no vacío")

    normalized_loss_db_by_band: dict[str, float] = {}
    issues: list[str] = []
    for band_name, loss_db in loss_db_by_band.items():
        normalized_band_name = str(band_name).strip() if isinstance(band_name, str | int | float) else ""
        if normalized_band_name == "":
            issues.append(f"{profile_path}.ears.{ear_name}.loss_db_by_band contiene una banda inválida")
            continue
        if not isinstance(loss_db, int | float) or not isfinite(float(loss_db)):
            issues.append(
                f"{profile_path}.ears.{ear_name}.loss_db_by_band.{normalized_band_name} debe ser numérico y finito"
            )
            continue
        normalized_loss_db_by_band[normalized_band_name] = float(loss_db)

    if issues:
        raise ValueError("\n".join(issues))

    return normalized_loss_db_by_band


def _defined_outputs(config: AppConfig) -> list[ReceiverOutputConfig]:
    return [
        output
        for output in [
            config.receiver_outputs.binaural_hrtf,
            config.receiver_outputs.bte_rear_hartf,
            config.receiver_outputs.bte_front_hartf,
        ]
        if output is not None
    ]


def _validate_range(errors: list[str], field_name: str, min_value: float, max_value: float) -> None:
    if min_value > max_value:
        errors.append(f"{field_name}.min no puede ser mayor que .max")


def _validate_existing_file(errors: list[str], field_name: str, path: Path) -> None:
    if not path.exists():
        errors.append(f"{field_name} no existe: {path}")
    elif not path.is_file():
        errors.append(f"{field_name} debe ser un archivo: {path}")


def _validate_existing_directory(errors: list[str], field_name: str, path: Path) -> None:
    if not path.exists():
        errors.append(f"{field_name} no existe: {path}")
    elif not path.is_dir():
        errors.append(f"{field_name} debe ser un directorio: {path}")


def _validate_parent_directory(errors: list[str], field_name: str, path: Path) -> None:
    current = path.parent
    while not current.exists() and current != current.parent:
        current = current.parent

    if not current.exists():
        errors.append(f"No existe ningún ancestro válido para {field_name}: {path}")
    elif not current.is_dir():
        errors.append(f"El ancestro existente de {field_name} no es un directorio: {current}")
