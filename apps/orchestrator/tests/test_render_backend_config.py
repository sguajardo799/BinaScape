"""Backend table, selector, backend-specific config rules and plan identity."""

import importlib.metadata
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from acoustic_orchestrator.config import backend_validation
from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.models import PyroomacousticsConfig
from acoustic_orchestrator.config.validator import validate_config
from acoustic_orchestrator.pipeline.render_backends import (
    RENDER_BACKENDS,
    get_render_backend,
    manifest_render_backend,
    version_in_range,
)
from acoustic_orchestrator.pipeline.render_validation import RAVEN_OCTAVE_FREQUENCIES_HZ
from acoustic_orchestrator.pipeline.static_plan import normalized_static_config
from raven_golden import EXAMPLES, load_golden, normalized_example_config
from test_validator import _write_config as _write_raven_config


@pytest.fixture
def pra_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the supported package is installed (metadata only)."""
    monkeypatch.setattr(backend_validation.importlib.metadata, "version",
                        lambda name: "0.10.1" if name == "pyroomacoustics" else importlib.metadata.version(name))


def write_pyroomacoustics_config(tmp_path: Path, **changes) -> Path:
    """A valid array-only Pyroomacoustics configuration built from the RAVEN fixture."""
    path = _write_raven_config(tmp_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.pop("raven")
    raw["render"] = {"backend": "pyroomacoustics", "sample_rate_hz": 44100}
    raw["pyroomacoustics"] = {"max_order": 3, "air_absorption": False}
    raw["receiver_outputs"] = {"circular_array": {
        "enabled": True, "num_microphones": 3, "radius_m": 0.05, "output_subdir": "circular_array", "required": True}}
    raw["hearing_degradation"]["enabled"] = False
    raw["hearing_degradation"]["input_targets"] = []
    for dotted, value in changes.items():
        *parents, leaf = dotted.split(".")
        node = raw
        for key in parents:
            node = node.setdefault(key, {})
        if value is _DELETE:
            node.pop(leaf, None)
        else:
            node[leaf] = value
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


_DELETE = object()


def _errors(path: Path) -> str:
    with pytest.raises(ValueError) as excinfo:
        validate_config(load_config(path))
    return str(excinfo.value)


# --- 2.1 backend table -------------------------------------------------------

def test_raven_entry_reproduces_existing_literals() -> None:
    raven = get_render_backend(None)
    assert raven is RENDER_BACKENDS["raven"] is get_render_backend("raven")
    assert raven.supported_sample_rates == {44100}
    assert (raven.t30_method, raven.t30_estimated_from) == ("rpf.getT30", "room")
    assert raven.summary_render_backend is None
    assert raven.package is None
    assert raven.receiver_output_types == {"binaural_hrtf", "bte_rear_hartf", "bte_front_hartf", "circular_array"}


def test_pyroomacoustics_entry() -> None:
    backend = get_render_backend("pyroomacoustics")
    assert backend.supported_sample_rates == {44100}
    assert (backend.t30_method, backend.t30_estimated_from) == ("schroeder_backward_integration_v1", "rir")
    assert backend.summary_render_backend == "Pyroomacoustics"
    assert backend.receiver_output_types == {"circular_array"}
    assert backend.package_requirement == "pyroomacoustics>=0.10.1,<0.11"
    assert len(RAVEN_OCTAVE_FREQUENCIES_HZ) == 10


def test_backend_table_never_imports_pyroomacoustics() -> None:
    source = Path(backend_validation.__file__).parents[1] / "pipeline" / "render_backends.py"
    assert "import pyroomacoustics" not in source.read_text(encoding="utf-8")
    assert "import pyroomacoustics" not in Path(backend_validation.__file__).read_text(encoding="utf-8")


def test_manifest_backend_defaults_to_raven_and_rejects_unknown() -> None:
    assert manifest_render_backend({"render": {}}).name == "raven"
    assert manifest_render_backend({}).name == "raven"
    assert manifest_render_backend({"render": {"backend": "pyroomacoustics"}}).name == "pyroomacoustics"
    with pytest.raises(ValueError, match="odeon"):
        manifest_render_backend({"render": {"backend": "odeon"}})


@pytest.mark.parametrize("version, ok", [
    ("0.10.1", True), ("0.10.9", True), ("0.10.10", True), ("0.10.0", False), ("0.9.0", False),
    ("0.11", False), ("0.11.0", False), ("1.0", False), ("0.10.1rc1", False), ("garbage", False),
])
def test_version_range(version: str, ok: bool) -> None:
    assert version_in_range(version, "0.10.1", "0.11") is ok


# --- 2.2 models -------------------------------------------------------------

def test_pyroomacoustics_model_valid_and_default_non_shoebox_order() -> None:
    config = PyroomacousticsConfig.model_validate({"max_order": 4, "air_absorption": True})
    assert config.max_order_non_shoebox == 4
    explicit = PyroomacousticsConfig.model_validate(
        {"max_order": 4, "max_order_non_shoebox": 2, "air_absorption": False,
         "temperature_c": 20.0, "humidity_pct": 50.0})
    assert (explicit.max_order_non_shoebox, explicit.temperature_c, explicit.humidity_pct) == (2, 20.0, 50.0)


@pytest.mark.parametrize("payload, field", [
    ({"air_absorption": True}, "max_order"),
    ({"max_order": -1, "air_absorption": True}, "max_order"),
    ({"max_order": 2.0, "air_absorption": True}, "max_order"),
    ({"max_order": True, "air_absorption": True}, "max_order"),
    ({"max_order": 2, "max_order_non_shoebox": -1, "air_absorption": True}, "max_order_non_shoebox"),
    ({"max_order": 2}, "air_absorption"),
    ({"max_order": 2, "air_absorption": True, "humidity_pct": 101.0, "temperature_c": 20.0}, "humidity_pct"),
    ({"max_order": 2, "air_absorption": True, "humidity_pct": -1.0, "temperature_c": 20.0}, "humidity_pct"),
    ({"max_order": 2, "air_absorption": True, "temperature_c": float("nan"), "humidity_pct": 10.0}, "temperature_c"),
])
def test_pyroomacoustics_model_rejections(payload: dict, field: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        PyroomacousticsConfig.model_validate(payload)
    assert field in str(excinfo.value)


@pytest.mark.parametrize("payload", [
    {"max_order": 2, "air_absorption": True, "temperature_c": 20.0},
    {"max_order": 2, "air_absorption": True, "humidity_pct": 40.0},
])
def test_partial_atmosphere_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError, match="temperature_c y humidity_pct"):
        PyroomacousticsConfig.model_validate(payload)


# --- render-backend-selection scenarios (2.3) --------------------------------

def test_existing_configuration_defaults_to_raven(tmp_path: Path) -> None:
    config = load_config(_write_raven_config(tmp_path))
    validate_config(config)
    assert config.render.backend == "raven"
    assert "backend" not in config.render.model_fields_set


def test_explicit_pyroomacoustics_selection_validates(tmp_path: Path, pra_installed: None) -> None:
    config = load_config(write_pyroomacoustics_config(tmp_path))
    validate_config(config)
    assert config.render.backend == "pyroomacoustics" and config.raven is None


def test_unknown_backend_names_field_and_values(tmp_path: Path) -> None:
    path = write_pyroomacoustics_config(tmp_path, **{"render.backend": "odeon"})
    with pytest.raises(ValidationError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "render.backend" in message and "'raven'" in message and "'pyroomacoustics'" in message


def test_linux_configuration_without_raven_does_not_check_rpf(tmp_path: Path, pra_installed: None) -> None:
    path = write_pyroomacoustics_config(tmp_path)
    (tmp_path / "base_room.rpf").unlink()
    validate_config(load_config(path))


def test_conflicting_raven_block_rejected(tmp_path: Path, pra_installed: None) -> None:
    path = write_pyroomacoustics_config(tmp_path, **{"raven.base_rpf_file": (tmp_path / "base_room.rpf").as_posix()})
    assert "raven no aplica al backend seleccionado render.backend=pyroomacoustics" in _errors(path)


def test_pyroomacoustics_block_rejected_under_raven(tmp_path: Path) -> None:
    raw = yaml.safe_load(_write_raven_config(tmp_path).read_text(encoding="utf-8"))
    raw["pyroomacoustics"] = {"max_order": 3, "air_absorption": False}
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert "pyroomacoustics no aplica al backend seleccionado render.backend=raven" in _errors(path)


def test_raven_block_required_under_raven(tmp_path: Path) -> None:
    raw = yaml.safe_load(_write_raven_config(tmp_path).read_text(encoding="utf-8"))
    raw.pop("raven")
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert "raven es obligatorio cuando render.backend=raven" in _errors(path)


@pytest.mark.parametrize("field, value", [
    ("generate_rir", False), ("generate_brir", True), ("simulation_type_rt", True),
    ("simulation_type_is", True), ("num_particles", 60000), ("is_order_ps", 2),
])
def test_raven_tuning_fields_rejected_when_explicit(tmp_path: Path, pra_installed: None, field: str, value) -> None:
    path = write_pyroomacoustics_config(tmp_path, **{f"render.{field}": value})
    assert f"render.{field} es un campo exclusivo de RAVEN" in _errors(path)


@pytest.mark.parametrize("value", [_DELETE, -1, "two", 1.5])
def test_missing_or_invalid_ism_order(tmp_path: Path, pra_installed: None, value) -> None:
    path = write_pyroomacoustics_config(tmp_path, **{"pyroomacoustics.max_order": value})
    with pytest.raises(ValidationError) as excinfo:
        load_config(path)
    assert "pyroomacoustics.max_order" in str(excinfo.value)


def test_missing_pyroomacoustics_block(tmp_path: Path, pra_installed: None) -> None:
    path = write_pyroomacoustics_config(tmp_path, pyroomacoustics=_DELETE)
    assert "pyroomacoustics es obligatorio cuando render.backend=pyroomacoustics" in _errors(path)


def test_partial_atmospheric_conditions_in_config(tmp_path: Path, pra_installed: None) -> None:
    path = write_pyroomacoustics_config(tmp_path, **{"pyroomacoustics.temperature_c": 21.0})
    with pytest.raises(ValidationError, match="temperature_c y humidity_pct"):
        load_config(path)


@pytest.mark.parametrize("output_name", ["binaural_hrtf", "bte_rear_hartf", "bte_front_hartf"])
def test_head_related_outputs_rejected(tmp_path: Path, pra_installed: None, output_name: str) -> None:
    (tmp_path / "assets" / "hrtf").mkdir(parents=True, exist_ok=True)
    path = write_pyroomacoustics_config(tmp_path, **{f"receiver_outputs.{output_name}": {
        "enabled": True, "ir_catalog_path": (tmp_path / "assets" / "hrtf").as_posix(), "file_pattern": "*.daff",
        "output_subdir": output_name, "num_channels": 2}})
    assert f"receiver_outputs.{output_name} no está soportado por render.backend=pyroomacoustics" in _errors(path)


def test_directional_source_rejected(tmp_path: Path, pra_installed: None) -> None:
    path = write_pyroomacoustics_config(tmp_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["source_sampling"]["source_types"][0]["directivity"] = (tmp_path / "assets" / "directivity" / "speech.daff").as_posix()
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    message = _errors(path)
    assert "source_sampling.source_types[0].directivity (speech)" in message
    assert "omnidireccional" in message


def test_hearing_degradation_rejected(tmp_path: Path, pra_installed: None) -> None:
    path = write_pyroomacoustics_config(tmp_path, **{"hearing_degradation.enabled": True})
    assert "hearing_degradation.enabled=true no está soportado por render.backend=pyroomacoustics" in _errors(path)


def test_dynamic_scene_rejected(tmp_path: Path, pra_installed: None) -> None:
    path = write_pyroomacoustics_config(tmp_path, **{"experiment.scene_type": "dynamic"})
    assert "experiment.scene_type=dynamic no está soportado por render.backend=pyroomacoustics" in _errors(path)


def test_unsupported_sample_rate_rejected(tmp_path: Path, pra_installed: None) -> None:
    path = write_pyroomacoustics_config(tmp_path, **{"render.sample_rate_hz": 48000})
    message = _errors(path)
    assert "render.sample_rate_hz=48000 no está soportado por render.backend=pyroomacoustics" in message
    assert "frecuencias soportadas: 44100" in message


def test_raven_sample_rate_behaviour_unchanged(tmp_path: Path) -> None:
    raw = yaml.safe_load(_write_raven_config(tmp_path).read_text(encoding="utf-8"))
    raw["render"]["sample_rate_hz"] = 48000
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    validate_config(load_config(path))  # Config-time RAVEN behaviour is unchanged (MATLAB rejects later).


# --- 2.4 dependency check ----------------------------------------------------

def test_dependency_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(backend_validation.importlib.metadata, "version", missing)
    message = _errors(write_pyroomacoustics_config(tmp_path))
    assert "pyroomacoustics>=0.10.1,<0.11" in message and "no está instalada" in message


def test_dependency_too_old(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(backend_validation.importlib.metadata, "version", lambda name: "0.9.0")
    message = _errors(write_pyroomacoustics_config(tmp_path))
    assert "pyroomacoustics>=0.10.1,<0.11" in message and "0.9.0" in message


def test_dependency_supported(pra_installed: None) -> None:
    assert backend_validation.check_backend_dependency(get_render_backend("pyroomacoustics")) is None
    assert backend_validation.check_backend_dependency(get_render_backend("raven")) is None


def test_raven_validation_never_queries_pyroomacoustics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def forbidden(name: str) -> str:
        raise AssertionError(f"unexpected dependency lookup: {name}")
    monkeypatch.setattr(backend_validation.importlib.metadata, "version", forbidden)
    validate_config(load_config(_write_raven_config(tmp_path)))


# --- 2.5 plan identity ---------------------------------------------------------

@pytest.mark.parametrize("name", EXAMPLES)
def test_normalized_config_identical_with_and_without_explicit_raven(name: str, tmp_path: Path) -> None:
    golden = load_golden(f"{name}.normalized_config.json")
    assert normalized_example_config(name) == golden
    assert normalized_example_config(name, explicit_raven_backend=True, tmp_dir=tmp_path) == golden
    assert "backend" not in golden["render"] and "pyroomacoustics" not in golden


def test_normalized_config_records_pyroomacoustics_identity(tmp_path: Path) -> None:
    normalized = normalized_static_config(load_config(write_pyroomacoustics_config(tmp_path)))
    assert normalized["render"]["backend"] == "pyroomacoustics"
    assert normalized["pyroomacoustics"] == {"max_order": 3, "max_order_non_shoebox": 3, "air_absorption": False,
                                             "temperature_c": None, "humidity_pct": None}
    assert normalized["raven"] is None
