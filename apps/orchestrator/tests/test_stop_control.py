import signal

import pytest

from acoustic_orchestrator.pipeline.stop_control import RenderStopped, StopControl, cooperative_sigint


def test_request_only_sets_state_and_coalesces_without_io(monkeypatch):
    control = StopControl()
    previous = signal.getsignal(signal.SIGINT)
    with cooperative_sigint(control):
        handler = signal.getsignal(signal.SIGINT)
        monkeypatch.setattr("builtins.print", lambda *a, **kw: pytest.fail("handler must not print"))
        handler(signal.SIGINT, None)
        handler(signal.SIGINT, None)
        assert control.requested
        with pytest.raises(RenderStopped):
            control.checkpoint()
    assert signal.getsignal(signal.SIGINT) is previous


@pytest.mark.parametrize("early_release", [False, True])
def test_handler_restored_on_error_and_before_handoff(early_release):
    previous = signal.getsignal(signal.SIGINT)
    control = StopControl()
    with pytest.raises(ValueError):
        with cooperative_sigint(control):
            assert signal.getsignal(signal.SIGINT) is not previous
            if early_release:
                control.release_signals()
                assert signal.getsignal(signal.SIGINT) is previous
            raise ValueError("failed render")
    assert signal.getsignal(signal.SIGINT) is previous


def test_programmatic_default_does_not_install_signal_handler():
    before = signal.getsignal(signal.SIGINT)
    control = StopControl()
    control.checkpoint()
    control.release_signals()
    assert not control.requested
    assert signal.getsignal(signal.SIGINT) is before


def test_already_stopped_api_does_not_read_config_or_write(tmp_path):
    from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes

    control = StopControl()
    control.request()
    with pytest.raises(RenderStopped):
        render_static_scenes(tmp_path / "missing.yml", stop=control)
    assert list(tmp_path.iterdir()) == []


def test_process_options_are_platform_specific():
    from acoustic_orchestrator.pipeline.stop_control import render_process_options

    assert render_process_options("nt") == {"creationflags": 0x00000200}
    assert render_process_options("posix") == {"start_new_session": True}


@pytest.mark.parametrize("backend", ["matlab", "pyroomacoustics"])
def test_process_core_preserves_command_environment_and_streams(monkeypatch, backend):
    import subprocess
    from types import SimpleNamespace
    from acoustic_orchestrator.pipeline import matlab_runner
    from acoustic_orchestrator.pipeline.stop_control import render_process_options

    command = ["matlab", "-batch", "original command"] if backend == "matlab" else ["python", "-m", "worker"]
    stream = object()
    diagnostics = SimpleNamespace(begin=lambda cmd: stream, close_console=lambda console: None,
                                  safely=lambda call: call(), finish=lambda code, error: None)
    attempt = SimpleNamespace(record={}, save=lambda: None)
    env = {"UNCHANGED": "value"}
    calls = []

    def run(actual, **options):
        calls.append((actual, options))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(matlab_runner.subprocess, "run", run)
    result = matlab_runner._run_render_process(attempt, diagnostics, command, progress=None, env=env)
    assert calls == [(command, {"check": True, "stdout": stream, "stderr": subprocess.STDOUT,
                                "env": env, **render_process_options()})]
    assert result.exit_code == 0 and result.termination_observed


def test_real_console_signal_stops_parent_without_interrupting_child(tmp_path):
    """A private console/session prevents the test signal reaching pytest itself."""
    import os
    from pathlib import Path
    import subprocess
    import sys

    child = tmp_path / "child.py"
    child.write_text('''
import signal, sys, time
from pathlib import Path
signal.signal(signal.SIGINT, lambda *args: sys.exit(99))
Path(sys.argv[1]).write_text("ready")
time.sleep(0.5)
Path(sys.argv[2]).write_text("finished")
''', encoding="utf-8")
    parent = tmp_path / "parent.py"
    parent.write_text('''
import ctypes, os, signal, subprocess, sys, time
from pathlib import Path
from acoustic_orchestrator.pipeline.stop_control import StopControl, cooperative_sigint, render_process_options
if os.name == "nt":
    kernel = ctypes.windll.kernel32
    kernel.SetConsoleCtrlHandler(None, False)
control = StopControl()
with cooperative_sigint(control):
    process = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]], **render_process_options())
    try:
        deadline = time.monotonic() + 10
        while not Path(sys.argv[2]).exists():
            if time.monotonic() > deadline:
                raise RuntimeError("child never became ready")
            time.sleep(0.01)
        if os.name == "nt":
            if not kernel.GenerateConsoleCtrlEvent(0, 0):
                raise RuntimeError("GenerateConsoleCtrlEvent failed")
        else:
            os.killpg(os.getpgrp(), signal.SIGINT)
        code = process.wait(timeout=10)
        deadline = time.monotonic() + 2
        while not control.requested and time.monotonic() < deadline:
            time.sleep(0.01)
        assert control.requested, "parent did not observe SIGINT"
        assert code == 0, ("child was interrupted", code)
        assert Path(sys.argv[3]).read_text() == "finished"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
print("cooperative-stop-child-finished", flush=True)
''', encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    result = subprocess.run([sys.executable, str(parent), str(child), str(tmp_path / "ready"), str(tmp_path / "done")],
                            env=env, capture_output=True, text=True, timeout=30,
                            **({"creationflags": subprocess.CREATE_NEW_CONSOLE} if os.name == "nt"
                               else {"start_new_session": True}))
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cooperative-stop-child-finished" in result.stdout
