from pathlib import Path
import signal
import subprocess
from threading import Event
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from acoustic_orchestrator import cli
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.stop_control import StopControl
from test_generate_manifests import _write_valid_raven_metadata
from test_render_acceptance import publish_process_receipt
from test_render_diagnostics_integration import process_manifest
from test_render_resilience import configure


@pytest.mark.parametrize("no_progress", [False, True])
def test_cli_observes_stop_while_single_worker_is_still_running(tmp_path, monkeypatch, no_progress):
    config, layout = configure(tmp_path, scenes=2)
    active_seen = Event()
    controls, calls = [], []
    previous = signal.getsignal(signal.SIGINT)

    def control_factory(**kwargs):
        def notify(message):
            kwargs["notify"](message)
            if "esperando 1 procesos" in message:
                active_seen.set()
        control = StopControl(notify=notify)
        controls.append(control)
        return control

    def run(command, **kwargs):
        calls.append(command)
        controls[0].request()
        assert active_seen.wait(10), "coordinator did not observe stop while waiting for the process"
        _write_valid_raven_metadata(process_manifest(command))
        publish_process_receipt(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli, "StopControl", control_factory)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    result = CliRunner().invoke(cli.app, ["render-static", str(config)] + (["--no-progress"] if no_progress else []))
    assert result.exit_code == 130, result.output
    assert len(calls) == 1
    assert "Parada solicitada" in result.stderr and "esperando 1 procesos" in result.stderr
    assert "Render detenido por solicitud" in result.stderr and "ERROR render-static" not in result.stderr
    assert "\x1b[" not in result.stderr  # CliRunner is not a terminal.
    summary = read_json_object(layout["render_index_path"].parent / "static_run_summary.json")["render"]
    assert summary["stopped"] and summary["stop_reason"] == "operator_request"
    assert summary["matlab_invocations"] == summary["completed_variants"] == 1
    assert summary["total_variants"] == 2
    assert signal.getsignal(signal.SIGINT) is previous


@pytest.mark.parametrize("no_progress", [False, True])
def test_cli_storage_error_during_stop_retains_process_cause_and_exhaustion(tmp_path, monkeypatch, no_progress):
    config, _ = configure(tmp_path, scenes=2)
    controls, calls, denied = [], [], []

    def factory(**kwargs):
        control = StopControl(**kwargs)
        controls.append(control)
        return control

    def failed(command, **kwargs):
        calls.append(command)
        controls[0].request()
        raise subprocess.CalledProcessError(7, command)

    original = Path.replace

    def replace(path, target):
        if Path(target).name == "chain.json" and read_json_object(path)["state"] == "scheduled":
            denied.append(target)
            error = PermissionError(13, "denied", str(path), 5, str(target))
            error.winerror = 5
            raise error
        return original(path, target)

    monkeypatch.setattr(cli, "StopControl", factory)
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", failed)
    result = CliRunner().invoke(cli.app, ["render-static", str(config)] + (["--no-progress"] if no_progress else []))
    assert result.exit_code == 1, result.output
    assert len(calls) == 1 and len(denied) == 5
    assert "CalledProcessError" in result.stderr and "PermissionError" in result.stderr
    assert "chain.json" in result.stderr and "attempt_id=" in result.stderr
    assert "5 attempts (0.75 s scheduled wait)" in result.stderr
    assert "Render detenido por solicitud" not in result.stderr


def test_stop_notification_failure_does_not_change_functional_control():
    control = StopControl(notify=lambda message: (_ for _ in ()).throw(OSError("console unavailable")))
    control.request()
    control.observe(2)
    control.observe(1)
    assert control.requested
