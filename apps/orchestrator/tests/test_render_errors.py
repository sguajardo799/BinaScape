import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner
from acoustic_orchestrator import cli

from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, _attach_render_error_context


def test_render_error_keeps_original_cause_notes_and_attempt_context(tmp_path):
    cause = subprocess.CalledProcessError(7, "matlab")
    cause.add_note("Functional acceptance was not committed: PermissionError: index denied")
    task = {"variant_paths": {"scene_id": "scene", "variant_id": "scene__hrtf"},
            "reserved_attempt": SimpleNamespace(attempt_id="attempt", outcome_path=tmp_path / "outcome.json",
                                                record={"chain_id": "chain"})}
    _attach_render_error_context(cause, task)
    error = RenderStaticRunError(manifest_paths=[], summary={}, cause=cause)
    assert error.__cause__ is cause
    assert "PermissionError" in cause.__notes__[0]
    assert error.context == {"scene_id": "scene", "variant_id": "scene__hrtf", "attempt_id": "attempt",
                             "chain_id": "chain", "evidence": (tmp_path / "outcome.json").as_posix()}


def test_render_error_before_attempt_does_not_invent_identity():
    cause = PermissionError("runtime denied")
    _attach_render_error_context(cause, {"variant_paths": {"scene_id": "scene", "variant_id": "scene__hrtf"}})
    error = RenderStaticRunError(manifest_paths=[], summary={}, cause=cause)
    assert error.context == {"scene_id": "scene", "variant_id": "scene__hrtf"}
    assert error.__cause__ is cause


@pytest.mark.parametrize("no_progress", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
def test_cli_explains_storage_error_with_or_without_summary(monkeypatch, no_progress, wrapped):
    def fail(*args, **kwargs):
        error = PermissionError("[WinError 5] Acceso denegado: outcome.json.tmp -> outcome.json")
        error.render_context = {"scene_id": "scene", "attempt_id": "attempt", "evidence": "outcome.json"}
        if wrapped:
            raise RenderStaticRunError(manifest_paths=[], summary={}, cause=error)
        raise error
    monkeypatch.setattr(cli, "render_static_scenes", fail)
    monkeypatch.setattr(cli, "_echo_render_summary", lambda *a: cli.typer.echo("summary available"))
    args = ["render-static", "config.yml"] + (["--no-progress"] if no_progress else [])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 1
    assert "PermissionError: [WinError 5]" in result.stderr
    assert "outcome.json.tmp -> outcome.json" in result.stderr
    assert "attempt_id=attempt" in result.stderr
    assert ("summary available" in result.stdout) == wrapped
    assert "antivirus" not in result.output


def test_cli_explains_both_process_and_storage_errors(monkeypatch):
    def fail(*args, **kwargs):
        try:
            raise PermissionError("index denied")
        except PermissionError as storage:
            error = subprocess.CalledProcessError(7, "matlab")
            error.add_note("Functional acceptance was not committed: index denied")
            raise error from storage
    monkeypatch.setattr(cli, "render_static_scenes", fail)
    result = CliRunner().invoke(cli.app, ["render-static", "config.yml", "--no-progress"])
    assert result.exit_code == 1
    assert "PermissionError: index denied" in result.stderr
    assert "CalledProcessError" in result.stderr
    assert "Functional acceptance was not committed" in result.stderr


@pytest.mark.parametrize("persistent", [False, True])
def test_cli_metadata_replacement_with_no_progress(monkeypatch, tmp_path, persistent):
    from pathlib import Path
    from acoustic_orchestrator.pipeline import atomic_replace as publication

    source, target = tmp_path / "outcome.json.tmp", tmp_path / "outcome.json"
    source.write_text("prepared", encoding="utf-8")
    original_replace = Path.replace
    calls, sleeps = [], []

    def replace(path, destination):
        calls.append(path)
        if persistent or len(calls) <= 2:
            error = PermissionError(13, "denied", str(path), 5, str(destination))
            error.winerror = 5
            raise error
        return original_replace(path, destination)

    def render(*args, **kwargs):
        try:
            publication.atomic_replace(source, target)
        except OSError as error:
            error.render_context = {"scene_id": "scene", "attempt_id": "attempt", "evidence": str(target)}
            raise
        return [], {"sampling": {"status": "completed"}}

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(publication.time, "sleep", sleeps.append)
    monkeypatch.setattr(cli, "render_static_scenes", render)
    monkeypatch.setattr(cli, "_echo_render_summary", lambda *args: None)
    result = CliRunner().invoke(cli.app, ["render-static", "config.yml", "--no-progress"])
    assert result.exit_code == int(persistent)
    assert len(calls) == (5 if persistent else 3)
    if persistent:
        assert "PermissionError" in result.stderr
        assert "5 attempts (0.75 s scheduled wait)" in result.stderr
        assert "outcome.json.tmp" in result.stderr and "outcome.json" in result.stderr
        assert "attempt_id=attempt" in result.stderr
        assert "antivirus" not in result.output
    else:
        assert target.read_text(encoding="utf-8") == "prepared"
