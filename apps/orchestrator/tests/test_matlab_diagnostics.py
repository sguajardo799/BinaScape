import json
import subprocess
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from threading import Barrier
from uuid import UUID

import pytest

from acoustic_orchestrator.pipeline.matlab_runner import render_manifest_with_matlab
from acoustic_orchestrator.pipeline.matlab_diagnostics import MatlabDiagnostics
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout


def write_manifest(tmp_path: Path, name: str = "scene__binaural_hrtf") -> Path:
    manifest = tmp_path / f"{name}.json"
    manifest.write_text(json.dumps({
        "scene_id": "scene", "receiver": {"hrtfs": [{"hrtf_id": "binaural_hrtf"}]},
        "render": {"active_output_type": "binaural_hrtf", "output_wav_path": str(tmp_path / "out.wav"),
                   "output_metadata_path": str(tmp_path / "out.json")},
    }), encoding="utf-8")
    return manifest


def test_attempt_paths_and_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0))
    manifest = write_manifest(tmp_path)
    render_manifest_with_matlab(manifest)
    original = manifest.read_bytes()
    manifest.write_text(manifest.read_text() + "\n", encoding="utf-8")
    render_manifest_with_matlab(manifest)
    attempts = list((tmp_path / "matlab_diagnostics").iterdir())
    assert len(attempts) == 2
    assert all(UUID(p.name) for p in attempts)
    assert {p.joinpath("input_manifest.json").read_bytes() for p in attempts} == {original, manifest.read_bytes()}
    explicit = tmp_path / "custom"
    render_manifest_with_matlab(manifest, diagnostics_dir=explicit)
    assert len(list(explicit.iterdir())) == 1


def test_layout_diagnostics_stays_in_run_root(tmp_path):
    outputs = SimpleNamespace(artifact_root=tmp_path, run_name="run")
    layout = resolve_artifact_layout(outputs, "experiment")
    assert layout["matlab_diagnostics_dir"] == tmp_path / "run" / "metadata" / "diagnostics" / "matlab"


def summary(tmp_path):
    return json.loads(next((tmp_path / "matlab_diagnostics").glob("*/invocation.json")).read_text(encoding="utf-8"))


@pytest.mark.parametrize("error", [None, subprocess.CalledProcessError(7, "matlab"), FileNotFoundError("matlab")])
def test_process_result_and_console(tmp_path, monkeypatch, error):
    manifest = write_manifest(tmp_path)
    def run(command, **kwargs):
        initial = summary(tmp_path)
        assert initial["process_status"] == "running"
        assert initial["exit_code"] is None
        assert kwargs["check"] is True
        assert kwargs["stderr"] == subprocess.STDOUT
        kwargs["stdout"].write(b"MATLAB stdout and stderr\n")
        if error:
            raise error
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    if error:
        with pytest.raises(type(error)) as caught:
            render_manifest_with_matlab(manifest)
        assert caught.value is error
    else:
        assert render_manifest_with_matlab(manifest) is None
    record = summary(tmp_path)
    assert record["process_status"] == ("succeeded" if error is None else "failed" if isinstance(error, subprocess.CalledProcessError) else "launch_failed")
    assert record["exit_code"] == (0 if error is None else 7 if isinstance(error, subprocess.CalledProcessError) else None)
    assert record["duration_s"] >= 0
    assert record["started_at"] <= record["finished_at"]
    assert Path(record["console_path"]).read_bytes() == b"MATLAB stdout and stderr\n"


def test_prelaunch_failure_and_matlab_path_escaping(tmp_path, monkeypatch):
    directory = tmp_path / "John's render"
    directory.mkdir()
    manifest = write_manifest(directory)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.MATLAB_ENTRYPOINT", tmp_path / "absent.m")
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(FileNotFoundError, match="entrypoint"):
        render_manifest_with_matlab(manifest)
    record = summary(directory)
    assert record["process_status"] == "launch_failed"
    assert "John''s render" in record["command"][2]
    assert record["command"][1] == "-batch"


def write_events(diagnostics, events):
    lines = [{"schema_version": 1, "attempt_id": diagnostics.attempt_id, "sequence": index,
              "timestamp": "2026-10-19T00:00:00Z", **event} for index, event in enumerate(events, 1)]
    diagnostics.events_path.write_text("".join(json.dumps(event) + "\n" for event in lines), encoding="utf-8")


def test_tolerant_events_and_old_outputs(tmp_path):
    manifest = write_manifest(tmp_path)
    (tmp_path / "out.wav").write_bytes(b"previous attempt")
    diag = MatlabDiagnostics(manifest)
    write_events(diag, [
        {"type": "stage_started", "stage": "initialization"},
        {"type": "stage_completed", "stage": "initialization"},
        {"type": "stage_started", "stage": "simulation"},
        {"type": "step_started", "stage": "simulation", "step": "raven_run"},
        {"type": "entrypoint_completed", "attempt_id": "wrong-attempt"},
    ])
    with diag.events_path.open("a", encoding="utf-8") as stream:
        stream.write('{"truncated":')
    with pytest.warns(RuntimeWarning) as caught:
        diag.finish(9, subprocess.CalledProcessError(9, "matlab"))
    assert len(caught) == 2
    record = diag.record
    assert record["failure_location"] == "simulation"
    assert record["last_started_stage"] == "simulation"
    assert record["last_completed_stage"] == "initialization"
    assert record["last_started_step"] == "raven_run"
    assert record["entrypoint_completed"] is False
    assert record["artifacts"][0] == {
        "kind": "wav", "path": (tmp_path / "out.wav").as_posix(),
        "observed_before": True, "observed_after": True, "write_confirmed": False,
    }


@pytest.mark.parametrize("corrupt_line", [b'{"message":"\xc3', b'{"message":"\xc3\n'])
def test_broken_utf8_tail_preserves_completed_events(tmp_path, corrupt_line):
    diag = MatlabDiagnostics(write_manifest(tmp_path))
    write_events(diag, [
        {"type": "stage_started", "stage": "initialization"},
        {"type": "stage_completed", "stage": "initialization"},
        {"type": "stage_started", "stage": "simulation"},
    ])
    with diag.events_path.open("ab") as stream:
        stream.write(corrupt_line)
    original_error = subprocess.CalledProcessError(9, "matlab")
    with pytest.warns(RuntimeWarning, match="events line 4") as caught:
        diag.finish(original_error.returncode, original_error)
    assert len(caught) == 1
    persisted = json.loads(diag.summary_path.read_text(encoding="utf-8"))
    assert persisted["last_started_stage"] == "simulation"
    assert persisted["last_completed_stage"] == "initialization"
    assert persisted["failure_location"] == "simulation"
    assert persisted["process_status"] == "failed"
    assert persisted["exit_code"] == 9
    assert persisted["entrypoint_completed"] is False
    assert len(persisted["diagnostic_errors"]) == 1


def test_corrupt_utf8_line_does_not_hide_later_valid_events(tmp_path):
    diag = MatlabDiagnostics(write_manifest(tmp_path))
    write_events(diag, [
        {"type": "stage_started", "stage": "initialization"},
        {"type": "stage_completed", "stage": "initialization"},
        {"type": "stage_started", "stage": "simulation"},
    ])
    lines = diag.events_path.read_bytes().splitlines(keepends=True)
    diag.events_path.write_bytes(lines[0] + b'{"message":"\xff"}\n' + b"".join(lines[1:]))
    with pytest.warns(RuntimeWarning, match="events line 2") as caught:
        diag.finish(1, subprocess.CalledProcessError(1, "matlab"))
    assert len(caught) == 1
    assert diag.record["last_started_stage"] == "simulation"
    assert diag.record["last_completed_stage"] == "initialization"
    assert diag.record["failure_location"] == "simulation"


@pytest.mark.parametrize("events,location", [
    ([], "indeterminate"),
    ([{"type": "stage_started", "stage": "initialization"}], "initialization"),
    ([{"type": "stage_started", "stage": "simulation"}], "simulation"),
    ([{"type": "stage_started", "stage": "artifact_writing"}], "artifact_writing"),
    ([{"type": "entrypoint_completed"}], "post_entrypoint"),
])
def test_progress_locations(tmp_path, events, location):
    diag = MatlabDiagnostics(write_manifest(tmp_path))
    write_events(diag, events)
    diag.finish(1, subprocess.CalledProcessError(1, "matlab"))
    assert diag.record["failure_location"] == location


def test_confirm_only_current_attempt_writes(tmp_path):
    diag = MatlabDiagnostics(write_manifest(tmp_path))
    (tmp_path / "out.wav").write_bytes(b"current")
    write_events(diag, [
        {"type": "stage_started", "stage": "artifact_writing"},
        {"type": "artifact_written", "path": str(tmp_path / "out.wav")},
    ])
    diag.finish(1, RuntimeError("metadata failed"))
    assert [a["write_confirmed"] for a in diag.record["artifacts"]] == [True, False]
    assert [a["observed_after"] for a in diag.record["artifacts"]] == [True, False]
    assert diag.record["last_completed_stage"] is None


@pytest.mark.parametrize("failure", ["directory", "snapshot", "open", "flush", "close", "events", "replace", "warning"])
@pytest.mark.parametrize("process_fails", [False, True])
def test_diagnostic_failures_never_replace_process_result(tmp_path, monkeypatch, failure, process_fails):
    manifest = write_manifest(tmp_path)
    original_error = subprocess.CalledProcessError(11, "original matlab command")
    calls = []
    def fail(*args, **kwargs):
        raise OSError("diagnostic-only failure")
    if failure == "directory":
        monkeypatch.setattr(Path, "mkdir", fail)
    elif failure == "snapshot":
        monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_diagnostics.shutil.copyfile", fail)
    elif failure in {"open", "flush", "close"}:
        original_open = Path.open
        class FailingConsole:
            def __init__(self, stream):
                self.stream = stream
            def write(self, data):
                return self.stream.write(data)
            def flush(self):
                if failure == "flush":
                    fail()
                self.stream.flush()
            def close(self):
                self.stream.close()
                if failure == "close":
                    fail()
        def open_file(path, *args, **kwargs):
            if path.name == "console.log":
                if failure == "open":
                    fail()
                return FailingConsole(original_open(path, *args, **kwargs))
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", open_file)
    elif failure == "events":
        monkeypatch.setattr(MatlabDiagnostics, "read_events", fail)
    elif failure == "replace":
        monkeypatch.setattr(Path, "replace", fail)
    else:
        monkeypatch.setattr(MatlabDiagnostics, "read_events", fail)
        monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_diagnostics.warnings.warn", fail)
        class BrokenStderr:
            def write(self, *args):
                fail()
            def flush(self):
                fail()
        monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_diagnostics.sys.stderr", BrokenStderr())
    def run(command, **kwargs):
        calls.append(command)
        if "stdout" in kwargs:
            kwargs["stdout"].write(b"render output")
        if process_fails:
            raise original_error
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    # Even warning-as-error policies must not turn diagnostic warnings into renderer failures.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        if process_fails:
            with pytest.raises(subprocess.CalledProcessError) as caught:
                render_manifest_with_matlab(manifest)
            assert caught.value is original_error
        else:
            render_manifest_with_matlab(manifest)
    assert len(calls) == 1


def test_readme_json_matches_summary_fields(tmp_path):
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Diagnóstico por invocación MATLAB", 1)[1].split("## Circular Array", 1)[0]
    example = json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])
    diag = MatlabDiagnostics(write_manifest(tmp_path))
    write_events(diag, [{"type": "entrypoint_completed"}])
    diag.finish(7, subprocess.CalledProcessError(7, "matlab"))
    assert {key: diag.record[key] for key in example} == example


def complete_events(tmp_path):
    return [
        {"type": "stage_started", "stage": "initialization"},
        {"type": "stage_completed", "stage": "initialization"},
        {"type": "stage_started", "stage": "simulation"},
        {"type": "step_started", "stage": "simulation", "step": "raven_run"},
        {"type": "step_completed", "stage": "simulation", "step": "raven_run"},
        {"type": "step_started", "stage": "simulation", "step": "audio_postprocessing"},
        {"type": "step_completed", "stage": "simulation", "step": "audio_postprocessing"},
        {"type": "stage_completed", "stage": "simulation"},
        {"type": "stage_started", "stage": "artifact_writing"},
        {"type": "artifact_written", "path": str(tmp_path / "out.wav")},
        {"type": "artifact_written", "path": str(tmp_path / "out.json")},
        {"type": "stage_completed", "stage": "artifact_writing"},
        {"type": "entrypoint_completed"},
    ]


@pytest.mark.parametrize("case,count,location,last_completed,last_step,confirmed", [
    ("launch", 0, "launch", None, None, [False, False]),
    ("before_entrypoint", 0, "indeterminate", None, None, [False, False]),
    ("initialization", 1, "initialization", None, None, [False, False]),
    ("raven_run", 4, "simulation", "initialization", "raven_run", [False, False]),
    ("audio_postprocessing", 6, "simulation", "initialization", "audio_postprocessing", [False, False]),
    ("wav", 9, "artifact_writing", "simulation", "audio_postprocessing", [False, False]),
    ("metadata", 10, "artifact_writing", "simulation", "audio_postprocessing", [True, False]),
    ("post_entrypoint", 13, "post_entrypoint", "artifact_writing", "audio_postprocessing", [True, True]),
])
def test_runner_failure_matrix(tmp_path, monkeypatch, case, count, location, last_completed, last_step, confirmed):
    manifest = write_manifest(tmp_path)
    error = PermissionError("launch denied") if case == "launch" else subprocess.CalledProcessError(23, "matlab")
    def run(command, **kwargs):
        initial = summary(tmp_path)
        diag = SimpleNamespace(attempt_id=initial["attempt_id"], events_path=Path(initial["events_path"]))
        write_events(diag, complete_events(tmp_path)[:count])
        raise error
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.raises(type(error)) as caught:
        render_manifest_with_matlab(manifest)
    assert caught.value is error
    record = summary(tmp_path)
    assert record["exit_code"] == (None if case == "launch" else 23)
    assert record["failure_location"] == location
    assert record["last_completed_stage"] == last_completed
    assert record["last_started_step"] == last_step
    assert [a["write_confirmed"] for a in record["artifacts"]] == confirmed
    assert record["entrypoint_completed"] is (case == "post_entrypoint")


@pytest.mark.parametrize("final_event,code", [(True, 0), (True, 3), (False, 0)])
def test_entrypoint_and_process_status_are_independent(tmp_path, monkeypatch, final_event, code):
    manifest = write_manifest(tmp_path)
    def run(command, **kwargs):
        initial = summary(tmp_path)
        diag = SimpleNamespace(attempt_id=initial["attempt_id"], events_path=Path(initial["events_path"]))
        write_events(diag, [{"type": "entrypoint_completed"}] if final_event else [])
        # Events never rewrite the process-owned initial summary.
        assert summary(tmp_path)["process_status"] == "running"
        if code:
            raise subprocess.CalledProcessError(code, command)
        return SimpleNamespace(returncode=code)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    if code:
        with pytest.raises(subprocess.CalledProcessError):
            render_manifest_with_matlab(manifest)
    else:
        render_manifest_with_matlab(manifest)
    record = summary(tmp_path)
    assert record["entrypoint_completed"] is final_event
    assert record["process_status"] == ("failed" if code else "succeeded")


def test_unobserved_termination_does_not_invent_success(tmp_path, monkeypatch):
    manifest = write_manifest(tmp_path)
    def run(command, **kwargs):
        initial = summary(tmp_path)
        diag = SimpleNamespace(attempt_id=initial["attempt_id"], events_path=Path(initial["events_path"]))
        write_events(diag, [{"type": "entrypoint_completed"}])
        raise KeyboardInterrupt()
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.raises(KeyboardInterrupt):
        render_manifest_with_matlab(manifest)
    record = summary(tmp_path)
    assert record["process_status"] == "running"
    assert record["exit_code"] is None
    assert record["duration_s"] is None
    assert record["finished_at"] is None
    assert "entrypoint_completed" in Path(record["events_path"]).read_text()


@pytest.mark.parametrize("same_input", [False, True])
def test_concurrent_attempt_directories_are_isolated(tmp_path, monkeypatch, same_input):
    inputs = []
    for marker in ("LEFT_INPUT_ONLY", "RIGHT_INPUT_ONLY"):
        directory = tmp_path / marker
        directory.mkdir()
        path = write_manifest(directory)
        payload = json.loads(path.read_text())
        payload["test_marker"] = marker
        path.write_text(json.dumps(payload), encoding="utf-8")
        inputs.append(path)
    if same_input:
        inputs[1] = inputs[0]
    root = tmp_path / "diagnostics"
    barrier = Barrier(2, timeout=10)
    replacements = []
    original_replace = Path.replace
    def replace(source, target):
        replacements.append((source, target))
        return original_replace(source, target)
    monkeypatch.setattr(Path, "replace", replace)
    def run(command, **kwargs):
        directory = Path(kwargs["stdout"].name).parent
        initial = json.loads((directory / "invocation.json").read_text())
        assert initial["process_status"] == "running"
        barrier.wait()
        kwargs["stdout"].write(directory.name.encode())
        write_events(SimpleNamespace(attempt_id=directory.name, events_path=directory / "events.jsonl"),
                     [{"type": "entrypoint_completed", "marker": directory.name}])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(render_manifest_with_matlab, path, diagnostics_dir=root) for path in inputs]
        for future in futures:
            future.result(timeout=20)
    directories = list(root.iterdir())
    assert len(directories) == 2
    expected_snapshots = sorted(path.read_bytes() for path in inputs)
    assert sorted((path / "input_manifest.json").read_bytes() for path in directories) == expected_snapshots
    for directory in directories:
        other = next(path for path in directories if path != directory)
        assert {path.name for path in directory.iterdir()} == {
            "invocation.json", "input_manifest.json", "events.jsonl", "console.log",
        }
        record = json.loads((directory / "invocation.json").read_text())
        assert record["attempt_id"] == directory.name
        assert record["process_status"] == "succeeded"
        assert record["entrypoint_completed"] is True
        for field in ("console_path", "events_path", "manifest_snapshot_path"):
            assert Path(record[field]).parent == directory
        assert (directory / "console.log").read_text() == directory.name
        for path in directory.iterdir():
            assert other.name not in path.read_text(encoding="utf-8")
    assert len(replacements) == 4
    assert all(source.parent == target.parent and source.parent in directories for source, target in replacements)


def test_concurrent_diagnostic_failure_does_not_affect_other_attempt(tmp_path, monkeypatch):
    inputs = []
    for name in ("broken", "healthy"):
        directory = tmp_path / name
        directory.mkdir()
        inputs.append(write_manifest(directory))
    root = tmp_path / "diagnostics"
    barrier = Barrier(2, timeout=10)
    calls = []
    original_save = MatlabDiagnostics.save
    def save(diag):
        if diag.manifest_path.parent.name == "broken" and diag.record["process_status"] != "running":
            raise OSError("only this attempt cannot finalize its summary")
        return original_save(diag)
    monkeypatch.setattr(MatlabDiagnostics, "save", save)
    def run(command, **kwargs):
        directory = Path(kwargs["stdout"].name).parent
        calls.append(directory)
        barrier.wait()
        kwargs["stdout"].write(directory.name.encode())
        write_events(SimpleNamespace(attempt_id=directory.name, events_path=directory / "events.jsonl"),
                     [{"type": "entrypoint_completed"}])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.warns(RuntimeWarning, match="only this attempt"):
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(render_manifest_with_matlab, path, diagnostics_dir=root) for path in inputs]
            assert [future.result(timeout=20) for future in futures] == [None, None]
    assert len(calls) == len(set(calls)) == 2
    summaries = [json.loads(path.read_text()) for path in root.glob("*/invocation.json")]
    healthy = next(record for record in summaries if Path(record["manifest_path"]).parent.name == "healthy")
    broken = next(record for record in summaries if record is not healthy)
    assert healthy["process_status"] == "succeeded"
    assert healthy["exit_code"] == 0
    assert healthy["entrypoint_completed"] is True
    assert healthy["diagnostic_errors"] == []
    assert Path(healthy["manifest_snapshot_path"]).read_bytes() == inputs[1].read_bytes()
    assert Path(healthy["console_path"]).read_text() == healthy["attempt_id"]
    assert json.loads(Path(healthy["events_path"]).read_text())["attempt_id"] == healthy["attempt_id"]
    assert broken["process_status"] == "running"  # Last persisted state, not a fabricated success.
    assert Path(healthy["console_path"]).parent != Path(broken["console_path"]).parent
