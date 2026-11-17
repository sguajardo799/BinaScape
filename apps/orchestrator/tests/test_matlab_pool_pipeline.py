"""`render-static` with `execution.matlab_runner: pool` against a fake pool session."""
import json
import threading
from pathlib import Path

import pytest
import yaml

from acoustic_orchestrator.pipeline import matlab_pool
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes
from test_generate_manifests import _write_valid_raven_metadata
from test_matlab_pool_runner import FakeSession
from test_render_retry_dispatch import circuit_path, configure, outcomes
from test_render_diagnostics_integration import successful_process


def _use_pool(path: Path) -> Path:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["execution"]["matlab_runner"] = "pool"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def render_snapshot(request: dict) -> int:
    manifest = json.loads(Path(request["snapshot_path"]).read_text(encoding="utf-8"))
    wav = Path(manifest["render"]["output_wav_path"])
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(b"simulated render")
    _write_valid_raven_metadata(manifest)
    return 0


class Recorder:
    """Fake launcher that also tracks how many jobs were outstanding at once."""

    def __init__(self, behavior=render_snapshot) -> None:
        self.behavior = behavior
        self.sessions: list[FakeSession] = []
        self.max_outstanding = 0
        self._lock = threading.Lock()

    def launch(self, command, session_dir, env, console):
        def observed(request: dict):
            with self._lock:
                requests = {p.stem for p in (session_dir / "requests").glob("*.json")}
                results = {p.stem for p in (session_dir / "results").glob("*.json")}
                self.max_outstanding = max(self.max_outstanding, len(requests - results))
            return self.behavior(request)
        session = FakeSession(session_dir, observed)
        self.sessions.append(session)
        return session


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(matlab_pool, "_launch_subprocess", recorder.launch)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *a, **k: pytest.fail("pool mode launched a per-attempt MATLAB process"))
    return recorder


def test_pool_run_renders_everything_in_one_session(tmp_path: Path, recorder: Recorder) -> None:
    path, layout = configure(tmp_path, scenes=2, workers=2)
    _use_pool(path)
    _, summary = render_static_scenes(path)
    records = outcomes(layout)
    assert len(records) == 6 and summary["render_jobs"] == 6
    assert all(record["decision"] == "continue" for record in records)
    assert all(record["matlab_runner"] == "pool" for record in records)
    assert {record["pool_session_id"] for record in records} == {recorder.sessions[0].session_dir.name}
    assert all(record["pool_worker_id"] == "worker-7" for record in records)
    assert len(recorder.sessions) == 1
    assert recorder.max_outstanding <= 2
    session = recorder.sessions[0]
    assert session.returncode == 0 and not session.killed  # Stopped through `stop`, not killed.
    invocation = read_json_object(session.session_dir / "invocation.json")
    assert invocation["exit_code"] == 0 and invocation["prefdir_cleanup"] == "removed"


def test_pool_session_stopped_when_circuit_opens(tmp_path: Path, recorder: Recorder) -> None:
    recorder.behavior = lambda request: 1
    path, layout = configure(tmp_path, scenes=2, workers=2, attempts=1, limit=1)
    _use_pool(path)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)
    assert read_json_object(circuit_path(layout))["open"] is True
    assert recorder.sessions and all(session.returncode is not None for session in recorder.sessions)
    records = outcomes(layout)
    assert records and all(record["process_status"] == "failed" and record["termination_observed"]
                           for record in records)


def test_pool_failures_are_retried_like_process_failures(tmp_path: Path, recorder: Recorder) -> None:
    calls: dict[str, int] = {}
    lock = threading.Lock()

    def flaky(request: dict) -> int:
        variant = request["completion_context"]["variant_id"]
        with lock:
            calls[variant] = calls.get(variant, 0) + 1
            first = calls[variant] == 1
        return 1 if first else render_snapshot(request)
    recorder.behavior = flaky
    path, layout = configure(tmp_path, scenes=1, workers=2, attempts=2, limit=10)
    _use_pool(path)
    render_static_scenes(path)
    records = outcomes(layout)
    assert len(records) == 6
    assert sorted(record["attempt_ordinal"] for record in records) == [1, 1, 1, 2, 2, 2]
    assert sum(record["decision"] == "continue" for record in records) == 3


def test_process_run_resumes_in_pool_mode_without_rendering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, layout = configure(tmp_path, scenes=1, workers=1)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", successful_process)
    render_static_scenes(path)
    before = {record["attempt_id"] for record in outcomes(layout)}
    recorder = Recorder()
    monkeypatch.setattr(matlab_pool, "_launch_subprocess", recorder.launch)
    _use_pool(path)
    _, summary = render_static_scenes(path)
    assert recorder.sessions == []  # Nothing pending: no MATLAB session is opened.
    assert {record["attempt_id"] for record in outcomes(layout)} == before
    assert summary["render_jobs"] == 0
