"""Load and validate a runtime-manifest snapshot into a render job.

Everything rejected here is deterministic input failure (``invalid_input``):
the worker publishes a failure report and the attempt is not retried.
No Pyroomacoustics import: validation runs before any simulation.
"""

from dataclasses import dataclass
import json
import math
from pathlib import Path

import soundfile

from acoustic_orchestrator.backends.pyroomacoustics.errors import InvalidRenderInput
from acoustic_orchestrator.backends.pyroomacoustics.frames import public_to_backend
from acoustic_orchestrator.backends.pyroomacoustics.materials import convert_surface_materials
from acoustic_orchestrator.backends.pyroomacoustics.room_geometry import build_room_description, contains_backend_point
from acoustic_orchestrator.backends.pyroomacoustics.synthesis import check_noise_layers
from acoustic_orchestrator.pipeline.render_backends import get_render_backend
from acoustic_orchestrator.pipeline.render_validation import effective_seed

BACKEND_NAME = "pyroomacoustics"


@dataclass
class SourceInput:
    source: dict  # the manifest entry, unchanged
    position_backend_m: tuple[float, float, float]


@dataclass
class RenderJob:
    manifest: dict
    fs: int
    room_description: dict
    materials: dict[str, dict]
    parameters: dict
    microphones: list[dict]  # descriptor entries in microphone_index order
    microphones_backend_m: list[tuple[float, float, float]]
    sources: list[SourceInput]
    seed: int
    trim_reverb_tail: bool
    target_duration_s: float | None

    @property
    def num_channels(self) -> int:
        return len(self.microphones)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _vector3(value: object, label: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3 or not all(_finite(item) for item in value):
        raise InvalidRenderInput(f"{label} must be three finite numbers")
    return float(value[0]), float(value[1]), float(value[2])


def _reject_constant(value: str) -> None:
    raise InvalidRenderInput(f"non-finite JSON value: {value}")


def parse_snapshot(data: bytes) -> dict:
    def unique(pairs: list) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise InvalidRenderInput(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    try:
        manifest = json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=_reject_constant)
    except (UnicodeError, ValueError) as exc:
        raise InvalidRenderInput(f"snapshot is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise InvalidRenderInput("snapshot must be a JSON object")
    return manifest


def _parameters(render: dict) -> dict:
    parameters = render.get("backend_parameters")
    if not isinstance(parameters, dict):
        raise InvalidRenderInput("render.backend_parameters is missing")
    for name in ("max_order", "max_order_non_shoebox"):
        if type(parameters.get(name)) is not int or parameters[name] < 0:
            raise InvalidRenderInput(f"render.backend_parameters.{name} must be an integer >= 0")
    if type(parameters.get("air_absorption")) is not bool:
        raise InvalidRenderInput("render.backend_parameters.air_absorption must be a boolean")
    temperature, humidity = parameters.get("temperature_c"), parameters.get("humidity_pct")
    if (temperature is None) != (humidity is None):
        raise InvalidRenderInput("render.backend_parameters temperature_c and humidity_pct must be given together")
    if temperature is not None and (not _finite(temperature) or not _finite(humidity) or not 0 <= humidity <= 100):
        raise InvalidRenderInput("render.backend_parameters temperature_c/humidity_pct invalid")
    return parameters


def _microphones(receiver: dict) -> list[dict]:
    array = receiver.get("circular_array")
    if not isinstance(array, dict):
        raise InvalidRenderInput("receiver.circular_array descriptor is missing")
    microphones = array.get("microphones")
    count = array.get("num_microphones")
    if type(count) is not int or count < 2 or not isinstance(microphones, list) or len(microphones) != count:
        raise InvalidRenderInput("receiver.circular_array.microphones must list num_microphones (>= 2) entries")
    ordered = sorted(microphones, key=lambda item: item.get("microphone_index") if isinstance(item, dict) else -1)
    for expected, item in enumerate(ordered):
        if (not isinstance(item, dict) or type(item.get("microphone_index")) is not int
                or item["microphone_index"] != expected or item.get("channel_index") != expected + 1):
            raise InvalidRenderInput("receiver.circular_array microphone_index/channel_index are not 0..N-1 / 1..N")
        _vector3(item.get("position_m"), f"receiver.circular_array.microphones[{expected}].position_m")
    if receiver.get("hrtfs"):
        raise InvalidRenderInput("array variants must not carry receiver IRs")
    return ordered


def load_render_job(manifest: dict) -> RenderJob:
    render = manifest.get("render")
    receiver = manifest.get("receiver")
    if not isinstance(render, dict) or not isinstance(receiver, dict):
        raise InvalidRenderInput("snapshot lacks render/receiver objects")
    if render.get("backend") != BACKEND_NAME:
        raise InvalidRenderInput(f"render.backend must be {BACKEND_NAME!r}, got {render.get('backend')!r}")
    if manifest.get("scene_type") != "static":
        raise InvalidRenderInput("only static scenes are supported")
    if render.get("active_output_type") != "circular_array":
        raise InvalidRenderInput(f"unsupported output type {render.get('active_output_type')!r}; only circular_array")
    backend = get_render_backend(BACKEND_NAME)
    fs = render.get("sample_rate_hz")
    if type(fs) is not int or fs not in backend.supported_sample_rates:
        raise InvalidRenderInput(f"render.sample_rate_hz {fs!r} not supported by {BACKEND_NAME}")
    for field in ("output_wav_path", "output_metadata_path"):
        if not isinstance(render.get(field), str) or not Path(render[field]).is_absolute():
            raise InvalidRenderInput(f"render.{field} must be an absolute path")
    seed, _ = effective_seed(manifest)
    if type(seed) is not int:
        raise InvalidRenderInput("render.seed must be an integer for reproducible rendering")
    target = render.get("target_duration_s")
    if target in ([], None):
        target = None
    elif not _finite(target) or target <= 0:
        raise InvalidRenderInput("render.target_duration_s must be finite and positive")
    trim = render.get("trim_reverb_tail", False)
    trim = False if trim in ([], None) else trim
    if type(trim) is not bool:
        raise InvalidRenderInput("render.trim_reverb_tail must be a boolean")

    parameters = _parameters(render)
    microphones = _microphones(receiver)
    room = manifest.get("room")
    description = build_room_description(room if isinstance(room, dict) else {})
    surfaces = [*description["wall_surface_ids"], description["floor_surface_id"], description["ceiling_surface_id"]]
    materials = convert_surface_materials(room, surfaces)
    translation = description["translation_m"]
    microphones_backend = []
    for item in microphones:
        position = public_to_backend(item["position_m"], translation)
        if not contains_backend_point(description, position):
            raise InvalidRenderInput(f"microphone {item['microphone_index']} is outside the room")
        microphones_backend.append(position)

    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise InvalidRenderInput("snapshot has no sources")
    identifiers = [source.get("source_id") if isinstance(source, dict) else None for source in sources]
    if any(not isinstance(item, str) or not item for item in identifiers) or len(set(identifiers)) != len(identifiers):
        raise InvalidRenderInput("source_id values must be unique non-empty strings")
    inputs = []
    for source in sources:
        label = f"sources[{source['source_id']}]"
        if source.get("directivity_path"):
            raise InvalidRenderInput(f"{label}.directivity_path is not supported by {BACKEND_NAME}")
        position = public_to_backend(_vector3(source.get("position_m"), f"{label}.position_m"), translation)
        if not contains_backend_point(description, position):
            raise InvalidRenderInput(f"{label} is outside the room")
        for field in ("gain_db", "start_time_s"):
            if source.get(field) not in (None, []) and not _finite(source[field]):
                raise InvalidRenderInput(f"{label}.{field} must be finite")
        orientation = source.get("orientation_deg")
        if not isinstance(orientation, dict) or not all(_finite(orientation.get(k, 0)) for k in ("yaw", "pitch")):
            raise InvalidRenderInput(f"{label}.orientation_deg invalid")
        audio_path = source.get("audio_path")
        if not isinstance(audio_path, str) or not audio_path:
            raise InvalidRenderInput(f"{label}.audio_path missing")
        try:
            info = soundfile.info(audio_path)
        except (RuntimeError, OSError) as exc:
            raise InvalidRenderInput(f"{label}.audio_path unreadable: {exc}") from exc
        if info.frames <= 0:
            raise InvalidRenderInput(f"{label}.audio_path is empty")
        inputs.append(SourceInput(source, position))

    noise = manifest.get("background_noise") or {}
    if not isinstance(noise, dict):
        raise InvalidRenderInput("background_noise must be an object")
    if noise.get("enabled"):
        layers = noise.get("layers")
        if not isinstance(layers, list) or not layers:
            raise InvalidRenderInput("background_noise enabled without layers")
        check_noise_layers(layers, len(microphones))

    return RenderJob(manifest=manifest, fs=fs, room_description=description, materials=materials,
                     parameters=parameters, microphones=microphones, microphones_backend_m=microphones_backend,
                     sources=inputs, seed=seed, trim_reverb_tail=trim, target_duration_s=target)
