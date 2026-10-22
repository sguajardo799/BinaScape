"""Real coordinator/runner integration with deterministic fake MATLAB processes."""

import subprocess
from collections import Counter
from pathlib import Path
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest
import yaml
from render_fixtures import write_completion_for_attempt

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline import render_pipeline
from acoustic_orchestrator.pipeline.output_index import load_clarity_index, load_variant_index
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.process_retries import BatchCircuit
from acoustic_orchestrator.pipeline.render_attempt import read_json_object
from acoustic_orchestrator.pipeline.render_pipeline import (
    RenderStaticRunError,
    render_static_scenes,
    run_clarity_handoff,
)
from test_generate_manifests import _build_workspace, _write_config, _write_valid_raven_metadata
from test_render_diagnostics_integration import process_manifest, successful_process


def configure(tmp_path, *, scenes=2, workers=2, attempts=3, limit=5, optional=True, clarity=False):
    workspace = _build_workspace(tmp_path)
    path = _write_config(workspace, "retry_dispatch", num_simulations=scenes, num_workers=workers,
                         include_optional_hartf_outputs=optional, resume_if_possible=True,
                         overwrite_existing=False, total_duration_s=0.01, save_render_metadata=True)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["execution"]["matlab_process_retry"] = {
        "max_attempts": attempts, "initial_delay_s": 0, "max_delay_s": 0,
        "max_consecutive_failures": limit,
    }
    if clarity:
        (workspace / "hearing_profiles.yaml").write_text(
            "profiles:\n  - hearing_profile_id: mild\n    ears:\n"
            "      left:\n        loss_db_by_band: {250: 10}\n"
            "      right:\n        loss_db_by_band: {250: 10}\n", encoding="utf-8",
        )
        raw["hearing_degradation"]["enabled"] = True
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path, resolve_artifact_layout(load_config(path).outputs, "sim_test")


def outcomes(layout):
    return [read_json_object(path) for path in layout["render_attempts_dir"].rglob("outcome.json")]


def circuit_path(layout):
    return layout["render_index_path"].parent / "matlab_process_circuit.json"


def chains(layout):
    return [read_json_object(path)
            for path in (layout["render_attempts_dir"].parent / "process_chains").glob("*/chain.json")]


@pytest.mark.parametrize("late_success", [False, True])
def test_parallel_cutoff_drains_inflight_without_launching_pending_or_retries(
    tmp_path, monkeypatch, late_success,
):
    path, layout = configure(tmp_path, limit=1)
    started = Barrier(2, timeout=15)
    cutoff_persisted = Event()
    lock = Lock()
    noncanonical_calls = []
    original_resolve = BatchCircuit.resolve

    def observe_resolution(self, *args, **kwargs):
        opened = original_resolve(self, *args, **kwargs)
        if opened:
            assert read_json_object(self.path)["open"] is True
            cutoff_persisted.set()
        return opened

    def run(command, **kwargs):
        manifest = process_manifest(command)
        if manifest["render"]["active_output_type"] == "binaural_hrtf":
            return successful_process(command, **kwargs)
        with lock:
            ordinal = len(noncanonical_calls)
            noncanonical_calls.append(command)
        assert ordinal < 2, "dispatch exceeded worker bound or launched after cutoff"
        started.wait()
        if ordinal == 1:
            assert cutoff_persisted.wait(15), "coordinator did not persist cutoff while worker was running"
            if late_success:
                return successful_process(command, **kwargs)
        raise subprocess.CalledProcessError(29, command)

    monkeypatch.setattr(BatchCircuit, "resolve", observe_resolution)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)

    assert len(noncanonical_calls) == 2  # Four optional jobs existed; only two started.
    records = outcomes(layout)
    assert len(records) == 4  # Two canonical renders plus the two bounded workers.
    optional = [record for record in records if record["output_type"] != "binaural_hrtf"]
    assert len(optional) == 2
    assert all(record["commit_status"] == "committed" for record in records)
    assert all(record["termination_observed"] for record in records)
    assert all(record["attempt_ordinal"] == 1 for record in optional)
    assert sum(record["decision"] == "continue" for record in optional) == int(late_success)
    circuit = read_json_object(circuit_path(layout))
    assert circuit["open"] is True
    assert len(circuit["resolutions"]) == 4
    assert circuit["resolutions"][-1]["classification"] == (
        "accepted" if late_success else "unknown_process_failure"
    )
    assert circuit["consecutive_failures"] == (0 if late_success else 2)
    assert all(chain["state"] == "circuit_open" for chain in chains(layout)
               if chain["artifacts"][0]["path"].find("binaural_hrtf") == -1)
    index = load_variant_index(layout["render_index_path"])
    assert sum(record["status"] == "planned" for record in index.values()) == 2

    # A new CLI process must respect the latched circuit, even after late success.
    before = {record["attempt_id"] for record in records}
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *args, **kwargs: pytest.fail("restart renewed a latched circuit"))
    with pytest.raises((RenderStaticRunError, RuntimeError, ValueError)):
        render_static_scenes(path)
    assert {record["attempt_id"] for record in outcomes(layout)} == before
    assert read_json_object(circuit_path(layout))["open"] is True


def test_parallel_acceptance_resets_streak_before_later_worker_failure(tmp_path, monkeypatch):
    path, layout = configure(tmp_path, scenes=1, attempts=2, limit=2)
    initial_workers = Barrier(2, timeout=15)
    first_failure_recorded = Event()
    retry_success_recorded = Event()
    lock = Lock()
    calls = Counter()
    roles = []
    original_resolve = BatchCircuit.resolve

    def observe_resolution(self, attempt_id, classification, **kwargs):
        opened = original_resolve(self, attempt_id, classification, **kwargs)
        attempt = read_json_object(layout["render_attempts_dir"] / attempt_id / "outcome.json")
        if attempt["output_type"] != "binaural_hrtf":
            if classification == "unknown_process_failure" and attempt["output_type"] == roles[0]:
                first_failure_recorded.set()
            if classification == "accepted" and attempt["output_type"] == roles[0]:
                assert self.record["consecutive_failures"] == 0
                retry_success_recorded.set()
        return opened

    def run(command, **kwargs):
        manifest = process_manifest(command)
        output_type = manifest["render"]["active_output_type"]
        if output_type == "binaural_hrtf":
            return successful_process(command, **kwargs)
        with lock:
            calls[output_type] += 1
            ordinal = calls[output_type]
            if output_type not in roles:
                roles.append(output_type)
            role = roles.index(output_type)
        if ordinal == 1:
            initial_workers.wait()
            if role == 1:
                assert retry_success_recorded.wait(15), "retry acceptance was not resolved before late failure"
            raise subprocess.CalledProcessError(11, command)
        assert first_failure_recorded.is_set()
        return successful_process(command, **kwargs)

    monkeypatch.setattr(BatchCircuit, "resolve", observe_resolution)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    _, summary = render_static_scenes(path)
    assert sorted(calls.values()) == [2, 2]
    assert summary["matlab_invocations"] == 5
    assert summary["process_retries"] == summary["variants_completed_after_retry"] == 2
    assert summary["completed_variants"] == 3
    circuit = read_json_object(circuit_path(layout))
    assert [record["classification"] for record in circuit["resolutions"]] == [
        "accepted", "unknown_process_failure", "accepted", "unknown_process_failure", "accepted",
    ]
    assert circuit["consecutive_failures"] == 0
    assert circuit["open"] is False
    assert all(record["commit_status"] == "committed" for record in outcomes(layout))


def test_noncanonical_exhausted_budget_is_not_renewed_on_restart(tmp_path, monkeypatch):
    path, layout = configure(tmp_path, scenes=1, attempts=2, limit=10)
    calls = Counter()

    def run(command, **kwargs):
        output_type = process_manifest(command)["render"]["active_output_type"]
        calls[output_type] += 1
        if output_type == "bte_front_hartf":
            raise subprocess.CalledProcessError(19, command)
        return successful_process(command, **kwargs)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)
    assert calls["bte_front_hartf"] == 2
    exhausted = [chain for chain in chains(layout) if chain["state"] == "exhausted"]
    assert len(exhausted) == 1
    before = {record["attempt_id"] for record in outcomes(layout)}
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run",
                        lambda *args, **kwargs: pytest.fail("restart renewed exhausted process budget"))
    with pytest.raises((RenderStaticRunError, RuntimeError, ValueError)):
        render_static_scenes(path)
    assert {record["attempt_id"] for record in outcomes(layout)} == before
    assert read_json_object(circuit_path(layout))["open"] is False


@pytest.mark.parametrize("first_exit_code", [0, 37])
def test_t30_rejection_opens_new_chain_without_consuming_process_retry_budget(
    tmp_path, monkeypatch, first_exit_code,
):
    path, layout = configure(tmp_path, scenes=1, attempts=1)
    original_sample = render_pipeline.sample_static_scene
    calls = []

    def sample(config, rng, scene_index, **kwargs):
        result = original_sample(config, rng, scene_index, **kwargs)
        if not kwargs:
            result["reverberation_sampling"]["attempts"] = 1
        return result

    def run(command, **kwargs):
        manifest = process_manifest(command)
        calls.append(manifest["render"]["active_output_type"])
        result = successful_process(command, **kwargs)
        if len(calls) == 1:
            _write_valid_raven_metadata(manifest, [0.5] * 9)
            attempt_id = Path(kwargs["stdout"].name).parent.name
            directory = layout["render_attempts_dir"] / attempt_id
            record = read_json_object(directory / "outcome.json")
            write_completion_for_attempt(SimpleNamespace(
                record=record, expected_artifacts=record["expected_artifacts"],
                completion_path=directory / "completion.json",
            ))
            if first_exit_code:
                raise subprocess.CalledProcessError(first_exit_code, command)
        return result

    monkeypatch.setattr(render_pipeline, "sample_static_scene", sample)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    manifests, summary = render_static_scenes(path)
    assert calls[:2] == ["binaural_hrtf", "binaural_hrtf"]
    assert summary["completed_variants"] == 3
    assert summary["matlab_invocations"] == 4
    assert summary["process_retries"] == 0
    canonical = [record for record in outcomes(layout) if record["output_type"] == "binaural_hrtf"]
    assert len(canonical) == 2
    assert len({record["chain_id"] for record in canonical}) == 2
    assert len({record["manifest_sha256"] for record in canonical}) == 2
    assert all(record["attempt_ordinal"] == 1 for record in canonical)
    rejected = next(record for record in canonical if record["decision"] == "retry_acoustic")
    accepted = next(record for record in canonical if record["decision"] == "continue")
    assert rejected["retry_classification"] == "acoustic_rejection"
    assert rejected["completion_validation"]["status"] == "valid"
    assert rejected["exit_code"] == first_exit_code
    current_chain = next(chain for chain in chains(layout) if chain["chain_id"] == accepted["chain_id"])
    assert rejected["chain_id"] in current_chain["supersedes"]
    assert read_json_object(manifests[0])["reverberation_sampling"]["raven_retry_count"] == 1
    assert read_json_object(circuit_path(layout))["consecutive_failures"] == 0


@pytest.mark.parametrize("complete_files", [False, True])
def test_clarity_never_receives_failed_attempt_outputs_without_completion(
    tmp_path, monkeypatch, complete_files,
):
    path, layout = configure(tmp_path, scenes=1, attempts=2, optional=False, clarity=True)
    calls = []
    submissions = []

    def run(command, **kwargs):
        manifest = process_manifest(command)
        calls.append(command)
        if complete_files:
            successful_process(command, **kwargs)  # No completion receipt is emitted.
        else:
            wav = Path(manifest["render"]["output_wav_path"])
            wav.parent.mkdir(parents=True, exist_ok=True)
            wav.write_bytes(b"partial PCM")
        raise subprocess.CalledProcessError(23, command)

    def submit(*args, **kwargs):
        submissions.append((args, kwargs))
        pytest.fail("Clarity received output from a failed incomplete attempt")

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    monkeypatch.setattr("acoustic_orchestrator.pipeline.clarity_handoff.submit_clarity_manifest", submit)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)
    assert len(calls) == 2
    assert all(record["decision"] == "abort" for record in outcomes(layout))
    handoff = run_clarity_handoff(path, submit=True)
    assert handoff["planned_jobs"] == 0
    assert handoff["submitted"] is False
    assert submissions == []
    assert not layout["clarity_jobs_path"].read_text(encoding="utf-8").strip()
    records = load_clarity_index(layout["clarity_index_path"])
    assert records and all(record["status"] in {"blocked", "skipped"} for record in records.values())
    assert all(not record["runnable"] for record in records.values())
