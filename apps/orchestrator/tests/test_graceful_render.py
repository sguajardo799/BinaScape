from pathlib import Path

import pytest

from acoustic_orchestrator.pipeline import render_pipeline, process_retries
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.stop_control import RenderStopped, StopControl
from test_render_resilience import configure
from test_render_diagnostics_integration import successful_process


def test_stop_after_plan_inspection_prevents_publication(tmp_path, monkeypatch):
    config, layout = configure(tmp_path, scenes=2)
    control = StopControl()
    original = render_pipeline.inspect_static_plan

    def inspect(*args, **kwargs):
        result = original(*args, **kwargs)
        control.request()
        return result

    monkeypatch.setattr(render_pipeline, "inspect_static_plan", inspect)
    monkeypatch.setattr(render_pipeline, "apply_static_plan", lambda *a, **kw: pytest.fail("no plan publication"))
    with pytest.raises(RenderStopped):
        render_pipeline.render_static_scenes(config, stop=control)
    assert not list(layout["scene_manifest_dir"].glob("*.json"))


@pytest.mark.parametrize("boundary", ["before", "during", "after"])
def test_reservation_authorization_is_not_abandoned_on_stop(tmp_path, monkeypatch, boundary):
    config, layout = configure(tmp_path, scenes=2)
    control = StopControl()
    original_prepare = render_pipeline._RenderCoordinator.prepare
    original_reserve = process_retries.RetryChain.reserve_attempt
    calls = []

    def prepare(coordinator, task):
        if boundary == "before":
            control.request()
        return original_prepare(coordinator, task)

    def reserve(chain, *args, **kwargs):
        if boundary == "during":
            control.request()
        attempt = original_reserve(chain, *args, **kwargs)
        if boundary == "after":
            control.request()
        return attempt

    def run(command, **kwargs):
        calls.append(command)
        return successful_process(command, **kwargs)

    monkeypatch.setattr(render_pipeline._RenderCoordinator, "prepare", prepare)
    monkeypatch.setattr(process_retries.RetryChain, "reserve_attempt", reserve)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.raises((RenderStopped, render_pipeline.RenderStaticRunError)):
        render_pipeline.render_static_scenes(config, stop=control)
    assert len(calls) == (0 if boundary == "before" else 1)
    chains = [read_json_object(p) for p in (layout["render_attempts_dir"].parent / "process_chains").glob("*/chain.json")]
    if boundary == "before":
        assert not chains
    else:
        assert len(chains) == 1
        assert chains[0]["state"] == "not_retryable"
        assert chains[0]["attempts"][-1]["state"] == "resolved"


def test_stop_interrupts_retry_wait_before_consuming_ordinal(monkeypatch):
    from acoustic_orchestrator.pipeline import stop_control

    control = StopControl()
    monkeypatch.setattr(stop_control.time, "sleep", lambda seconds: control.request())
    with pytest.raises(RenderStopped):
        control.wait_delay(60)


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("storage_failure", [False, True])
def test_stop_drains_parallel_results_without_dispatching_more(tmp_path, monkeypatch, optional, storage_failure):
    from threading import Barrier, Event
    from test_parallel_canonical_render import configure as parallel_configure
    from test_render_diagnostics_integration import process_manifest

    config, layout = parallel_configure(tmp_path, scenes=1 if optional else 4, workers=2, optional=optional)
    control = StopControl()
    barrier = Barrier(2, timeout=20)
    requested = Event()
    calls = []

    def run(command, **kwargs):
        manifest = process_manifest(command)
        calls.append(manifest["scene_id"] + "__" + manifest["render"]["active_output_type"])
        in_pool = not optional or manifest["render"]["active_output_type"] != "binaural_hrtf"
        if in_pool:
            barrier.wait()
            if manifest["scene_id"] == "scene_static_0001" and not optional or manifest["render"]["active_output_type"] == "bte_front_hartf":
                control.request()
                requested.set()
            else:
                assert requested.wait(10)
        return successful_process(command, **kwargs)

    real_save = process_retries.RenderAttempt.save
    denied = []

    def save(attempt):
        if storage_failure and control.requested and attempt.record.get("retry_classification") == "accepted" and not denied:
            denied.append(attempt.attempt_id)
            raise PermissionError("classification denied during stop")
        return real_save(attempt)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    monkeypatch.setattr(process_retries.RenderAttempt, "save", save)
    monkeypatch.setattr(render_pipeline, "prepare_clarity_handoff", lambda *a, **kw: pytest.fail("no handoff after stop"))
    error_type = render_pipeline.RenderStaticRunError if storage_failure else RenderStopped
    with pytest.raises(error_type) as caught:
        render_pipeline.render_static_scenes(config, stop=control)
    assert len(calls) == (3 if optional else 2)
    outcomes = [read_json_object(p) for p in layout["render_attempts_dir"].glob("*/outcome.json")]
    assert all(record["termination_observed"] for record in outcomes)
    chains = [read_json_object(p) for p in (layout["render_attempts_dir"].parent / "process_chains").glob("*/chain.json")]
    assert sum(chain["state"] == "running" for chain in chains) == int(storage_failure)
    if not storage_failure:
        assert caught.value.summary["matlab_invocations"] == len(calls)
    assert not (layout["render_index_path"].parent / "static_writer.lock").exists()


@pytest.mark.parametrize("attempts,limit", [(3, 5), (1, 5), (3, 1)])
def test_stop_preserves_scheduled_retry_deadline_budget_and_circuit(tmp_path, monkeypatch, attempts, limit):
    import subprocess
    import yaml

    config, layout = configure(tmp_path, scenes=1, attempts=attempts)
    raw = yaml.safe_load(config.read_text())
    raw["execution"]["matlab_process_retry"].update(initial_delay_s=2, max_delay_s=2,
                                                    max_consecutive_failures=limit)
    config.write_text(yaml.safe_dump(raw))
    control = StopControl()
    calls = []

    def failed(command, **kwargs):
        calls.append(command)
        control.request()
        raise subprocess.CalledProcessError(7, command)

    real_resolve = process_retries.RetryChain.resolve_attempt
    real_reserve = process_retries.RetryChain.reserve_attempt
    monkeypatch.setattr(process_retries.RetryChain, "resolve_attempt",
                        lambda self, *a, **kw: real_resolve(self, *a, **kw, clock=lambda: 100.0))
    monkeypatch.setattr(process_retries.RetryChain, "reserve_attempt",
                        lambda self, *a, **kw: real_reserve(self, *a, **kw, clock=lambda: 100.0))
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", failed)
    with pytest.raises(RenderStopped) as caught:
        render_pipeline.render_static_scenes(config, stop=control)
    assert len(calls) == caught.value.summary["matlab_invocations"] == caught.value.summary["failed_matlab_processes"] == 1
    chain_path = next((layout["render_attempts_dir"].parent / "process_chains").glob("*/chain.json"))
    chain = read_json_object(chain_path)
    first = read_json_object(Path(chain["attempts"][0]["outcome_path"]))
    expected = "circuit_open" if limit == 1 else "exhausted" if attempts == 1 else "scheduled"
    assert chain["state"] == expected
    assert first["exit_code"] == 7 and first["retry_classification"] == "unknown_process_failure"
    circuit_path = layout["render_index_path"].parent / "matlab_process_circuit.json"
    circuit = read_json_object(circuit_path)
    assert circuit["consecutive_failures"] == 1 and circuit["open"] == (limit == 1)
    if expected != "scheduled":
        monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                            lambda *a, **kw: pytest.fail("no renewed budget"))
        with pytest.raises((RuntimeError, ValueError)):
            render_pipeline.render_static_scenes(config)
        assert read_json_object(chain_path) == chain
        assert read_json_object(circuit_path) == circuit
        return
    assert chain["not_before"] == 102
    sleeps = []
    monkeypatch.setattr(StopControl, "wait_delay", lambda self, seconds: sleeps.append(seconds))
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", successful_process)
    _, summary = render_pipeline.render_static_scenes(config)
    assert sleeps == [2]
    assert summary["matlab_invocations"] == summary["process_retries"] == 1
    after = read_json_object(chain_path)
    second = read_json_object(Path(after["attempts"][-1]["outcome_path"]))
    assert len(after["attempts"]) == second["attempt_ordinal"] == 2
    assert second["attempt_id"] != first["attempt_id"]
    assert second["previous_attempt_id"] == first["attempt_id"]
    assert second["manifest_sha256"] == first["manifest_sha256"]
    assert read_json_object(circuit_path)["resolutions"][:1] == circuit["resolutions"]
