from pathlib import Path

import yaml

from .models import AppConfig


LEGACY_OUTPUT_ROOT_KEYS = {
    "scene_manifest_dir",
    "rendered_wav_dir",
    "render_metadata_dir",
    "logs_dir",
}


def load_config(config_path: str | Path) -> AppConfig:
    config_path = Path(config_path).resolve()

    with config_path.open("r", encoding="utf-8") as file:
        raw_config = yaml.safe_load(file)

    _reject_legacy_output_roots(raw_config)
    _normalize_legacy_config(raw_config)

    config = AppConfig.model_validate(raw_config)
    return resolve_relative_paths(config, config_path.parent)


def resolve_relative_paths(config: AppConfig, base_dir: Path) -> AppConfig:
    config.raven.base_rpf_file = _resolve(config.raven.base_rpf_file, base_dir)

    for output in _receiver_outputs(config):
        output.ir_catalog_path = _resolve(output.ir_catalog_path, base_dir)

    for source_type in config.source_sampling.source_types:
        source_type.audio_dir = _resolve(source_type.audio_dir, base_dir)
        if source_type.directivity is not None:
            source_type.directivity = _resolve(source_type.directivity, base_dir)

    for strategy in config.background_noise.strategies:
        if strategy.type == "audio_folder":
            strategy.audio_dir = _resolve(strategy.audio_dir, base_dir)

    config.outputs.artifact_root = _resolve(config.outputs.artifact_root, base_dir)
    if config.outputs.run_name is None:
        config.outputs.run_name = config.experiment.experiment_id

    config.hearing_degradation.hearing_profiles_path = _resolve(
        config.hearing_degradation.hearing_profiles_path,
        base_dir,
    )
    hearing_output_dir = config.hearing_degradation.output_dir
    if hearing_output_dir is None:
        config.hearing_degradation.output_dir = (
            config.outputs.artifact_root
            / config.outputs.run_name
            / "output_audio"
            / "degraded"
        ).resolve()
    else:
        config.hearing_degradation.output_dir = _resolve(hearing_output_dir, base_dir)

    backend_project_path = config.hearing_degradation.runner.backend_project_path
    if backend_project_path is not None:
        config.hearing_degradation.runner.backend_project_path = _resolve(
            backend_project_path,
            base_dir,
        )

    return config


def _receiver_outputs(config: AppConfig):
    return [
        output
        for output in [
            config.receiver_outputs.binaural_hrtf,
            config.receiver_outputs.bte_rear_hartf,
            config.receiver_outputs.bte_front_hartf,
        ]
        if output is not None
    ]


def _resolve(path: Path, base_dir: Path) -> Path:
    return path if path.is_absolute() else (base_dir / path).resolve()


def _reject_legacy_output_roots(raw_config: object) -> None:
    if not isinstance(raw_config, dict):
        return

    outputs = raw_config.get("outputs")
    if not isinstance(outputs, dict):
        return

    legacy_keys = sorted(key for key in LEGACY_OUTPUT_ROOT_KEYS if key in outputs)
    if legacy_keys:
        raise ValueError(
            "outputs usa claves legacy no soportadas: "
            + ", ".join(legacy_keys)
            + ". Usa outputs.artifact_root y outputs.run_name."
        )


def _normalize_legacy_config(raw_config: object) -> None:
    if not isinstance(raw_config, dict):
        return

    room_sampling = raw_config.get("room_sampling")
    if isinstance(room_sampling, dict):
        if "max_rt30_s" in room_sampling:
            raise ValueError(
                "room_sampling.max_rt30_s ya no es válido; configure room_sampling.reverberation"
            )
        _normalize_legacy_room_sampling(room_sampling)

    scene_validation = raw_config.get("scene_validation")
    if isinstance(scene_validation, dict):
        _normalize_legacy_retry_budget(scene_validation)


def _normalize_legacy_room_sampling(room_sampling: dict) -> None:
    has_dimensions = "dimensions_m" in room_sampling
    has_geometry = "geometry" in room_sampling
    has_semantic_surfaces = "semantic_surfaces" in room_sampling

    if has_dimensions and has_geometry:
        raise ValueError("room_sampling no puede mezclar dimensions_m legacy con geometry")

    if not has_dimensions:
        if has_semantic_surfaces:
            raise ValueError("room_sampling.semantic_surfaces solo se admite junto a dimensions_m legacy")
        return

    dimensions = room_sampling.pop("dimensions_m")
    if not isinstance(dimensions, dict):
        raise ValueError("room_sampling.dimensions_m legacy debe ser un objeto")

    semantic_surfaces = room_sampling.pop("semantic_surfaces", None)
    if semantic_surfaces is not None:
        if not isinstance(semantic_surfaces, dict):
            raise ValueError("room_sampling.semantic_surfaces legacy debe ser un objeto")

    missing = [name for name in ("length", "width", "height") if name not in dimensions]
    if missing:
        raise ValueError("room_sampling.dimensions_m legacy incompleto: " + ", ".join(missing))

    room_sampling["geometry"] = {
        "height_m": dimensions["height"],
        "shape_mix": [
            {
                "type": "shoebox",
                "probability": 1.0,
                "length_m": dimensions["length"],
                "width_m": dimensions["width"],
            }
        ],
    }


def _normalize_legacy_retry_budget(scene_validation: dict) -> None:
    legacy_key = "max_sampling_attempts_per_scene"
    if legacy_key not in scene_validation:
        return
    if "max_scene_attempts" in scene_validation or "max_receiver_attempts" in scene_validation:
        raise ValueError(
            "scene_validation no puede mezclar max_sampling_attempts_per_scene legacy con los presupuestos nuevos"
        )
    attempts = scene_validation.pop(legacy_key)
    scene_validation["max_scene_attempts"] = attempts
    scene_validation["max_receiver_attempts"] = attempts
