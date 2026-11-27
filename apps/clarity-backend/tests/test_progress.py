import json

import pytest

from clarity_backend.cli import run_manifest
from clarity_backend.progress import ClarityProgress, JobEvent


def test_jobs_advance_after_processing_with_separate_failures(tmp_path, monkeypatch, capsys):
    from clarity_backend import cli

    manifest = tmp_path / "jobs.jsonl"
    manifest.write_text('\n{"job_id":"one"}\n\n{"job_id":"two"}\n')
    processed = []

    def process(job):
        processed.append(job["job_id"])
        (tmp_path / job["job_id"]).write_text("persisted result")
        return job["job_id"] == "one"

    events = []

    def observe(event):
        if event.kind == "resolved":
            assert (tmp_path / event.key).read_text() == "persisted result"
        events.append(event)

    monkeypatch.setattr(cli, "_process_job", process)
    with pytest.raises(SystemExit) as error:
        run_manifest(manifest, progress=observe)
    assert error.value.code == 1
    assert events[0].total == 2
    assert events[-1].completed == events[-1].failed == 1
    assert events[-1].status == "con fallos"
    assert processed == ["one", "two"]
    assert capsys.readouterr() == ("", "")


def test_empty_manifest_and_silent_api(tmp_path, capsys):
    manifest = tmp_path / "empty.jsonl"
    manifest.write_text("\n\n")
    events = []
    run_manifest(manifest, progress=events.append)
    assert [e.kind for e in events] == ["begin", "finish"]
    assert events[-1].total == 0
    run_manifest(manifest)
    assert capsys.readouterr() == ("", "")


def test_clarity_eta_warmup_failures_and_finite_values():
    from io import StringIO

    terminal = ClarityProgress(stream=StringIO())
    terminal(JobEvent("begin", 8))
    for index in range(3):
        terminal(JobEvent("resolved", 8, completed=index + 1, duration=10, status="completed"))
        if index < 2:
            assert terminal.eta() is None
    assert terminal.eta() == pytest.approx(50)
    terminal(JobEvent("resolved", 8, completed=3, failed=1, duration=0, status="failed"))
    assert terminal.samples == 3
    assert terminal.eta() == pytest.approx(40)
    terminal(JobEvent("resolved", 8, completed=3, failed=1, duration=float("nan"), status="completed"))
    assert terminal.samples == 3
    terminal(JobEvent("finish", 8, completed=3, failed=1, status="interrumpido"))
    assert terminal.eta() is None


def test_cli_plain_stderr_and_opt_out(tmp_path, monkeypatch, capsys):
    from clarity_backend.cli import main

    manifest = tmp_path / "empty.jsonl"
    manifest.write_text("\n")
    monkeypatch.setattr("sys.argv", ["clarity-backend", "run-manifest", str(manifest)])
    main()
    out = capsys.readouterr()
    assert out.out == ""
    assert "Degradado" in out.err
    assert "sin trabajo pendiente" in out.err
    assert "\x1b" not in out.err
    monkeypatch.setattr("sys.argv", ["clarity-backend", "run-manifest", str(manifest), "--no-progress"])
    main()
    assert capsys.readouterr() == ("", "")


def test_late_invalid_json_keeps_earlier_result_and_pending_counter(tmp_path, monkeypatch):
    from clarity_backend import cli

    manifest = tmp_path / "jobs.jsonl"
    manifest.write_text('{"job_id":"good"}\n{')
    written = tmp_path / "result"

    def process(job):
        written.write_text("complete")
        return True

    monkeypatch.setattr(cli, "_process_job", process)
    events = []
    with pytest.raises(json.JSONDecodeError):
        run_manifest(manifest, progress=events.append)
    assert written.read_text() == "complete"
    assert events[-1].status == "interrumpido"
    assert events[-1].completed == 1
    assert events[-1].total == 2


@pytest.mark.parametrize("failure", [OSError("metadata storage"), KeyboardInterrupt()])
def test_processing_interrupt_does_not_resolve_job(tmp_path, monkeypatch, failure):
    from clarity_backend import cli

    manifest = tmp_path / "jobs.jsonl"
    manifest.write_text('{"job_id":"pending"}')

    def process(job):
        raise failure

    monkeypatch.setattr(cli, "_process_job", process)
    events = []
    with pytest.raises(type(failure)):
        run_manifest(manifest, progress=events.append)
    assert events[-1].completed == events[-1].failed == 0
    assert events[-1].status == "interrumpido"


def test_broken_callback_does_not_change_failure_or_continue_emission(tmp_path, monkeypatch):
    from clarity_backend import cli

    manifest = tmp_path / "jobs.jsonl"
    manifest.write_text('{"job_id":"failure"}')
    monkeypatch.setattr(cli, "_process_job", lambda job: False)
    calls = []

    def broken(event):
        calls.append(event)
        raise OSError("console")

    with pytest.raises(SystemExit) as error:
        run_manifest(manifest, progress=broken)
    assert error.value.code == 1
    assert len(calls) == 1


@pytest.mark.parametrize("operation", ["start", "stop"])
def test_interactive_presentation_failure_preserves_original_error(monkeypatch, operation):
    from io import StringIO

    stream = StringIO()
    stream.isatty = lambda: True

    def broken(*args, **kwargs):
        raise OSError("console")

    monkeypatch.setattr(f"clarity_backend.progress.Live.{operation}", broken)
    with pytest.raises(ValueError, match="functional"):
        with ClarityProgress(stream=stream) as terminal:
            terminal(JobEvent("begin", 1))
            raise ValueError("functional")
    assert terminal.failed
