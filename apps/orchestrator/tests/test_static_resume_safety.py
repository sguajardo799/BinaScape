"""Regression tests for protected restart/T30 boundaries, using temporary runs."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from threading import Event
import wave

import pytest

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.models import MatlabProcessRetryConfig
from acoustic_orchestrator.pipeline import process_retries, render_pipeline
from acoustic_orchestrator.pipeline.matlab_runner import MatlabProcessResult
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.process_retries import RetryBlockedError, open_chain
from acoustic_orchestrator.pipeline.render_acceptance import evaluate_render, persist_acceptance
from render_fixtures import write_render_metadata, write_render_wav
from test_generate_manifests import _build_workspace, _write_config


class SimulatedInterruption(BaseException):
    """Bypass ordinary error handling, as an interrupted coordinator would."""


def _config(tmp_path, *, count=1, workers=1, multi_output=True, duration=None, process_attempts=1):
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(
        workspace, "resume_safety", num_simulations=count, num_workers=workers,
        include_optional_hartf_outputs=multi_output,
        resume_if_possible=True, overwrite_existing=False, save_render_metadata=True,
        total_duration_s=duration, min_sources=1, max_sources=1,
        speech_min_count=1, speech_max_count=1, clapping_min_count=0, clapping_max_count=0,
        process_max_attempts=process_attempts,
    )
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    return workspace, config_path, config, layout


def _snapshot(paths):
    return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}


def _run_snapshot(root):
    return _snapshot(path for path in root.rglob("*") if path.is_file())


def _process(path, options, *, code=0, t30=None, partial=False):
    """Return real attempt evidence without launching an external process."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    attempt = options["attempt"]
    if code == 0:
        write_render_wav(manifest)
        write_render_metadata(manifest, t30_s=t30)
    elif partial:
        output = Path(manifest["render"]["output_wav_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"RIFF-partial-attempt-output")
    error = subprocess.CalledProcessError(code, ["simulated-matlab", str(path)]) if code else None
    status = "failed" if code else "succeeded"
    attempt.record.update(process_status=status, exit_code=code, termination_observed=True)
    attempt.save()
    return MatlabProcessResult(attempt, code, error, True, status)


def _good_process(path, *args, **kwargs):
    return _process(path, kwargs)


def _force_available_t30_retry(monkeypatch, *, original_audio=None, replacement_audio=None):
    original_sample = render_pipeline.sample_static_scene
    sampled = []

    def sample(config, rng, index, **kwargs):
        scene = original_sample(config, rng, index, **kwargs)
        if not kwargs:
            # Reserve acoustic budget even when the fixture's initial proposal
            # came from the fallback reservoir at the end of its search.
            scene["reverberation_sampling"]["attempts"] = 1
        else:
            scene["sources"][0]["gain_db"] += 0.125
        audio = replacement_audio if kwargs else original_audio
        if audio is not None:
            scene["sources"][0].update(audio_path=audio, audio_relpath=audio.name, identity_id="")
        sampled.append(deepcopy(scene))
        return scene

    monkeypatch.setattr(render_pipeline, "sample_static_scene", sample)
    return sampled


def test_protected_secondary_survives_missing_canonical_t30_rejection(tmp_path, monkeypatch):
    _, config_path, config, layout = _config(tmp_path)
    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", _good_process)
    manifests, _ = render_pipeline.render_static_scenes(config_path)
    variants = render_pipeline._build_planned_variants(config, manifests, layout["runtime_manifest_dir"])
    canonical = variants[0]["variant_paths"]
    Path(canonical["rendered_wav_path"]).unlink()
    Path(canonical["render_metadata_path"]).unlink()
    protected = [*manifests]
    for variant in variants[1:]:
        paths = variant["variant_paths"]
        protected.extend(Path(paths[key]) for key in
                         ("runtime_manifest_path", "rendered_wav_path", "render_metadata_path"))
    before = _snapshot(protected)
    invocations = []

    def reject(path, *args, **kwargs):
        invocations.append(json.loads(path.read_text())["render"]["active_output_type"])
        return _process(path, kwargs, t30=[0.5] * 9)

    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", reject)
    monkeypatch.setattr(render_pipeline, "sample_static_scene",
                        lambda *a, **kw: pytest.fail("Protected scene must not be resampled"))
    with pytest.raises(render_pipeline.RenderStaticRunError) as caught:
        render_pipeline.render_static_scenes(config_path)
    assert "protected" in str(caught.value).lower()
    assert invocations == [canonical["output_type"]]
    assert _snapshot(protected) == before


def test_interrupted_retry_archive_resumes_persisted_t30_candidate(tmp_path, monkeypatch):
    _, config_path, _, layout = _config(tmp_path, multi_output=False, process_attempts=3)
    sampled = _force_available_t30_retry(monkeypatch)
    invocations = []
    candidates = []

    def render(path, *args, **kwargs):
        runtime = json.loads(path.read_text())
        invocations.append(kwargs["attempt"].attempt_id)
        if len(invocations) == 1:
            return _process(path, kwargs, t30=[0.5] * 9)
        scene_path = layout["scene_manifest_dir"] / f"{runtime['scene_id']}.json"
        scene = json.loads(scene_path.read_text())
        assert scene["sources"][0]["gain_db"] == runtime["sources"][0]["gain_db"]
        assert scene["reverberation_sampling"]["raven_retry_count"] == 1
        candidates.append(scene)
        return _process(path, kwargs, code=1, partial=True)

    save = process_retries._save
    interrupted = []

    def interrupt_after_move(path, record):
        if (path.name == "archive.json" and record.get("state") == "pending"
                and any(entry.get("state") == "archived" for entry in record.get("entries", []))
                and not interrupted):
            interrupted.append(path)
            raise SimulatedInterruption("after moving retry output, before archive commit")
        return save(path, record)

    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", render)
    monkeypatch.setattr(process_retries, "_save", interrupt_after_move)
    with pytest.raises(SimulatedInterruption):
        render_pipeline.render_static_scenes(config_path)
    assert len(invocations) == 2
    assert len(sampled) == 2
    assert interrupted
    candidate = candidates[-1]
    scene_path = layout["scene_manifest_dir"] / f"{candidate['scene_id']}.json"
    protected_scene = _snapshot([scene_path])
    scheduled = [json.loads(p.read_text()) for p in
                 (layout["render_attempts_dir"].parent / "process_chains").glob("*/chain.json")
                 if json.loads(p.read_text())["state"] == "scheduled"]
    assert len(scheduled) == 1
    first_id = scheduled[0]["attempts"][-1]["attempt_id"]
    first_outcome = layout["render_attempts_dir"] / first_id / "outcome.json"
    old_failure = first_outcome.read_bytes()
    resumed = []

    def succeed(path, *args, **kwargs):
        runtime = json.loads(path.read_text())
        assert runtime["sources"][0]["gain_db"] == candidate["sources"][0]["gain_db"]
        resumed.append(dict(kwargs["attempt"].record))
        return _process(path, kwargs)

    monkeypatch.setattr(process_retries, "_save", save)
    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", succeed)
    monkeypatch.setattr(render_pipeline, "sample_static_scene",
                        lambda *a, **kw: pytest.fail("Persisted T30 candidate was resampled"))
    manifests, summary = render_pipeline.render_static_scenes(config_path)
    assert len(manifests) == 1
    assert len(resumed) == 1
    assert resumed[0]["attempt_ordinal"] == 2
    assert resumed[0]["previous_attempt_id"] == first_id
    assert resumed[0]["chain_id"] == scheduled[0]["chain_id"]
    assert resumed[0]["manifest_sha256"] == scheduled[0]["input_sha256"]
    assert first_outcome.read_bytes() == old_failure
    assert _snapshot([scene_path]) == protected_scene
    assert summary["matlab_invocations"] == 1
    assert summary["process_retries"] == 1
    journal = json.loads(interrupted[0].read_text())
    assert journal["state"] == "complete"
    assert all(Path(entry["archive_path"]).is_file() for entry in journal["entries"])


def test_t30_changed_pcm_same_source_id_preserves_old_crop_in_archive(tmp_path, monkeypatch):
    workspace, config_path, _, layout = _config(tmp_path, multi_output=False, duration=1.0)
    original = workspace / "assets" / "events" / "speech" / "speech_01.wav"
    alternate = original.with_name("alternate.wav")
    with wave.open(str(original), "rb") as audio:
        params = audio.getparams()
    with wave.open(str(alternate), "wb") as audio:
        audio.setparams(params)
        audio.writeframes(b"\x01\x00" * params.nframes * params.nchannels)
    _force_available_t30_retry(monkeypatch, original_audio=original, replacement_audio=alternate)
    crops = []

    def render(path, *args, **kwargs):
        runtime = json.loads(path.read_text())
        cropped = Path(runtime["sources"][0]["audio_path"])
        assert cropped.is_relative_to(layout["prepared_audio_dir"])
        crops.append(cropped.read_bytes())
        return _process(path, kwargs, t30=[0.5] * (9 if len(crops) == 1 else 10))

    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", render)
    manifests, summary = render_pipeline.render_static_scenes(config_path)
    assert len(manifests) == 1
    assert len(crops) == 2 and crops[0] != crops[1]
    assert summary["completed_variants"] == 1
    archived = []
    for journal_path in (layout["run_root"] / "metadata").rglob("archive.json"):
        journal = json.loads(journal_path.read_text())
        for entry in journal.get("entries", []):
            path = Path(entry["archive_path"])
            if path.suffix == ".wav" and path.is_file() and path.read_bytes() == crops[0]:
                archived.append((journal, entry))
    assert archived, "Original cropped PCM must remain in journaled archive evidence"
    assert all(journal["state"] == "complete" for journal, _ in archived)


@pytest.mark.parametrize("state", ["running", "exhausted"])
def test_chain_veto_before_filling_holes_or_rewriting_plan_index_runtime(tmp_path, monkeypatch, state):
    _, config_path, config, layout = _config(tmp_path, count=2, multi_output=False)
    manifests = render_pipeline.generate_static_manifests(config_path)
    variant = render_pipeline._build_planned_variants(config, [manifests[-1]], layout["runtime_manifest_dir"])[0]
    task = render_pipeline._prepare_render_task(config, variant, layout["runtime_manifest_dir"], {})
    chain = open_chain(task["runtime_manifest_path"], layout["render_attempts_dir"].parent / "process_chains",
                       MatlabProcessRetryConfig(max_attempts=1))
    attempt = chain.reserve_attempt(task["runtime_manifest_path"], layout["render_attempts_dir"])
    if state == "exhausted":
        process = _process(task["runtime_manifest_path"], {"attempt": attempt}, code=1)
        acceptance = evaluate_render(process, task["variant_paths"], metadata_required=True, canonical=True)
        persist_acceptance(acceptance, lambda: None)
        assert chain.resolve_attempt(acceptance) == "exhausted"
    manifests[0].unlink()  # An earlier hole must remain absent on a later veto.
    layout["render_index_path"].write_text("", encoding="utf-8")
    before = _run_snapshot(layout["run_root"])
    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab",
                        lambda *a, **kw: pytest.fail("Blocked chain invoked MATLAB"))
    with pytest.raises((RetryBlockedError, render_pipeline.RenderStaticRunError)):
        render_pipeline.render_static_scenes(config_path)
    assert _run_snapshot(layout["run_root"]) == before
    assert not manifests[0].exists()


def test_resolution_failure_stops_dispatch_but_drains_started_worker(tmp_path, monkeypatch):
    _, config_path, _, layout = _config(tmp_path, workers=2)
    late_started = Event()
    resolution_failed = Event()
    started = []

    def render(path, *args, **kwargs):
        output = json.loads(path.read_text())["render"]["active_output_type"]
        started.append(output)
        if output == "bte_front_hartf":
            assert late_started.wait(10), "Second worker was not started"
        elif output == "bte_rear_hartf":
            late_started.set()
            assert resolution_failed.wait(10), "First worker resolution was not attempted"
        return _process(path, kwargs)

    resolve = render_pipeline._RenderCoordinator.resolve

    def fail_one_resolution(self, result, **kwargs):
        if result["task"]["variant_paths"]["output_type"] == "bte_front_hartf":
            resolution_failed.set()
            raise OSError("injected coordinator/index resolution failure")
        return resolve(self, result, **kwargs)

    monkeypatch.setattr(render_pipeline, "render_manifest_with_matlab", render)
    monkeypatch.setattr(render_pipeline._RenderCoordinator, "resolve", fail_one_resolution)
    with pytest.raises(render_pipeline.RenderStaticRunError, match="injected coordinator/index resolution failure"):
        render_pipeline.render_static_scenes(config_path)
    assert set(started) == {"binaural_hrtf", "bte_front_hartf", "bte_rear_hartf"}
    outcomes = [json.loads(path.read_text()) for path in layout["render_attempts_dir"].glob("*/outcome.json")]
    late = [outcome for outcome in outcomes if outcome["output_type"] == "bte_rear_hartf"]
    assert len(late) == 1
    assert late[0]["termination_observed"] is True
    assert late[0]["commit_status"] == "committed"
    assert late[0]["decision"] == "continue"
    assert late[0]["retry_classification"] == "accepted"
