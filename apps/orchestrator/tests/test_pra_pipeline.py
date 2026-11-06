"""Orchestrator integration of the Pyroomacoustics backend (tasks 6.1–6.7)."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

import acoustic_orchestrator.pipeline.render_pipeline as render_pipeline
from acoustic_orchestrator.config import backend_validation
from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline import matlab_runner
from acoustic_orchestrator.pipeline.matlab_diagnostics import MatlabActivityReader
from acoustic_orchestrator.pipeline.output_index import should_resume_variant
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.progress import ProgressEvent
from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt, read_json_object
from acoustic_orchestrator.pipeline.render_validation import equivalent_render_inputs, validate_render
from acoustic_orchestrator.pipeline.static_plan import StaticPlanConflict, _hash
from raven_golden import load_golden
from render_fixtures import simulate_matlab_process, write_render_metadata, write_render_wav
from test_generate_manifests import _build_workspace, _make_receiver_orientation_compatible, _write_config

PRA_T30 = {"method": "schroeder_backward_integration_v1", "estimated_from": "rir"}


@pytest.fixture(autouse=True)
def pra_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config validation only needs distribution metadata, never the package."""
    real = backend_validation.importlib.metadata.version
    monkeypatch.setattr(backend_validation.importlib.metadata, "version",
                        lambda name: "0.10.1" if name == "pyroomacoustics" else real(name))


def write_array_config(tmp_path: Path, backend: str, *, max_order: int = 3, resume: bool = True,
                       overwrite: bool = False, workspace: Path | None = None) -> Path:
    workspace = workspace or _build_workspace(tmp_path)
    path = _write_config(workspace, "array_backend", speech_directivity=None, total_duration_s=0.5,
                         include_optional_hartf_outputs=False)
    _make_receiver_orientation_compatible(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["receiver_outputs"] = {"circular_array": {"enabled": True, "num_microphones": 3, "radius_m": 0.05,
                                                  "required": True}}
    raw["hearing_degradation"].update(enabled=False, input_targets=[])
    raw["execution"].update(resume_if_possible=resume, overwrite_existing=overwrite, num_simulations=1,
                            save_render_metadata=True)
    distribution = raw["room_sampling"]["reverberation"]["distribution"]
    distribution["bin_width_s"] = distribution["range_s"]["max"] - distribution["range_s"]["min"]
    if backend == "pyroomacoustics":
        raw.pop("raven")
        raw["render"] = {"backend": "pyroomacoustics", "sample_rate_hz": 44100}
        raw["pyroomacoustics"] = {"max_order": max_order, "air_absorption": False}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def fake_pra_render(calls: list):
    def render(manifest_path: Path, *args, **kwargs) -> None:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        calls.append(manifest)
        assert manifest["render"]["backend"] == "pyroomacoustics"
        write_render_wav(manifest)
        payload = write_render_metadata(manifest, [0.4] * 10)
        payload["summary"]["render_backend"] = "Pyroomacoustics"
        payload["summary"]["room"]["reverberation"].update(PRA_T30)
        payload["backend"] = {"name": "pyroomacoustics", "version": "0.10.1"}
        Path(manifest["render"]["output_metadata_path"]).write_text(json.dumps(payload), encoding="utf-8")
    return simulate_matlab_process(render)


def fake_matlab_render(calls: list):
    def render(manifest_path: Path, *args, **kwargs) -> None:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        calls.append(manifest)
        write_render_wav(manifest)
        write_render_metadata(manifest, [0.4] * 10)
    return simulate_matlab_process(render)


def forbidden(name: str):
    def runner(*args, **kwargs):
        raise AssertionError(f"{name} must not be launched")
    return runner


def _layout(config_path: Path):
    config = load_config(config_path)
    return resolve_artifact_layout(config.outputs, config.experiment.experiment_id)


def file_snapshot(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(root.rglob("*")) if path.is_file()}


# --- 6.1 shared process core keeps the MATLAB command -------------------------------

def test_matlab_command_byte_identical_to_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from raven_golden import capture_matlab_command

    assert capture_matlab_command(tmp_path, monkeypatch) == load_golden("matlab_command.json")


# --- 6.2 Pyroomacoustics runner ------------------------------------------------------

def _runtime(tmp_path: Path) -> Path:
    path = tmp_path / "runtime" / "scene_static_0001__circular_array.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"scene_id": "scene_static_0001", "render": {
        "active_output_type": "circular_array", "backend": "pyroomacoustics",
        "output_wav_path": (tmp_path / "out" / "a.wav").as_posix(),
        "output_metadata_path": (tmp_path / "out" / "a.json").as_posix()}}), encoding="utf-8")
    return path


def test_worker_command_environment_and_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_run(command, check, **kwargs):
        seen.update(command=command, check=check, env=kwargs.get("env"))
        kwargs["stdout"].write(b"[INFO] worker output\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(matlab_runner.subprocess, "run", fake_run)
    path = _runtime(tmp_path)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    result = matlab_runner.render_manifest_with_pyroomacoustics(
        path, diagnostics_dir=tmp_path / "diag", attempts_dir=tmp_path / "attempts", attempt=attempt)
    assert seen["check"] is True
    assert seen["command"] == [
        sys.executable, "-m", "acoustic_orchestrator.backends.pyroomacoustics.worker", attempt.snapshot_path.as_posix(),
        "--attempt-id", attempt.attempt_id, "--variant-id", "scene_static_0001__circular_array",
        "--manifest-sha256", attempt.manifest_sha256, "--completion-path", attempt.completion_path.as_posix(),
        "--failure-path", attempt.failure_path.as_posix(),
        "--events-path", (tmp_path / "diag" / attempt.attempt_id / "events.jsonl").as_posix()]
    assert seen["env"]["OMP_NUM_THREADS"] == "1" and seen["env"]["PRA_NUM_THREADS"] == "1"
    assert result.process_status == "succeeded" and result.exit_code == 0 and result.error is None
    record = read_json_object(attempt.outcome_path)
    assert record["command"] == seen["command"] and record["process_status"] == "succeeded"
    diag = tmp_path / "diag" / attempt.attempt_id
    assert (diag / "console.log").read_bytes() == b"[INFO] worker output\n"
    assert read_json_object(diag / "invocation.json")["command"] == seen["command"]


def test_worker_non_zero_exit_has_called_process_error_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command, check, **kwargs):
        raise subprocess.CalledProcessError(3, command)

    monkeypatch.setattr(matlab_runner.subprocess, "run", fake_run)
    path = _runtime(tmp_path)
    result = matlab_runner.render_manifest_with_pyroomacoustics(path, attempts_dir=tmp_path / "attempts",
                                                                diagnostics_dir=tmp_path / "diag")
    assert isinstance(result.error, subprocess.CalledProcessError) and result.error.returncode == 3
    assert (result.process_status, result.exit_code, result.termination_observed) == ("failed", 3, True)
    launch = matlab_runner.render_manifest_with_pyroomacoustics
    monkeypatch.setattr(matlab_runner.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("x")))
    assert launch(path, attempts_dir=tmp_path / "attempts").process_status == "launch_failed"


# --- 6.3 dispatch by runtime backend and 6.4 manifests -------------------------------------

def test_raven_variants_reach_matlab_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", fake_matlab_render(calls))
    monkeypatch.setattr(render_pipeline, "render_manifest_with_pyroomacoustics", forbidden("pyroomacoustics worker"))
    _, summary = render_pipeline.render_static_scenes(write_array_config(tmp_path, "raven"))
    assert len(calls) == 1 and summary["completed_variants"] == 1
    assert "backend" not in calls[0]["render"] and "backend_parameters" not in calls[0]["render"]
    assert calls[0]["base_rpf_file"] is not None


def test_pyroomacoustics_variants_reach_worker_runner_with_backend_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list = []
    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", forbidden("MATLAB"))
    monkeypatch.setattr(render_pipeline, "render_manifest_with_pyroomacoustics", fake_pra_render(calls))
    config_path = write_array_config(tmp_path, "pyroomacoustics", max_order=4)
    _, summary = render_pipeline.render_static_scenes(config_path)
    assert len(calls) == 1 and summary["completed_variants"] == 1
    layout = _layout(config_path)
    scene = read_json_object(layout["scene_manifest_dir"] / "scene_static_0001.json")
    runtime = read_json_object(next(layout["runtime_manifest_dir"].glob("*.json")))
    for manifest in (scene, runtime):
        assert manifest["base_rpf_file"] is None
        assert manifest["render"]["backend"] == "pyroomacoustics"
        assert manifest["render"]["backend_parameters"] == {
            "max_order": 4, "max_order_non_shoebox": 4, "air_absorption": False,
            "temperature_c": None, "humidity_pct": None}
    plan = read_json_object(layout["render_index_path"].parent / "static_plan.json")
    assert not any("rpf" in key for key in plan["selected_dependencies"])


@pytest.mark.parametrize("name", ["circular_array_only", "static_example"])
def test_raven_manifests_still_equal_goldens(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from raven_golden import capture_example_manifests

    assert capture_example_manifests(tmp_path, name, monkeypatch) == load_golden(f"{name}.manifests.json")


# --- 6.5 backend-aware validation -------------------------------------------------------------

def _rendered_pra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list = []
    monkeypatch.setattr(render_pipeline, "render_manifest_with_pyroomacoustics", fake_pra_render(calls))
    config_path = write_array_config(tmp_path, "pyroomacoustics")
    render_pipeline.render_static_scenes(config_path)
    layout = _layout(config_path)
    record = json.loads(layout["render_index_path"].read_text(encoding="utf-8").splitlines()[0])
    return config_path, layout, record


def _variant_paths(record: dict) -> dict:
    """Planned variant paths (as ``build_render_variant_paths`` makes them) for an index record."""
    keys = ("variant_id", "scene_id", "output_type", "runtime_manifest_path", "rendered_wav_path",
            "render_metadata_path", "receiver_ir_path")
    paths = {key: record[key] for key in keys}
    runtime = read_json_object(Path(record["runtime_manifest_path"]))
    paths.update(circular_array=runtime["receiver"]["circular_array"], sample_rate_hz=runtime["render"]["sample_rate_hz"])
    return paths


def test_pra_descriptor_accepted_and_cross_backend_method_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, record = _rendered_pra(tmp_path, monkeypatch)
    paths = _variant_paths(record)
    assert validate_render(paths, metadata_required=True, canonical=True).validity == "valid"
    metadata_path = Path(paths["render_metadata_path"])
    sidecar = json.loads(metadata_path.read_text(encoding="utf-8"))
    sidecar["summary"]["room"]["reverberation"].update(method="rpf.getT30", estimated_from="room")
    metadata_path.write_text(json.dumps(sidecar), encoding="utf-8")
    result = validate_render(paths, metadata_required=True, canonical=True)
    assert "render_metadata_t30_method_inconsistent" in result.invalid


def test_raven_variant_with_pra_method_or_identity_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", fake_matlab_render(calls))
    config_path = write_array_config(tmp_path, "raven")
    render_pipeline.render_static_scenes(config_path)
    record = json.loads(_layout(config_path)["render_index_path"].read_text(encoding="utf-8").splitlines()[0])
    paths = _variant_paths(record)
    assert validate_render(paths, metadata_required=True, canonical=True).validity == "valid"
    metadata_path = Path(paths["render_metadata_path"])
    original = metadata_path.read_text(encoding="utf-8")
    sidecar = json.loads(original)
    sidecar["summary"]["room"]["reverberation"].update(PRA_T30)
    metadata_path.write_text(json.dumps(sidecar), encoding="utf-8")
    assert "render_metadata_t30_method_inconsistent" in validate_render(paths, metadata_required=True, canonical=True).invalid
    sidecar = json.loads(original)
    sidecar["backend"] = {"name": "pyroomacoustics"}
    metadata_path.write_text(json.dumps(sidecar), encoding="utf-8")
    assert "render_metadata_backend_identity_mismatch" in validate_render(paths, metadata_required=True).invalid


@pytest.mark.parametrize("backend", ["raven", "pyroomacoustics"])
def test_runtime_rate_outside_backend_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    calls: list = []
    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", fake_matlab_render(calls))
    monkeypatch.setattr(render_pipeline, "render_manifest_with_pyroomacoustics", fake_pra_render(calls))
    config_path = write_array_config(tmp_path, backend)
    render_pipeline.render_static_scenes(config_path)
    record = json.loads(_layout(config_path)["render_index_path"].read_text(encoding="utf-8").splitlines()[0])
    paths = _variant_paths(record)
    runtime_path = Path(paths["runtime_manifest_path"])
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["render"]["sample_rate_hz"] = 48000
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    result = validate_render(paths, metadata_required=True)
    assert "runtime_sample_rate_invalid" in result.invalid and result.validity == "invalid"
    runtime["render"]["backend"] = "odeon"
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    assert validate_render(paths, metadata_required=True).invalid == ["runtime_render_backend_invalid"]


# --- 6.6 progress from worker events ---------------------------------------------------------

def test_activity_reader_and_terminal_progress_accept_worker_step(tmp_path: Path) -> None:
    from io import StringIO

    from acoustic_orchestrator.terminal_progress import TerminalProgress

    path = tmp_path / "events.jsonl"
    events = [dict(type="stage_started", stage="simulation"),
              dict(type="step_started", stage="simulation", step="pyroomacoustics_rir")]
    path.write_text("".join(json.dumps(dict(schema_version=1, attempt_id="attempt", sequence=n, timestamp="t", **e)) + "\n"
                            for n, e in enumerate(events, 1)), encoding="utf-8")
    assert MatlabActivityReader(path, "attempt").poll() == "simulation / pyroomacoustics_rir"
    stream = StringIO()
    stream.isatty = lambda: True
    terminal = TerminalProgress(stream=stream, clock=lambda: 0.0)
    terminal._consume(ProgressEvent("begin", "Render canonico", total=1, unit="escenas"))
    terminal._consume(ProgressEvent("attempt_begin", "Render canonico", total=1, unit="escenas",
                                   key="scene__circular_array", attempt_id="attempt", events_path=str(path)))
    assert "simulation / pyroomacoustics_rir" in terminal._text(terminal.current).plain


# --- 6.7 resume isolation -------------------------------------------------------------------------

def test_raven_run_resumed_as_pyroomacoustics_reuses_nothing_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list = []
    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", fake_matlab_render(calls))
    raven_path = write_array_config(tmp_path, "raven")
    render_pipeline.render_static_scenes(raven_path)
    run_root = _layout(raven_path)["run_root"]
    before = file_snapshot(run_root)
    pra_path = write_array_config(tmp_path, "pyroomacoustics", workspace=raven_path.parent)
    monkeypatch.setattr(render_pipeline, "render_manifest_with_pyroomacoustics", forbidden("pyroomacoustics worker"))
    with pytest.raises(StaticPlanConflict, match="Configuration, catalog or generation contract changed"):
        render_pipeline.render_static_scenes(pra_path)
    assert file_snapshot(run_root) == before


def test_changed_ism_order_is_not_equivalent_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path, layout, record = _rendered_pra(tmp_path, monkeypatch)
    runtime = read_json_object(Path(record["runtime_manifest_path"]))
    changed = json.loads(json.dumps(runtime))
    changed["render"]["backend_parameters"]["max_order"] += 1
    assert equivalent_render_inputs(runtime, runtime)
    assert not equivalent_render_inputs(changed, runtime)
    records = {record["variant_id"]: record}
    assert should_resume_variant(records, _variant_paths(record), metadata_required=True, expected_manifest=runtime)
    assert not should_resume_variant(records, _variant_paths(record), metadata_required=True, expected_manifest=changed)
    raven_view = json.loads(json.dumps(runtime))
    raven_view["render"].pop("backend")
    raven_view["render"].pop("backend_parameters")
    assert not equivalent_render_inputs(raven_view, runtime)
    # Through the pipeline: an authorized regeneration re-renders instead of reusing.
    calls: list = []
    monkeypatch.setattr(render_pipeline, "render_manifest_with_pyroomacoustics", fake_pra_render(calls))
    changed_path = write_array_config(tmp_path, "pyroomacoustics", max_order=5, overwrite=True,
                                      workspace=config_path.parent)
    render_pipeline.render_static_scenes(changed_path)
    assert len(calls) == 1 and calls[0]["render"]["backend_parameters"]["max_order"] == 5


def test_unchanged_pyroomacoustics_run_reuses_without_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path, layout, _ = _rendered_pra(tmp_path, monkeypatch)
    monkeypatch.setattr(render_pipeline, "render_manifest_with_pyroomacoustics", forbidden("pyroomacoustics worker"))
    _, summary = render_pipeline.render_static_scenes(config_path)
    assert summary["matlab_invocations"] == 0 and summary["resumed_variants"] == 1
    assert summary["completed_variants"] == 1


def test_pre_upgrade_raven_plan_reports_conflict_without_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A plan whose generator fingerprint predates this change is a conflict, never reused or rewritten."""
    calls: list = []
    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", fake_matlab_render(calls))
    config_path = write_array_config(tmp_path, "raven")
    render_pipeline.render_static_scenes(config_path)
    plan_path = _layout(config_path)["render_index_path"].parent / "static_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["contract"]["generation"]["implementation"]["config/validator.py"] = "0" * 64  # pre-upgrade validator
    plan["contract_hash"] = _hash(plan["contract"])
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    run_root = _layout(config_path)["run_root"]
    before = file_snapshot(run_root)
    with pytest.raises(StaticPlanConflict, match="Generation contract changed"):
        render_pipeline.render_static_scenes(config_path)
    assert file_snapshot(run_root) == before
    assert len(calls) == 1
