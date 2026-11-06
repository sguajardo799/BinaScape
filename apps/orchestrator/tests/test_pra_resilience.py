"""[Linux] Retry, resume, recovery and diagnostics for Pyroomacoustics worker attempts (tasks 8.1–8.5).

The process boundary is ``matlab_runner.subprocess.run`` (the shared process core),
replaced by a deterministic fake worker that publishes outputs and records with the
real worker writers. 8.2 runs the real worker in-process (needs the extra).
"""

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from pra_support import write_tone_wav
from render_fixtures import write_render_metadata, write_render_wav
from test_pra_pipeline import PRA_T30, pra_installed, write_array_config  # noqa: F401 - autouse fixture

from acoustic_orchestrator.backends.pyroomacoustics.records import EventWriter, write_completion
from acoustic_orchestrator.backends.pyroomacoustics.worker import parse_args
from acoustic_orchestrator.cli import app
from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline import interruption_recovery as recovery
from acoustic_orchestrator.pipeline import process_retries as retries
from acoustic_orchestrator.pipeline import render_pipeline
from acoustic_orchestrator.pipeline.matlab_diagnostics import MatlabDiagnostics
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes

WORKER = "acoustic_orchestrator.backends.pyroomacoustics.worker"


def configure(tmp_path: Path, *, attempts: int = 3, limit: int = 5, resume: bool = True) -> tuple[Path, dict]:
    path = write_array_config(tmp_path, "pyroomacoustics", resume=resume)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["execution"]["matlab_process_retry"] = {"max_attempts": attempts, "initial_delay_s": 0, "max_delay_s": 0,
                                                "max_consecutive_failures": limit}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = load_config(path)
    return path, resolve_artifact_layout(config.outputs, config.experiment.experiment_id)


def worker_arguments(command: list[str]):
    assert command[1:3] == ["-m", WORKER], command
    return parse_args(command[3:])


def fake_worker(command: list[str], **kwargs) -> SimpleNamespace:
    """Publish outputs, sidecar, completion and events exactly as the worker contract requires."""
    args = worker_arguments(command)
    events = EventWriter(args.events_path, args.attempt_id)
    events.emit("stage_started", "initialization")
    manifest = json.loads(args.snapshot.read_text(encoding="utf-8"))
    events.emit("stage_completed", "initialization", output_type="circular_array")
    events.emit("stage_started", "simulation", output_type="circular_array")
    events.emit("step_started", "simulation", output_type="circular_array", step="pyroomacoustics_rir")
    events.emit("step_completed", "simulation", output_type="circular_array", step="pyroomacoustics_rir")
    events.emit("stage_completed", "simulation", output_type="circular_array")
    events.emit("stage_started", "artifact_writing")
    write_render_wav(manifest)
    events.emit("artifact_written", "artifact_writing", path=manifest["render"]["output_wav_path"], kind="wav")
    payload = write_render_metadata(manifest, [0.4] * 10)
    payload["summary"]["render_backend"] = "Pyroomacoustics"
    payload["summary"]["room"]["reverberation"].update(PRA_T30)
    payload["backend"] = {"name": "pyroomacoustics", "version": "0.10.1"}
    Path(manifest["render"]["output_metadata_path"]).write_text(json.dumps(payload), encoding="utf-8")
    events.emit("artifact_written", "artifact_writing", path=manifest["render"]["output_metadata_path"], kind="metadata")
    write_completion(snapshot_path=args.snapshot, attempt_id=args.attempt_id, variant_id=args.variant_id,
                     manifest_sha256=args.manifest_sha256, scene_id=manifest["scene_id"], output_type="circular_array",
                     completion_path=args.completion_path,
                     artifacts=[("wav", manifest["render"]["output_wav_path"]),
                                ("metadata", manifest["render"]["output_metadata_path"])])
    events.emit("stage_completed", "artifact_writing")
    events.emit("entrypoint_completed", "")
    if kwargs.get("stdout"):
        kwargs["stdout"].write(b"[INFO] Static render completed successfully\n")
    return SimpleNamespace(returncode=0)


def outcomes(layout: dict) -> list[dict]:
    return sorted((read_json_object(p) for p in layout["render_attempts_dir"].rglob("outcome.json")),
                  key=lambda record: record["started_at"])


def chains(layout: dict) -> list[dict]:
    return [read_json_object(p) for p in (layout["run_root"] / "metadata" / "process_chains").glob("*/chain.json")]


def circuit(layout: dict) -> dict:
    return read_json_object(layout["render_index_path"].parent / "matlab_process_circuit.json")


# --- 8.1 crash consumes the retry budget and counts toward the circuit ------------------------

def test_worker_crash_is_retried_with_same_snapshot_and_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, layout = configure(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(worker_arguments(command))
        if len(calls) == 1:
            kwargs["stdout"].write(b"Traceback: injected worker crash\n")
            raise subprocess.CalledProcessError(1, command)
        return fake_worker(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    _, summary = render_static_scenes(path)
    assert summary["matlab_invocations"] == 2 and summary["process_retries"] == 1
    assert summary["failed_matlab_processes"] == 1 and summary["variants_completed_after_retry"] == 1
    records = outcomes(layout)
    assert [r["exit_code"] for r in records] == [1, 0]
    assert records[0]["retry_classification"] == "unknown_process_failure"
    assert records[0]["retry_decision"] == "scheduled"
    assert len({r["chain_id"] for r in records}) == 1 and len({r["attempt_id"] for r in records}) == 2
    assert len({r["manifest_sha256"] for r in records}) == 1  # same snapshot input
    seeds = {json.loads(Path(r["manifest_snapshot_path"]).read_text())["render"]["seed"] for r in records}
    assert len(seeds) == 1
    [chain] = chains(layout)
    assert len(chain["attempts"]) == 2 and chain["input_sha256"] == records[0]["manifest_sha256"]
    record = circuit(layout)
    assert record["consecutive_failures"] == 0 and record["open"] is False and len(record["resolutions"]) == 2


def test_worker_crash_counts_toward_circuit_breaker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, layout = configure(tmp_path, limit=1)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)
    assert len(calls) == 1
    record = circuit(layout)
    assert record["open"] is True and record["consecutive_failures"] == 1


# --- 8.2 invalid snapshot → deterministic failure, not retried (real worker) --------------------

def test_invalid_snapshot_is_deterministic_and_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pra_support import require_pyroomacoustics

    require_pyroomacoustics()
    from acoustic_orchestrator.backends.pyroomacoustics.worker import main as worker_main

    path, layout = configure(tmp_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    noise_dir = path.parent / "assets" / "noise" / "cafeteria"
    for wav in noise_dir.glob("*.wav"):
        write_tone_wav(wav, seconds=0.2, channels=2)  # stereo noise for a 3-microphone array
    raw["background_noise"] = {"enabled": True, "snr_db": 10.0, "strategies": [
        {"type": "audio_folder", "noise_type": "cafeteria", "audio_dir": "./assets/noise/cafeteria"}]}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        worker_arguments(command)
        calls.append(command)
        code = worker_main(command[3:])
        if code:
            raise subprocess.CalledProcessError(code, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)
    assert len(calls) == 1
    [record] = outcomes(layout)
    assert record["retry_classification"] == "deterministic_failure" and record["retry_decision"] == "not_retryable"
    report = read_json_object(Path(record["failure_path"]))
    assert report["category"] == "invalid_input" and "channel mismatch" in report["error"]["message"]
    assert chains(layout)[0]["state"] == "not_retryable"
    # A restart does not renew the budget: still no new worker launch.
    with pytest.raises(Exception):
        render_static_scenes(path)
    assert len(calls) == 1


# --- 8.3 post_completion recovery ----------------------------------------------------------------

def test_post_completion_exit_is_recovered_with_visible_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, layout = configure(tmp_path)

    def run(command, **kwargs):
        fake_worker(command, **kwargs)
        raise subprocess.CalledProcessError(9, command)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    result = CliRunner().invoke(app, ["render-static", str(path), "--no-progress"])
    assert result.exit_code == 0, result.output
    assert "recuperados=1" in result.output
    assert "ADVERTENCIA" in result.output and "código 9" in result.output
    [record] = load_variant_index(layout["render_index_path"]).values()
    assert record["status"] == "completed"
    assert record["decision"] == "continue_with_warning" and record["recovery_reason"] == "post_completion"
    assert record["exit_code"] == 9 and record["process_status"] == "failed"


def test_post_completion_decision_through_evaluate_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from acoustic_orchestrator.pipeline.matlab_runner import render_manifest_with_pyroomacoustics
    from acoustic_orchestrator.pipeline.render_acceptance import evaluate_render
    from test_pra_pipeline import _variant_paths

    path, layout = configure(tmp_path)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", fake_worker)
    render_static_scenes(path)
    [record] = load_variant_index(layout["render_index_path"]).values()
    paths = _variant_paths(record)

    def run(command, **kwargs):
        fake_worker(command, **kwargs)
        raise subprocess.CalledProcessError(4, command)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    process = render_manifest_with_pyroomacoustics(Path(paths["runtime_manifest_path"]),
                                                   attempts_dir=tmp_path / "attempts", diagnostics_dir=tmp_path / "diag")
    acceptance = evaluate_render(process, paths, metadata_required=True, canonical=True)
    assert acceptance.decision == "continue_with_warning" and acceptance.recovered
    assert acceptance.as_record()["recovery_reason"] == "post_completion"


# --- 8.4 reconcile-static on an interrupted worker attempt --------------------------------------

def _pending_worker_attempt(tmp_path: Path):
    path, layout = configure(tmp_path)
    config = load_config(path)
    manifest_path = render_pipeline.generate_static_manifests(path)[0]
    scene = json.loads(manifest_path.read_text())
    rewrites = render_pipeline.prepare_scene_source_assets(scene, layout["prepared_audio_dir"] / scene["scene_id"])
    variant = render_pipeline._build_planned_variants_from_manifest(config, scene, layout["runtime_manifest_dir"])[0]
    task = render_pipeline._prepare_render_task(config, variant, layout["runtime_manifest_dir"], rewrites)
    chain = retries.open_chain(task["runtime_manifest_path"], layout["run_root"] / "metadata/process_chains",
                               config.execution.matlab_process_retry)
    attempt = chain.reserve_attempt(task["runtime_manifest_path"], layout["render_attempts_dir"])
    attempt.record["command"] = ["python", "-m", WORKER, attempt.snapshot_path.as_posix(), "--attempt-id",
                                 attempt.attempt_id]
    attempt.save()
    return path, layout, chain, attempt


def _files(root: Path) -> dict:
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def test_reconcile_static_on_interrupted_worker_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, layout, chain, attempt = _pending_worker_attempt(tmp_path)
    monkeypatch.setattr(recovery, "active_render_writers", lambda: [])
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("reconciliation must never launch a worker"))
    runner = CliRunner()
    args = ["reconcile-static", str(layout["run_root"]), "--chain", chain.record["chain_id"]]
    before = _files(layout["run_root"])
    inspected = runner.invoke(app, args)
    assert inspected.exit_code == 0, inspected.output
    assert "Solo lectura" in inspected.output and "restantes=2" in inspected.output
    assert _files(layout["run_root"]) == before
    guarded = runner.invoke(app, args + ["--apply", "--attempt", attempt.attempt_id])
    assert guarded.exit_code != 0 and _files(layout["run_root"]) == before
    applied = runner.invoke(app, args + ["--apply", "--attempt", attempt.attempt_id, "--confirm-stopped",
                                         "--reason", "Operator stopped all workers"])
    assert applied.exit_code == 0, applied.output
    assert "exit_code=null termination_observed=false" in applied.output
    assert read_json_object(attempt.outcome_path)["process_status"] == "interrupted"
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", fake_worker)
    _, summary = render_static_scenes(path)
    assert summary["completed_variants"] == summary["matlab_invocations"] == 1 and summary["process_retries"] == 1


def test_process_probe_blocks_running_pyroomacoustics_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"ProcessId": 200001, "Name": "python3", "CommandLine": f"/venv/bin/python -m {WORKER} /run/input_manifest.json"},
        {"ProcessId": 200002, "Name": "python3", "CommandLine": "python other_tool.py"},
    ]
    output = json.dumps(rows) if os.name == "nt" else "\n".join(
        f"{r['ProcessId']} {r['Name']} {r['CommandLine']}" for r in rows)
    monkeypatch.setattr(recovery.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=output))
    assert {row["pid"] for row in recovery.active_render_writers()} == {200001}


# --- 8.5 diagnostics ---------------------------------------------------------------------------------

def test_worker_attempt_diagnostics_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, layout = configure(tmp_path)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", fake_worker)
    render_static_scenes(path)
    [directory] = list(layout["matlab_diagnostics_dir"].iterdir())
    for name in ("invocation.json", "console.log", "events.jsonl"):
        assert (directory / name).is_file()
    invocation = read_json_object(directory / "invocation.json")
    assert invocation["command"][1:3] == ["-m", WORKER]
    assert invocation["entrypoint_completed"] is True and invocation["last_completed_step"] == "pyroomacoustics_rir"
    assert invocation["diagnostic_errors"] == []
    assert all(artifact["write_confirmed"] for artifact in invocation["artifacts"])
    assert b"completed successfully" in (directory / "console.log").read_bytes()


@pytest.mark.parametrize("diagnostic_failure", ["directory", "events", "summary"])
def test_diagnostic_failures_do_not_change_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    diagnostic_failure: str) -> None:
    def execute(path: Path, layout: dict) -> tuple[dict, dict]:
        _, summary = render_static_scenes(path)
        records = load_variant_index(layout["render_index_path"])
        stable = {key: {field: value for field, value in record.items()
                        if field not in {"updated_at", "attempt_id", "attempt_outcome_path", "completion_path",
                                         "runtime_manifest_path", "rendered_wav_path", "render_metadata_path",
                                         "metadata_snapshot", "observed_outputs"}}
                  for key, record in records.items()}
        return {key: summary[key] for key in ("completed_variants", "matlab_invocations", "failed_variants")}, stable

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", fake_worker)
    baseline = execute(*configure(tmp_path / "baseline"))
    broken_path, broken_layout = configure(tmp_path / "broken")

    def fail(*args, **kwargs):
        raise OSError("diagnostic system unavailable")

    if diagnostic_failure == "directory":
        original_mkdir = Path.mkdir

        def mkdir(self, *args, **kwargs):
            if self.is_relative_to(broken_layout["matlab_diagnostics_dir"]):
                fail()
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", mkdir)
    elif diagnostic_failure == "events":
        monkeypatch.setattr(MatlabDiagnostics, "read_events", fail)
    else:
        monkeypatch.setattr(MatlabDiagnostics, "save", fail)
    with pytest.warns(RuntimeWarning):
        assert execute(broken_path, broken_layout) == baseline
