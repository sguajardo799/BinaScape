from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
import yaml

from acoustic_orchestrator.pipeline import process_retries, render_pipeline
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.stop_control import RenderStopped, StopControl
from test_render_resilience import configure, evidence
from test_render_diagnostics_integration import process_manifest
from test_generate_manifests import _write_valid_raven_metadata
from test_render_acceptance import publish_process_receipt


@pytest.mark.parametrize("scenario", ["raven_sequential", "raven_parallel", "raven_variants", "pra_sequential", "pra_parallel"])
def test_stop_then_relaunch_and_third_call_after_denied_classification(tmp_path, monkeypatch, scenario):
    from uuid import uuid4
    workspace = tmp_path.parent / ("gst_" + uuid4().hex[:8])
    workers = 2 if scenario.endswith("parallel") else 1
    optional = scenario == "raven_variants"
    if scenario.startswith("pra"):
        from acoustic_orchestrator.config import backend_validation
        from test_pra_resilience import configure as pra_configure, fake_worker, worker_arguments
        real_version = backend_validation.importlib.metadata.version
        monkeypatch.setattr(backend_validation.importlib.metadata, "version",
                            lambda name: "0.10.1" if name == "pyroomacoustics" else real_version(name))
        config, layout = pra_configure(workspace)
        raw = yaml.safe_load(config.read_text())
        raw["execution"].update(num_simulations=3, num_workers=workers)
        config.write_text(yaml.safe_dump(raw))
        manifest_for = lambda command: read_json_object(worker_arguments(command).snapshot)
        worker = fake_worker
    else:
        if optional:
            from test_render_retry_dispatch import configure as optional_configure
            config, layout = optional_configure(workspace, scenes=1, workers=1, optional=True)
        else:
            config, layout = configure(workspace, scenes=3, workers=workers)
        manifest_for = process_manifest
        def worker(command, **kwargs):
            _write_valid_raven_metadata(process_manifest(command))
            publish_process_receipt(command)
            return SimpleNamespace(returncode=0)
    control = StopControl()
    barrier = Barrier(workers, timeout=20)
    phases = [[], [], []]

    def identity(command):
        manifest = manifest_for(command)
        return manifest["scene_id"] + "__" + manifest["render"]["active_output_type"]

    def first(command, **kwargs):
        key = identity(command)
        phases[0].append(key)
        result = worker(command, **kwargs)
        if not optional or not key.endswith("__binaural_hrtf"):
            barrier.wait()
            control.request()
            control.request()  # Coalesce repeated requests, never force shutdown.
        return result

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", first)
    with pytest.raises(RenderStopped) as stopped:
        render_pipeline.render_static_scenes(config, stop=control)
    assert stopped.value.summary["matlab_invocations"] == len(phases[0]) == (2 if optional else workers)
    protected = evidence(layout)
    resource_root = layout["run_root"] / "metadata/raven_resources"
    for path in resource_root.rglob("*"):
        if path.is_file():
            protected[path] = (path.read_bytes(), path.stat().st_mtime_ns)
    original_save = process_retries.RenderAttempt.save
    denied = []

    def deny_classification(attempt):
        if attempt.record.get("retry_classification") == "accepted" and not denied:
            denied.append(attempt.attempt_id)
            raise PermissionError("second-call classification denied")
        return original_save(attempt)

    def second(command, **kwargs):
        key = identity(command)
        assert key not in phases[0]
        phases[1].append(key)
        return worker(command, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(process_retries.RenderAttempt, "save", deny_classification)
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", second)
        with pytest.raises(render_pipeline.RenderStaticRunError):
            render_pipeline.render_static_scenes(config)
    assert denied

    def third(command, **kwargs):
        key = identity(command)
        assert key not in phases[0] + phases[1]
        phases[2].append(key)
        return worker(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", third)
    _, summary = render_pipeline.render_static_scenes(config)
    assert summary["persistence_repairs"] == 1
    assert summary["matlab_invocations"] == len(phases[2])
    assert summary["resumed_variants"] == len(phases[0] + phases[1])
    assert summary["process_retries"] == 0
    circuit = read_json_object(layout["render_index_path"].parent / "matlab_process_circuit.json")
    ids = [resolution["attempt_id"] for resolution in circuit["resolutions"]]
    assert len(ids) == len(set(ids)) == sum(len(phase) for phase in phases)
    import hashlib
    for path, (content, mtime) in protected.items():
        actual = path.read_bytes() if isinstance(content, bytes) else hashlib.sha256(path.read_bytes()).hexdigest()
        assert (actual, path.stat().st_mtime_ns) == (content, mtime)
    _, again = render_pipeline.render_static_scenes(config)
    assert again["persistence_repairs"] == again["matlab_invocations"] == 0


def test_stop_during_actual_retry_wait_preserves_last_failure_and_remaining_budget(tmp_path, monkeypatch):
    import subprocess
    config, layout = configure(tmp_path, scenes=1)
    raw = yaml.safe_load(config.read_text())
    raw["execution"]["matlab_process_retry"].update(initial_delay_s=2, max_delay_s=2)
    config.write_text(yaml.safe_dump(raw))
    control = StopControl()

    def wait_for_retry(self, seconds):
        assert seconds > 0
        self.request()
        self.checkpoint()

    with monkeypatch.context() as patch:
        patch.setattr(StopControl, "wait_delay", wait_for_retry)
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                      lambda command, **kw: (_ for _ in ()).throw(subprocess.CalledProcessError(7, command)))
        with pytest.raises(RenderStopped) as caught:
            render_pipeline.render_static_scenes(config, stop=control)
    assert caught.value.summary["matlab_invocations"] == caught.value.summary["failed_matlab_processes"] == 1
    assert caught.value.summary["process_retries"] == 0
    assert any("código 7" in note for note in caught.value.__notes__)
    chain = read_json_object(next((layout["run_root"] / "metadata/process_chains").glob("*/chain.json")))
    assert chain["state"] == "scheduled" and len(chain["attempts"]) == 1


def test_pyroomacoustics_observed_pending_uses_v3_without_another_process(tmp_path, monkeypatch):
    import subprocess
    from acoustic_orchestrator.config import backend_validation
    from test_pra_resilience import configure as pra_configure, fake_worker

    real_version = backend_validation.importlib.metadata.version
    monkeypatch.setattr(backend_validation.importlib.metadata, "version",
                        lambda name: "0.10.1" if name == "pyroomacoustics" else real_version(name))
    config, layout = pra_configure(tmp_path)

    def run(command, **kwargs):
        fake_worker(command, **kwargs)
        raise subprocess.CalledProcessError(1, command)

    with monkeypatch.context() as patch:
        patch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
        patch.setattr(render_pipeline._RenderCoordinator, "resolve", lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            render_pipeline.render_static_scenes(config)
    path = next(layout["render_attempts_dir"].glob("*/outcome.json"))
    original = read_json_object(path)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", lambda *a, **kw: pytest.fail("no new process"))
    _, summary = render_pipeline.render_static_scenes(config)
    assert summary["persistence_repairs"] == summary["resumed_variants"] == summary["resumed_recovered_variants"] == 1
    assert summary["matlab_invocations"] == summary["recovered_matlab_processes"] == 0
    assert read_json_object(path)["schema_version"] == 3
    assert read_json_object(path)["error"] == original["error"]
