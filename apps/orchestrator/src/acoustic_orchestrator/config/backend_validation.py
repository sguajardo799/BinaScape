"""Backend-specific configuration rules, applied by ``validate_config``.

Unsupported capabilities are rejected explicitly; nothing is replaced by an
omnidirectional or otherwise simplified model. This module sits outside the
static generator contract on purpose, so backend rules can evolve without
changing plan identity.
"""

import importlib.metadata

from acoustic_orchestrator.config.models import AppConfig
from acoustic_orchestrator.pipeline.render_backends import RenderBackend, get_render_backend, version_in_range

RAVEN_ONLY_RENDER_FIELDS = (
    "generate_rir", "generate_brir", "simulation_type_rt", "simulation_type_is", "num_particles", "is_order_ps",
)
_HEAD_RELATED_OUTPUTS = ("binaural_hrtf", "bte_rear_hartf", "bte_front_hartf")


def check_backend_dependency(backend: RenderBackend) -> str | None:
    """Return an error when the backend package is missing or out of range.

    Reads installed distribution metadata only; never imports the package.
    """
    if backend.package is None:
        return None
    requirement = backend.package_requirement
    try:
        version = importlib.metadata.version(backend.package)
    except importlib.metadata.PackageNotFoundError:
        return (f"render.backend={backend.name} requiere la dependencia {requirement} "
                f"(extra opcional 'pyroomacoustics' del orchestrator); no está instalada")
    assert backend.package_min_version is not None and backend.package_max_version is not None
    if not version_in_range(version, backend.package_min_version, backend.package_max_version):
        return (f"render.backend={backend.name} requiere la dependencia {requirement}; "
                f"versión instalada no soportada: {version}")
    return None


def validate_backend_config(errors: list[str], config: AppConfig) -> None:
    """Append one error per violated backend rule, each naming its field."""
    name = config.render.backend
    backend = get_render_backend(name)
    if name == "raven":
        if config.raven is None:
            errors.append("raven es obligatorio cuando render.backend=raven")
        if config.pyroomacoustics is not None:
            errors.append("pyroomacoustics no aplica al backend seleccionado render.backend=raven")
        return

    if config.pyroomacoustics is None:
        errors.append(f"pyroomacoustics es obligatorio cuando render.backend={name} "
                      "(requiere pyroomacoustics.max_order y pyroomacoustics.air_absorption)")
    if config.raven is not None:
        errors.append(f"raven no aplica al backend seleccionado render.backend={name}")
    for field_name in RAVEN_ONLY_RENDER_FIELDS:
        if field_name in config.render.model_fields_set:
            errors.append(f"render.{field_name} es un campo exclusivo de RAVEN y no aplica a render.backend={name}")
    for output_name in _HEAD_RELATED_OUTPUTS:
        output = getattr(config.receiver_outputs, output_name)
        if output is not None and output.enabled:
            errors.append(f"receiver_outputs.{output_name} no está soportado por render.backend={name}; "
                          f"salidas soportadas: {', '.join(sorted(backend.receiver_output_types))}")
    for index, source_type in enumerate(config.source_sampling.source_types):
        if source_type.directivity is not None:
            errors.append(
                f"source_sampling.source_types[{index}].directivity ({source_type.event_type}) "
                f"no está soportado por render.backend={name}; no se sustituye por una fuente omnidireccional"
            )
    if config.hearing_degradation.enabled:
        errors.append(f"hearing_degradation.enabled=true no está soportado por render.backend={name}")
    if config.experiment.scene_type == "dynamic":
        errors.append(f"experiment.scene_type=dynamic no está soportado por render.backend={name}")
    if config.render.sample_rate_hz not in backend.supported_sample_rates:
        rates = ", ".join(str(rate) for rate in sorted(backend.supported_sample_rates))
        errors.append(f"render.sample_rate_hz={config.render.sample_rate_hz} no está soportado por "
                      f"render.backend={name}; frecuencias soportadas: {rates}")
    dependency_error = check_backend_dependency(backend)
    if dependency_error is not None:
        errors.append(dependency_error)
