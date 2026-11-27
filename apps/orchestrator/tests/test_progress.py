import pytest

from dataclasses import replace

from acoustic_orchestrator.pipeline.progress import ProgressEvent, ProgressReporter, StageEstimate


def test_reporter_counts_logical_units_once_and_copies_counters(capsys):
    events = []
    report = ProgressReporter(events.append)
    report.begin("canonicas", 2, "escenas")
    report.start("one")
    report.count("invocaciones")
    report.count("reintentos")
    report.start("one")
    report.resolve("one", "aceptadas")
    report.resolve("one", "aceptadas")
    assert events[-1].completed == 1
    assert events[-1].counters == {"invocaciones": 1, "reintentos": 1, "aceptadas": 1}
    assert events[0].counters == {}
    assert capsys.readouterr() == ("", "")


def test_absent_or_broken_callback_is_silent_and_does_not_replace_error(capsys):
    calls = []

    def broken(event):
        calls.append(event)
        raise OSError("console unavailable")

    for callback in (None, broken):
        report = ProgressReporter(callback)
        with pytest.raises(ValueError, match="functional"):
            try:
                report.begin("test")
                report.start("one")
                raise ValueError("functional")
            finally:
                report.finish("interrumpido")
        assert not report.enabled
    assert len(calls) == 1
    assert capsys.readouterr() == ("", "")


def test_reporter_does_not_swallow_operator_interrupt():
    def interrupt(event):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ProgressReporter(interrupt).begin("test")


def test_sequential_eta_includes_retry_wait_and_excludes_reuse():
    now = [0.0]
    estimate = StageEstimate()
    events = []

    def observe(event):
        events.append(event)
        estimate.observe(event)

    report = ProgressReporter(observe, clock=lambda: now[0])
    report.begin("test", 25)
    for index in range(20):
        report.resolve(str(index), "reutilizadas")
    assert estimate.samples == 0
    for index in range(3):
        report.start(f"new{index}")
        now[0] += 10  # Includes the entire logical work, including retry wait.
        report.count("reintentos")
        report.resolve(f"new{index}", "aceptadas")
        if index < 2:
            assert estimate.remaining(events[-1]) is None
    assert estimate.remaining(events[-1]) == pytest.approx(20)
    report.resolve("failed", "fallidas")
    assert estimate.samples == 3
    assert estimate.remaining(events[-1]) == pytest.approx(10)
    report.finish("interrumpido")
    assert estimate.remaining(events[-1]) is None
    report.begin("empty", 0)
    assert estimate.samples == 0
    assert estimate.remaining(events[-1]) is None


def test_parallel_eta_uses_wall_time_once_and_validates_numbers():
    estimate = StageEstimate()
    event = ProgressEvent("begin", "parallel", total=10, parallel=True)
    estimate.observe(event)
    estimate.observe(replace(event, kind="start", elapsed=2))
    for index in range(3):
        estimate.observe(replace(event, kind="resolved", outcome="aceptadas", duration=10))
    observed = replace(event, kind="resolved", completed=3, elapsed=12)
    assert estimate.remaining(observed) == pytest.approx(70 / 3)
    assert estimate.remaining(replace(observed, elapsed=22)) == pytest.approx(140 / 3)
    assert estimate.remaining(replace(observed, elapsed=float("inf"))) is None
    assert estimate.remaining(replace(observed, elapsed=2)) == 1
    assert estimate.remaining(replace(observed, completed=10)) is None
    estimate.observe(replace(event, kind="resolved", outcome="aceptadas", duration=float("nan")))
    assert estimate.samples == 3


def test_plain_console_literal_text_and_disabled_mode():
    from io import StringIO
    from acoustic_orchestrator.terminal_progress import TerminalProgress

    stream = StringIO()
    with TerminalProgress(stream=stream) as terminal:
        report = ProgressReporter(terminal)
        report.begin("[literal]", 0, "escenas")
        report.finish()
    assert "[literal]" in stream.getvalue()
    assert "sin trabajo pendiente" in stream.getvalue()
    assert "\x1b" not in stream.getvalue()
    stream.seek(0)
    stream.truncate()
    with TerminalProgress(enabled=False, stream=stream) as terminal:
        ProgressReporter(terminal).begin("hidden")
    assert stream.getvalue() == ""
    assert terminal.thread is None


def test_interactive_console_has_one_owner_and_closes_on_interrupt():
    from io import StringIO
    from threading import current_thread
    from acoustic_orchestrator.terminal_progress import TerminalProgress

    class Terminal(StringIO):
        def isatty(self):
            return True

    stream = Terminal()
    terminal = TerminalProgress(stream=stream)
    owners = []
    original = terminal._consume

    def consume(event):
        owners.append(current_thread().name)
        original(event)

    terminal._consume = consume
    with pytest.raises(KeyboardInterrupt):
        with terminal:
            terminal.console.width = 30
            terminal(ProgressEvent("begin", "narrow", total=10))
            terminal(ProgressEvent("suspend", "narrow"))  # Also flushes queued events.
            terminal(ProgressEvent("resume", "narrow"))
            raise KeyboardInterrupt
    assert set(owners) == {"pipeline-progress"}
    assert not terminal.thread.is_alive()
    assert "interrumpida" in stream.getvalue()


@pytest.mark.parametrize("operation", ["_consume", "_refresh", "_stop_live"])
def test_console_failure_does_not_replace_functional_error(operation, monkeypatch):
    from io import StringIO
    from acoustic_orchestrator.terminal_progress import TerminalProgress

    stream = StringIO()
    stream.isatty = lambda: True
    terminal = TerminalProgress(stream=stream)

    def fail(*args, **kwargs):
        raise OSError("presentation")

    # Fail actual console cleanup rather than the guarded wrapper.
    if operation == "_stop_live":
        monkeypatch.setattr("acoustic_orchestrator.terminal_progress.Live.stop", fail)
    else:
        monkeypatch.setattr(terminal, operation, fail)
    with pytest.raises(ValueError, match="functional"):
        with terminal:
            terminal(ProgressEvent("begin", "test"))
            terminal(ProgressEvent("suspend", "test"))
            raise ValueError("functional")
    assert not terminal.thread.is_alive()


def test_generate_cli_progress_stderr_and_opt_out(tmp_path):
    from typer.testing import CliRunner
    from acoustic_orchestrator.cli import app
    from test_generate_manifests import _build_workspace, _write_config

    path = _write_config(_build_workspace(tmp_path), "progress_cli", num_simulations=1,
                         resume_if_possible=True, overwrite_existing=False)
    result = CliRunner().invoke(app, ["generate-manifests", str(path)])
    assert result.exit_code == 0, result.output
    assert "Generados 1 manifiestos" in result.stdout
    assert "Planificacion" not in result.stdout
    assert "Planificacion" in result.stderr
    assert "\x1b" not in result.stderr
    quiet = CliRunner().invoke(app, ["generate-manifests", str(path), "--no-progress"])
    assert quiet.exit_code == 0, quiet.output
    assert quiet.stderr == ""
    assert "Generados 1 manifiestos" in quiet.stdout


def test_canonical_progress_retry_then_protected_resume(tmp_path, monkeypatch):
    import subprocess
    from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes
    from test_render_resilience import configure, evidence
    from test_render_diagnostics_integration import successful_process

    path, layout = configure(tmp_path, scenes=1)
    calls = []

    def retry(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(29, command)
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", retry)
    events = []
    render_static_scenes(path, progress=events.append)
    resolved = [e for e in events if e.kind == "resolved" and e.stage == "Render canonico"]
    assert len(resolved) == 1
    assert resolved[0].outcome == "aceptadas"
    assert resolved[0].counters["invocaciones"] == 2
    assert resolved[0].counters["reintentos"] == 1
    before = evidence(layout)
    events.clear()
    _, summary = render_static_scenes(path, progress=events.append)
    assert summary["matlab_invocations"] == 0
    assert evidence(layout) == before
    reused = [e for e in events if e.kind == "resolved" and e.stage == "Render canonico"]
    assert [e.outcome for e in reused] == ["reutilizadas"]


@pytest.mark.parametrize("workers", [1, 2])
def test_remaining_variants_progress_and_reuse(tmp_path, monkeypatch, workers):
    import yaml
    from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes
    from test_render_diagnostics_integration import successful_process
    from test_generate_manifests import _build_workspace, _write_config

    path = _write_config(_build_workspace(tmp_path), "variants_progress", num_simulations=2,
                         num_workers=workers, resume_if_possible=True, overwrite_existing=False,
                         save_render_metadata=True, total_duration_s=0.01)
    raw = yaml.safe_load(path.read_text())
    raw["execution"]["matlab_process_retry"] = {"initial_delay_s": 0, "max_delay_s": 0}
    path.write_text(yaml.safe_dump(raw))
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", successful_process)
    events = []
    render_static_scenes(path, progress=events.append)
    resolved = [e for e in events if e.kind == "resolved" and e.stage == "Otras variantes"]
    assert len(resolved) == 4
    assert len({e.key for e in resolved}) == 4
    assert resolved[-1].completed == resolved[-1].total == 4
    assert resolved[-1].counters["invocaciones"] == 4
    events.clear()
    render_static_scenes(path, progress=events.append)
    reused = [e for e in events if e.kind == "resolved" and e.stage == "Otras variantes"]
    assert len(reused) == 4
    assert all(e.outcome == "reutilizadas" and e.duration is None for e in reused)


def test_incremental_activity_is_attempt_local_and_tolerates_torn_events(tmp_path, capsys):
    import json
    from acoustic_orchestrator.pipeline.matlab_diagnostics import MatlabActivityReader

    path = tmp_path / "events.jsonl"
    reader = MatlabActivityReader(path, "one")
    other = MatlabActivityReader(path, "other")
    assert "desconocida" in reader.poll()

    def event(sequence, **kwargs):
        return json.dumps(dict(schema_version=1, attempt_id="one", sequence=sequence,
                               timestamp="now", type="stage_started", stage="simulation", **kwargs)).encode()

    first = event(1)
    path.write_bytes(first[:20])
    assert "desconocida" in reader.poll()
    with path.open("ab") as stream:
        stream.write(first[20:] + b"\n" + b'bad\xff\n' + event(0) + b"\n")
    assert reader.poll() == "simulation"
    offset = reader.offset
    assert reader.poll() == "simulation"
    assert reader.offset == offset
    assert "desconocida" in other.poll()
    with path.open("ab") as stream:
        stream.write(json.dumps(dict(schema_version=1, attempt_id="one", sequence=2,
                                    timestamp="now", type="entrypoint_completed")).encode() + b"\n")
    assert "esperando terminacion" in reader.poll()
    assert capsys.readouterr() == ("", "")


def test_presentation_initialization_failure_is_isolated():
    from acoustic_orchestrator.terminal_progress import TerminalProgress

    class Broken:
        def isatty(self):
            raise OSError("console disconnected")

    with TerminalProgress(stream=Broken()) as terminal:
        ProgressReporter(terminal).begin("still runs")
    assert terminal.failed
    assert terminal.thread is None


def test_runner_interrupt_unregisters_activity_without_claiming_success(tmp_path, monkeypatch):
    from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes
    from test_render_resilience import configure

    path, _ = configure(tmp_path, scenes=1)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", interrupt)
    events = []
    with pytest.raises(KeyboardInterrupt):
        render_static_scenes(path, progress=events.append)
    starts = [e for e in events if e.kind == "attempt_begin"]
    ends = [e for e in events if e.kind == "attempt_end"]
    assert len(starts) == len(ends) == 1
    assert starts[0].attempt_id == ends[0].attempt_id
    assert not any(e.kind == "resolved" and e.stage == "Render canonico" for e in events)


def test_noninteractive_and_disabled_presentation_never_polls(tmp_path, monkeypatch):
    from io import StringIO
    from acoustic_orchestrator.terminal_progress import TerminalProgress

    monkeypatch.setattr("acoustic_orchestrator.terminal_progress.MatlabActivityReader.poll",
                        lambda self: pytest.fail("must not poll"))
    for enabled in (False, True):
        with TerminalProgress(enabled=enabled, stream=StringIO()) as terminal:
            terminal(ProgressEvent("attempt_begin", "render", attempt_id="id", events_path=str(tmp_path / "events")))
        assert terminal.thread is None
        assert not terminal.activities


def test_render_cli_stages_and_quiet_resume(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from acoustic_orchestrator.cli import app
    from test_render_resilience import configure
    from test_render_diagnostics_integration import successful_process

    path, _ = configure(tmp_path, scenes=1)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", successful_process)
    result = CliRunner().invoke(app, ["render-static", str(path)])
    assert result.exit_code == 0, result.output
    assert "Render canonico" in result.stderr
    assert "Otras variantes" in result.stderr
    assert "\x1b" not in result.stderr
    assert "ejecutados 1 render(s)" in result.stdout
    quiet = CliRunner().invoke(app, ["render-static", str(path), "--no-progress"])
    assert quiet.exit_code == 0, quiet.output
    assert quiet.stderr == ""
    assert "ejecutados 0 render(s)" in quiet.stdout


@pytest.mark.parametrize("returncode", [0, 1])
def test_handoff_cli_prepare_and_submit_propagates_opt_out(tmp_path, monkeypatch, returncode):
    import yaml
    from typer.testing import CliRunner
    from acoustic_orchestrator.cli import app
    from test_render_pipeline_clarity import _write_config, _prepare_completed_render_variant

    path = _write_config(tmp_path)
    _prepare_completed_render_variant(path)
    raw = yaml.safe_load(path.read_text())
    raw["hearing_degradation"]["runner"]["use_uv"] = False
    path.write_text(yaml.safe_dump(raw))
    prepared = CliRunner().invoke(app, ["clarity-handoff", str(path)])
    assert prepared.exit_code == 0, prepared.output
    assert "ejecucion pendiente" in prepared.stderr
    assert "Clarity handoff:" in prepared.stdout
    calls = []

    def submit(command, **kwargs):
        calls.append(command)
        return type("Completed", (), {"returncode": returncode})()

    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_runner.subprocess.run", submit)
    submitted = CliRunner().invoke(app, ["clarity-handoff", str(path), "--submit", "--no-progress"])
    assert submitted.exit_code == 0, submitted.output
    assert submitted.stderr == ""
    assert calls[0][-1] == "--no-progress"
    assert "Clarity handoff:" in submitted.stdout
    assert "exit code 1" in submitted.stdout if returncode else "enviado correctamente" in submitted.stdout


def test_enabled_disabled_equivalence_and_generation_contract(tmp_path, monkeypatch):
    import yaml
    from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes
    from acoustic_orchestrator.pipeline.static_plan import _generation_contract
    from test_render_resilience import configure, evidence
    from test_render_diagnostics_integration import successful_process

    path, layout = configure(tmp_path, scenes=3, overwrite=True, resume=False)
    generation = _generation_contract()
    calls = []

    def render(command, **kwargs):
        calls.append(command)
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", render)
    _, silent_summary = render_static_scenes(path)
    silent_bytes = {p: p.read_bytes() for p in evidence(layout)}
    assert len(calls) == 3
    events = []
    calls.clear()
    _, observed_summary = render_static_scenes(path, progress=events.append)
    assert len(calls) == 3
    assert {p: p.read_bytes() for p in evidence(layout)} == silent_bytes
    for field in ("render_jobs", "completed_variants", "failed_variants", "matlab_invocations", "sampling"):
        assert silent_summary[field] == observed_summary[field]
    raw = yaml.safe_load(path.read_text())
    raw["execution"].update(resume_if_possible=True, overwrite_existing=False)
    path.write_text(yaml.safe_dump(raw))
    protected = evidence(layout)
    events.clear()
    calls.clear()
    _, resumed = render_static_scenes(path, progress=events.append)
    assert resumed["matlab_invocations"] == 0
    assert not calls
    assert evidence(layout) == protected
    estimate = StageEstimate()
    for event in events:
        estimate.observe(event)
        assert estimate.samples == 0  # Planning and render reuse add no timing samples.
    assert _generation_contract() == generation


def test_long_running_activity_refreshes_elapsed_without_fabricating_completion(tmp_path):
    import json
    from io import StringIO
    from acoustic_orchestrator.terminal_progress import TerminalProgress

    stream = StringIO()
    stream.isatty = lambda: True
    now = [0.0]
    terminal = TerminalProgress(stream=stream, clock=lambda: now[0])
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(dict(schema_version=1, attempt_id="attempt", sequence=1,
                                   timestamp="now", type="step_started", stage="simulation", step="raven_run")) + "\n")
    terminal._consume(ProgressEvent("begin", "Render canonico", total=5, unit="escenas"))
    terminal._consume(ProgressEvent("attempt_begin", "Render canonico", total=5, unit="escenas",
                                   key="scene__hrtf", attempt_id="attempt", events_path=str(path)))
    now[0] = 125
    text = terminal._text(terminal.current).plain
    assert "elapsed=00:02:05" in text
    assert "simulation / raven_run" in text
    assert "0/5" in text
    assert "ETA calculando" in text
    terminal._consume(ProgressEvent("attempt_end", "Render canonico", total=5, key="scene__hrtf", attempt_id="attempt"))
    assert not terminal.activities
    assert "validando resultado" in terminal._text(terminal.current).plain


def test_parallel_out_of_order_resolution_is_counted_once(tmp_path, monkeypatch):
    from threading import Event
    from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes
    from test_render_diagnostics_integration import process_manifest, successful_process
    from test_generate_manifests import _build_workspace, _write_config

    path = _write_config(_build_workspace(tmp_path), "out_of_order", num_simulations=1, num_workers=2,
                         save_render_metadata=True, total_duration_s=0.01)
    release = Event()
    events = []

    def render(command, **kwargs):
        target = process_manifest(command)["render"]["active_output_type"]
        if target == "bte_front_hartf":
            assert release.wait(10), "second variant must be resolved first"
        return successful_process(command, **kwargs)

    def observe(event):
        events.append(event)
        if event.kind == "resolved" and event.key.endswith("bte_rear_hartf"):
            release.set()

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", render)
    render_static_scenes(path, progress=observe)
    resolved = [e for e in events if e.kind == "resolved" and e.stage == "Otras variantes"]
    assert [e.key.rsplit("__", 1)[-1] for e in resolved] == ["bte_rear_hartf", "bte_front_hartf"]
    assert [e.completed for e in resolved] == [1, 2]


def test_parallel_circuit_leaves_undispatched_jobs_pending(tmp_path, monkeypatch):
    import subprocess
    import yaml
    from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes
    from test_render_diagnostics_integration import process_manifest, successful_process
    from test_generate_manifests import _build_workspace, _write_config

    path = _write_config(_build_workspace(tmp_path), "circuit_progress", num_simulations=3, num_workers=2,
                         save_render_metadata=True, total_duration_s=0.01)
    raw = yaml.safe_load(path.read_text())
    raw["execution"]["matlab_process_retry"] = {"max_attempts": 1, "max_consecutive_failures": 1}
    path.write_text(yaml.safe_dump(raw))

    def render(command, **kwargs):
        if process_manifest(command)["render"]["active_output_type"] != "binaural_hrtf":
            raise subprocess.CalledProcessError(29, command)
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", render)
    events = []
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path, progress=events.append)
    final = events[-1]
    assert final.stage == "Otras variantes" and final.kind == "finish"
    assert final.outcome == "interrumpido"
    assert final.total == 6
    assert 1 <= final.completed <= 2
    assert final.counters["invocaciones"] <= 2
