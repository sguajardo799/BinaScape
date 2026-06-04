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
            / "outputs"
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
