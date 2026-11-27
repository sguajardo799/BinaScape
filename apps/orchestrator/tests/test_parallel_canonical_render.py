"""Canonical renders of different scenes run concurrently, bounded by num_workers."""

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes
from test_generate_manifests import _build_workspace, _write_config, _write_valid_raven_metadata
from test_render_diagnostics_integration import process_manifest, successful_process

RUNNER = "acoustic_orchestrator.pipeline.matlab_runner.subprocess.run"


def configure(tmp_path, *, scenes, workers, optional=False):
    workspace = _build_workspace(tmp_path)
    path = _write_config(workspace, "parallel_canonical", num_simulations=scenes, num_workers=workers,
                         include_optional_hartf_outputs=optional, resume_if_possible=True,
                         overwrite_existing=False, total_duration_s=0.01, save_render_metadata=True)
    return path, resolve_artifact_layout(load_config(path).outputs, "sim_test")


class ConcurrencyProbe:
    def __init__(self):
        self.lock = Lock()
        self.running = 0
        self.peak = 0
        self.calls = []

    def enter(self, manifest):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.calls.append((manifest["scene_id"], manifest["render"]["active_output_type"]))
            return len(self.calls) - 1

    def leave(self):
        with self.lock:
            self.running -= 1


def published_state(layout):
    root = layout["run_root"]
    manifests = {p.name: p.read_bytes() for p in layout["scene_manifest_dir"].glob("*.json")}
    index = load_variant_index(layout["render_index_path"])
    statuses = {variant_id: record["status"] for variant_id, record in index.items()}
    # Archived evidence lives under random attempt/archive ids; compare the layout only.
    wavs = sorted(re.sub(r"/[0-9a-f]{32}/", "/<id>/", p.relative_to(root).as_posix()) for p in root.rglob("*.wav"))
    return manifests, statuses, wavs


def test_canonical_renders_of_different_scenes_overlap(tmp_path, monkeypatch):
    path, layout = configure(tmp_path, scenes=3, workers=3)
    probe = ConcurrencyProbe()
    all_started = Barrier(3, timeout=10)

    def run(command, **kwargs):
        manifest = process_manifest(command)
        probe.enter(manifest)
        try:
            all_started.wait()  # Only passes if three canonical renders run at once.
            return successful_process(command, **kwargs)
        finally:
            probe.leave()

    monkeypatch.setattr(RUNNER, run)
    manifest_paths, summary = render_static_scenes(path)
    assert probe.peak == 3
    assert [p.stem for p in manifest_paths] == ["scene_static_0001", "scene_static_0002", "scene_static_0003"]
    assert summary["render_jobs"] == 3


def test_canonical_concurrency_is_bounded_by_num_workers(tmp_path, monkeypatch):
    path, layout = configure(tmp_path, scenes=5, workers=2)
    probe = ConcurrencyProbe()

    def run(command, **kwargs):
        probe.enter(process_manifest(command))
        try:
            return successful_process(command, **kwargs)
        finally:
            probe.leave()

    monkeypatch.setattr(RUNNER, run)
    manifest_paths, _ = render_static_scenes(path)
    assert probe.peak <= 2
    assert len(manifest_paths) == 5


def test_parallel_canonical_render_matches_sequential(tmp_path, monkeypatch):
    def run(command, **kwargs):
        manifest = process_manifest(command)
        if manifest["scene_id"] == "scene_static_0002" and manifest["render"]["active_output_type"] == "binaural_hrtf":
            wav = manifest["render"]["output_wav_path"]
            # First canonical attempt of scene 2 reports an invalid T30 band count.
            if wav not in rejected:
                rejected.append(wav)
                Path(wav).parent.mkdir(parents=True, exist_ok=True)
                Path(wav).write_bytes(b"simulated render")
                _write_valid_raven_metadata(manifest, [0.5] * 9)
                return SimpleNamespace(returncode=0)
        return successful_process(command, **kwargs)

    path, layout = configure(tmp_path, scenes=4, workers=1, optional=True)
    monkeypatch.setattr(RUNNER, run)
    rejected = []
    sequential_paths, sequential_summary = render_static_scenes(path)
    sequential = published_state(layout)
    assert len(rejected) == 1
    shutil.rmtree(layout["run_root"])

    raw = path.read_text(encoding="utf-8").replace("num_workers: 1", "num_workers: 3")
    path.write_text(raw, encoding="utf-8")
    rejected = []
    parallel_paths, parallel_summary = render_static_scenes(path)
    assert len(rejected) == 1
    assert parallel_paths == sequential_paths
    assert published_state(layout) == sequential
    assert parallel_summary["render_jobs"] == sequential_summary["render_jobs"]


def test_canonical_failure_drains_inflight_and_dispatches_nothing_new(tmp_path, monkeypatch):
    path, layout = configure(tmp_path, scenes=5, workers=2)
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace("execution:\n", "execution:\n  matlab_process_retry: {max_attempts: 1, initial_delay_s: 0, max_delay_s: 0, max_consecutive_failures: 5}\n", 1),
                    encoding="utf-8")
    both_started = Barrier(2, timeout=10)
    probe = ConcurrencyProbe()

    def run(command, **kwargs):
        manifest = process_manifest(command)
        ordinal = probe.enter(manifest)
        try:
            if ordinal < 2:
                both_started.wait()
            if manifest["scene_id"] == "scene_static_0001":
                raise subprocess.CalledProcessError(29, command)
            return successful_process(command, **kwargs)
        finally:
            probe.leave()

    monkeypatch.setattr(RUNNER, run)
    with pytest.raises(RenderStaticRunError):
        render_static_scenes(path)
    assert sorted(scene for scene, _ in probe.calls) == ["scene_static_0001", "scene_static_0002"]
    index = load_variant_index(layout["render_index_path"])
    statuses = Counter(record["status"] for record in index.values())
    assert statuses["completed"] == 1  # The in-flight scene 2 was resolved and indexed.
    assert statuses["planned"] == 3  # Scenes 3-5 were never dispatched.


def test_bookkeeping_error_still_resolves_inflight_canonicals(tmp_path, monkeypatch):
    import time

    from acoustic_orchestrator.pipeline import render_pipeline
    from acoustic_orchestrator.pipeline.render_attempt import read_json_object

    path, layout = configure(tmp_path, scenes=3, workers=3, optional=True)

    def archive_fails(*args, **kwargs):
        raise PermissionError(13, "file in use")

    def run(command, **kwargs):
        manifest = process_manifest(command)
        if manifest["scene_id"] == "scene_static_0003":
            time.sleep(0.1)  # Rejected first, while scenes 1 and 2 are still rendering.
            Path(manifest["render"]["output_wav_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(manifest["render"]["output_wav_path"]).write_bytes(b"simulated render")
            _write_valid_raven_metadata(manifest, [0.5] * 9)
            return SimpleNamespace(returncode=0)
        time.sleep(1.0)
        return successful_process(command, **kwargs)

    monkeypatch.setattr(render_pipeline, "_archive_prepared_source_paths", archive_fails)
    monkeypatch.setattr(RUNNER, run)
    with pytest.raises(RenderStaticRunError) as raised:
        render_static_scenes(path)
    assert isinstance(raised.value.__cause__, PermissionError)
    states = [read_json_object(p)["state"]
              for p in (layout["render_attempts_dir"].parent / "process_chains").glob("*/chain.json")]
    assert "running" not in states
    index = load_variant_index(layout["render_index_path"])
    assert index["scene_static_0001__binaural_hrtf"]["status"] == "completed"
    assert index["scene_static_0002__binaural_hrtf"]["status"] == "completed"
