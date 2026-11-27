"""Worker records, sidecar and process contract (tasks 5.1–5.4)."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from pra_support import (
    make_runtime_manifest,
    require_backend_dependencies,
    require_pyroomacoustics,
    variant_paths_for,
    worker_command,
)

require_backend_dependencies()

import numpy as np  # noqa: E402

from acoustic_orchestrator.backends.pyroomacoustics import synthesis, t30  # noqa: E402
from acoustic_orchestrator.backends.pyroomacoustics.job import load_render_job  # noqa: E402
from acoustic_orchestrator.backends.pyroomacoustics.records import (  # noqa: E402
    EventWriter,
    RecordExistsError,
    write_completion,
    write_failure,
)
from acoustic_orchestrator.backends.pyroomacoustics.sidecar import build_sidecar  # noqa: E402
from acoustic_orchestrator.pipeline.matlab_diagnostics import MatlabDiagnostics  # noqa: E402
from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt, inspect_completion  # noqa: E402
from acoustic_orchestrator.pipeline.render_failure import inspect_failure  # noqa: E402
from acoustic_orchestrator.pipeline.render_validation import validate_render  # noqa: E402

FS = 44100


def _outputs(manifest: dict) -> tuple[Path, Path]:
    render = manifest["render"]
    wav, metadata = Path(render["output_wav_path"]), Path(render["output_metadata_path"])
    synthesis.write_wav(wav, np.full((100, 3), 0.1), FS)
    metadata.write_text("{}", encoding="utf-8")
    return wav, metadata


# --- 5.1 writers against the existing readers ---------------------------------------

def test_completion_accepted_by_render_attempt_reader(tmp_path: Path) -> None:
    path, manifest = make_runtime_manifest(tmp_path)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    wav, metadata = _outputs(manifest)
    write_completion(snapshot_path=attempt.snapshot_path, attempt_id=attempt.attempt_id,
                     variant_id=attempt.variant_id, manifest_sha256=attempt.manifest_sha256,
                     scene_id=manifest["scene_id"], output_type="circular_array",
                     completion_path=attempt.completion_path,
                     artifacts=[("wav", wav.as_posix()), ("metadata", metadata.as_posix())])
    assert inspect_completion(attempt.record) == {"status": "valid", "issues": []}
    assert not attempt.completion_path.with_name("completion.json.tmp").exists()


def test_pre_existing_completion_refused(tmp_path: Path) -> None:
    path, manifest = make_runtime_manifest(tmp_path)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    wav, metadata = _outputs(manifest)
    attempt.completion_path.write_text("existing", encoding="utf-8")
    with pytest.raises(RecordExistsError):
        write_completion(snapshot_path=attempt.snapshot_path, attempt_id=attempt.attempt_id,
                         variant_id=attempt.variant_id, manifest_sha256=attempt.manifest_sha256,
                         scene_id=manifest["scene_id"], output_type="circular_array",
                         completion_path=attempt.completion_path,
                         artifacts=[("wav", wav.as_posix()), ("metadata", metadata.as_posix())])
    assert attempt.completion_path.read_text(encoding="utf-8") == "existing"


def test_failure_report_accepted_by_failure_reader(tmp_path: Path) -> None:
    path, _ = make_runtime_manifest(tmp_path)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    write_failure(snapshot_path=attempt.snapshot_path, attempt_id=attempt.attempt_id, variant_id=attempt.variant_id,
                  manifest_sha256=attempt.manifest_sha256, failure_path=attempt.failure_path,
                  completion_path=attempt.completion_path, identifier="BinaScape:Pyroomacoustics:InvalidInput",
                  message="receiver.circular_array descriptor is missing")
    assert inspect_failure(attempt.record) == {"status": "valid", "issues": [], "category": "invalid_input"}


def test_failure_not_attributed_to_other_bytes(tmp_path: Path) -> None:
    path, _ = make_runtime_manifest(tmp_path)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    with pytest.raises(ValueError, match="hash"):
        write_failure(snapshot_path=attempt.snapshot_path, attempt_id=attempt.attempt_id,
                      variant_id=attempt.variant_id, manifest_sha256="0" * 64, failure_path=attempt.failure_path,
                      completion_path=attempt.completion_path, identifier="x", message="y")
    assert not attempt.failure_path.exists()


def test_events_parsed_by_matlab_diagnostics(tmp_path: Path) -> None:
    path, _ = make_runtime_manifest(tmp_path)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    diagnostics = MatlabDiagnostics(path, tmp_path / "diag", attempt_id=attempt.attempt_id,
                                    variant_id=attempt.variant_id, input_snapshot_path=attempt.snapshot_path)
    events = EventWriter(diagnostics.events_path, attempt.attempt_id)
    events.emit("stage_started", "initialization")
    events.emit("stage_completed", "initialization", output_type="circular_array")
    events.emit("stage_started", "simulation", output_type="circular_array")
    events.emit("step_started", "simulation", output_type="circular_array", step="pyroomacoustics_rir")
    events.emit("step_completed", "simulation", output_type="circular_array", step="pyroomacoustics_rir")
    events.emit("stage_completed", "simulation", output_type="circular_array")
    events.emit("entrypoint_completed", "")
    parsed = diagnostics.read_events()
    assert [event["sequence"] for event in parsed] == list(range(1, 8))
    assert diagnostics.record["diagnostic_errors"] == []
    diagnostics.collect_evidence()
    assert diagnostics.record["last_completed_step"] == "pyroomacoustics_rir"
    assert diagnostics.record["last_completed_stage"] == "simulation"
    assert diagnostics.record["entrypoint_completed"] is True


# --- 5.2 sidecar builder with the existing validator --------------------------------

def _decaying_rirs(job, t60: float = 0.4) -> list[list[np.ndarray]]:
    t = np.arange(int(0.8 * FS)) / FS
    tones = sum(np.cos(2 * np.pi * f * t) for f in t30.OCTAVE_CENTER_FREQUENCIES_HZ) * 10 ** (-3 * t / t60)
    return [[np.roll(tones, 20 * (s + m)) * 0.05 for m in range(job.num_channels)] for s in range(len(job.sources))]


@pytest.mark.parametrize("noise", [None, {"enabled": True, "layers": [
    {"noise_id": "noise_0001", "strategy": "colored", "type": "pink", "color": "pink", "snr_db": 20.0}]}])
def test_builder_sidecar_and_wav_pass_canonical_validation(tmp_path: Path, noise: dict | None) -> None:
    path, manifest = make_runtime_manifest(tmp_path, noise=noise)
    job = load_render_job(manifest)
    rirs = _decaying_rirs(job)
    reverberation, pairs = t30.measure_t30(rirs, FS)
    prepared, rendered, offsets = [], [], []
    for item, source_rirs in zip(job.sources, rirs, strict=True):
        audio, rate = synthesis.read_audio(Path(item.source["audio_path"]))
        prepared.append(synthesis.prepare_source(audio, rate, FS, item.source["gain_db"]))
        rendered.append(synthesis.render_source(prepared[-1].signal, source_rirs, trim_reverb_tail=False))
        offsets.append(synthesis.source_offset_samples(item.source["start_time_s"], FS))
    mix, _ = synthesis.mix_sources(rendered, offsets, job.num_channels, job.target_duration_s, FS)
    audio, noise_metadata = synthesis.apply_background_noise(mix, FS, manifest["background_noise"], job.seed)
    synthesis.write_wav(Path(manifest["render"]["output_wav_path"]), audio, FS)
    timing = {"speed_of_sound_m_s": 343.0, "global_delay_samples": 40, "frac_delay_length": 81,
              "high_pass_filter": {"enabled": True, "cutoff_hz": 10.0, "design": {}}}
    sidecar = build_sidecar(job=job, prepared_sources=prepared, num_samples=audio.shape[0],
                            background_noise=noise_metadata, reverberation=reverberation, t30_pairs=pairs,
                            timing=timing, rir_lengths=[[len(r) for r in row] for row in rirs], seed_applied=42,
                            package_version="0.10.1", effective_max_order=8)
    Path(manifest["render"]["output_metadata_path"]).write_text(json.dumps(sidecar, allow_nan=False), encoding="utf-8")
    result = validate_render(variant_paths_for(path, manifest), metadata_required=True, canonical=True)
    assert result.validity == "valid", result.as_record()
    assert sidecar["summary"]["render_backend"] == "Pyroomacoustics"
    backend = sidecar["backend"]
    assert backend["name"] == "pyroomacoustics" and backend["simulation"]["method"] == "ism"
    assert backend["simulation"]["ray_tracing"] is False and backend["simulation"]["randomized_image_sources"] is False
    assert backend["coordinate_transform"]["rotation_matrix"] == [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]
    assert backend["coordinate_transform"]["translation_m"] == [0.0, 3.5, 0.0]
    assert all(item["scattering_applied"] is False for item in backend["materials"].values())
    assert set(backend["rir"]) == {"global_delay_samples", "frac_delay_length", "high_pass_filter", "lengths_samples"}
    assert len(backend["t30_pairs_s"]) == 2 and len(backend["t30_pairs_s"][0]) == 3
    # Legacy source pose representation (z reflected for schema 3.0).
    source = sidecar["summary"]["sources"][0]
    assert source["position_m"] == [1.0, 1.2, -1.0] and source["up_vector"] == [0, 1, 0]
    assert sidecar["receiver"]["circular_array"] == manifest["receiver"]["circular_array"]


def test_backend_identity_mismatch_rejected(tmp_path: Path) -> None:
    test_builder_sidecar_and_wav_pass_canonical_validation(tmp_path / "base", None)
    base_path, base_manifest = make_runtime_manifest(tmp_path / "base")
    paths = variant_paths_for(base_path, base_manifest)
    metadata_path = Path(base_manifest["render"]["output_metadata_path"])
    sidecar = json.loads(metadata_path.read_text(encoding="utf-8"))
    for mutate in (lambda s: s["summary"].update(render_backend="RAVEN + ITA"), lambda s: s.pop("backend"),
                   lambda s: s["backend"].update(name="raven")):
        changed = json.loads(json.dumps(sidecar))
        mutate(changed)
        metadata_path.write_text(json.dumps(changed), encoding="utf-8")
        result = validate_render(paths, metadata_required=True, canonical=True)
        assert "render_metadata_backend_identity_mismatch" in result.invalid


# --- 5.3 worker process [pra] ----------------------------------------------------------

def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=300)


def test_worker_success_publishes_valid_completion_and_events(tmp_path: Path) -> None:
    require_pyroomacoustics()
    path, manifest = make_runtime_manifest(tmp_path)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    events_path = tmp_path / "events.jsonl"
    completed = _run(worker_command(attempt, events_path))
    assert completed.returncode == 0, completed.stderr
    assert inspect_completion(attempt.record)["status"] == "valid"
    assert not attempt.failure_path.exists()
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    sequence = [(e["type"], e["stage"], e.get("step"), e.get("kind")) for e in events]
    assert sequence == [
        ("stage_started", "initialization", None, None), ("stage_completed", "initialization", None, None),
        ("stage_started", "simulation", None, None),
        ("step_started", "simulation", "pyroomacoustics_rir", None),
        ("step_completed", "simulation", "pyroomacoustics_rir", None),
        ("step_started", "simulation", "audio_postprocessing", None),
        ("step_completed", "simulation", "audio_postprocessing", None),
        ("stage_completed", "simulation", None, None), ("stage_started", "artifact_writing", None, None),
        ("artifact_written", "artifact_writing", None, "wav"), ("artifact_written", "artifact_writing", None, "metadata"),
        ("stage_completed", "artifact_writing", None, None), ("entrypoint_completed", "", None, None)]
    result = validate_render(variant_paths_for(path, manifest), metadata_required=True, canonical=False)
    assert result.artifacts_ok, result.as_record()
    assert result.wav_info["num_channels"] == 3 and result.wav_info["num_samples"] == 22050
    sidecar = result.metadata
    assert sidecar["backend"]["version"] == "0.10.1" and sidecar["backend"]["simulation"]["seed_applied"] == 42


def test_worker_invalid_snapshot_publishes_failure(tmp_path: Path) -> None:
    require_pyroomacoustics()
    path, manifest = make_runtime_manifest(tmp_path)
    del manifest["receiver"]["circular_array"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    attempt = RenderAttempt(path, tmp_path / "attempts")
    completed = _run(worker_command(attempt))
    assert completed.returncode != 0
    assert not attempt.completion_path.exists()
    attempt.record["exit_code"] = completed.returncode
    report = inspect_failure(attempt.record)
    assert report == {"status": "valid", "issues": [], "category": "invalid_input"}
    assert "circular_array" in json.loads(attempt.failure_path.read_text(encoding="utf-8"))["error"]["message"]


@pytest.mark.parametrize("mutation, text", [
    (lambda m: m["room"]["geometry"].update(type="dome"), "unsupported room.geometry.type"),
    (lambda m: m["room"]["acoustic_surfaces"]["floor"]["effective_absorption"].__setitem__(17, 1.5), "outside [0, 1]"),
])
def test_worker_rejects_unknown_geometry_and_bad_coefficients(tmp_path: Path, mutation, text: str) -> None:
    require_pyroomacoustics()
    path, manifest = make_runtime_manifest(tmp_path)
    mutation(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    attempt = RenderAttempt(path, tmp_path / "attempts")
    assert _run(worker_command(attempt)).returncode != 0
    assert text in json.loads(attempt.failure_path.read_text(encoding="utf-8"))["error"]["message"]


def test_worker_injected_exception_exits_non_zero_without_completion(tmp_path: Path) -> None:
    require_pyroomacoustics()
    path, _ = make_runtime_manifest(tmp_path)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    command = worker_command(attempt)
    injected = ("import sys; import acoustic_orchestrator.backends.pyroomacoustics.simulation as s\n"
                "def boom(*a, **k): raise RuntimeError('injected RIR failure')\n"
                "s.compute_rirs = boom\n"
                "from acoustic_orchestrator.backends.pyroomacoustics.worker import main\n"
                "sys.exit(main(sys.argv[1:]))")
    completed = _run([sys.executable, "-c", injected, *command[3:]])
    assert completed.returncode != 0 and "injected RIR failure" in completed.stderr
    assert not attempt.completion_path.exists() and not attempt.failure_path.exists()


def test_worker_renders_from_snapshot_only(tmp_path: Path) -> None:
    require_pyroomacoustics()
    path, manifest = make_runtime_manifest(tmp_path)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    changed = json.loads(json.dumps(manifest))
    changed["render"]["backend_parameters"]["max_order"] = 1
    changed["receiver"].pop("circular_array")
    path.write_text(json.dumps(changed), encoding="utf-8")
    assert _run(worker_command(attempt)).returncode == 0
    receipt = json.loads(attempt.completion_path.read_text(encoding="utf-8"))
    assert receipt["manifest_sha256"] == hashlib.sha256(attempt.snapshot_path.read_bytes()).hexdigest()
    sidecar = json.loads(Path(manifest["render"]["output_metadata_path"]).read_text(encoding="utf-8"))
    assert sidecar["backend"]["simulation"]["max_order"] == 8


def test_worker_refuses_existing_completion(tmp_path: Path) -> None:
    require_pyroomacoustics()
    path, _ = make_runtime_manifest(tmp_path)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    attempt.completion_path.write_text("other", encoding="utf-8")
    assert _run(worker_command(attempt)).returncode != 0
    assert attempt.completion_path.read_text(encoding="utf-8") == "other"


# --- 5.4 determinism [pra] -------------------------------------------------------------

def test_two_worker_processes_are_byte_identical(tmp_path: Path) -> None:
    require_pyroomacoustics()
    noise = {"enabled": True, "layers": [{"strategy": "colored", "color": "pink", "snr_db": 15.0}]}
    path, manifest = make_runtime_manifest(tmp_path, noise=noise, air_absorption=True)
    wav = Path(manifest["render"]["output_wav_path"])
    metadata = Path(manifest["render"]["output_metadata_path"])
    observed = []
    for run in range(2):
        attempt = RenderAttempt(path, tmp_path / f"attempts_{run}")
        assert _run(worker_command(attempt)).returncode == 0
        observed.append((hashlib.sha256(wav.read_bytes()).hexdigest(), json.loads(metadata.read_text(encoding="utf-8"))))
    assert observed[0][0] == observed[1][0]
    assert observed[0][1] == observed[1][1]


# --- scenario coverage additions ------------------------------------------------------------

def test_eight_channel_worker_output_with_gain_offset_duration(tmp_path: Path) -> None:
    """circular-microphone-array "Eight-channel output" through the real worker."""
    require_pyroomacoustics()
    import soundfile

    path, manifest = make_runtime_manifest(tmp_path, num_microphones=8, target_duration_s=0.75)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    assert _run(worker_command(attempt)).returncode == 0
    info = soundfile.info(manifest["render"]["output_wav_path"])
    assert (info.channels, info.samplerate, info.subtype, info.frames) == (8, 44100, "PCM_16", 33075)
    result = validate_render(variant_paths_for(path, manifest), metadata_required=True)
    assert result.artifacts_ok, result.as_record()
    sources = result.metadata["summary"]["sources"]
    assert [s["gain_db"] for s in sources] == [-3.0, 0.0] and [s["source_start_time_s"] for s in sources] == [0.0, 0.05]


def test_metadata_export_failure_publishes_no_completion(tmp_path: Path) -> None:
    """static-render-acceptance "Fallo exportando metadatos" for the worker."""
    require_pyroomacoustics()
    path, manifest = make_runtime_manifest(tmp_path)
    attempt = RenderAttempt(path, tmp_path / "attempts")
    injected = ("import sys; import acoustic_orchestrator.backends.pyroomacoustics.sidecar as s\n"
                "def boom(**k): raise OSError('metadata export failed')\n"
                "s.build_sidecar = boom\n"
                "from acoustic_orchestrator.backends.pyroomacoustics.worker import main\n"
                "sys.exit(main(sys.argv[1:]))")
    completed = _run([sys.executable, "-c", injected, *worker_command(attempt)[3:]])
    assert completed.returncode != 0 and "metadata export failed" in completed.stderr
    assert Path(manifest["render"]["output_wav_path"]).is_file()
    assert not attempt.completion_path.exists() and not attempt.failure_path.exists()
    assert inspect_completion(attempt.record)["status"] == "missing"


def test_raven_only_code_paths_never_import_pyroomacoustics() -> None:
    """pyroomacoustics-render-backend "RAVEN-only installation": no import is attempted."""
    code = ("import sys\n"
            "import acoustic_orchestrator.cli, acoustic_orchestrator.pipeline.render_pipeline\n"
            "import acoustic_orchestrator.config.validator, acoustic_orchestrator.pipeline.render_validation\n"
            "assert 'pyroomacoustics' not in sys.modules, 'pyroomacoustics imported'\n"
            "assert not any(m.startswith('acoustic_orchestrator.backends') for m in sys.modules)\n"
            "print('ok')")
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0 and completed.stdout.strip() == "ok", completed.stderr
