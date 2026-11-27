"""Pyroomacoustics render worker: one attempt, one process.

Usage::

    python -m acoustic_orchestrator.backends.pyroomacoustics.worker <snapshot>
        --attempt-id ID --variant-id ID --manifest-sha256 HEX
        --completion-path PATH --failure-path PATH [--events-path PATH]

Mirrors the MATLAB entrypoint contract (``run_raven_static_render.m``): reads
only the attempt snapshot; publishes ``failure.json`` (``invalid_input``) when
the snapshot fails to load or validate; emits the same diagnostic stages;
writes the WAV, then the sidecar, then the completion record; exits 0 only
after publication. Any other error exits non-zero without a completion.
"""

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys

from acoustic_orchestrator.backends.pyroomacoustics.errors import InvalidRenderInput
from acoustic_orchestrator.backends.pyroomacoustics.records import (
    EventWriter,
    sha256_bytes,
    write_completion,
    write_failure,
)

STEP_RIR = "pyroomacoustics_rir"
STEP_AUDIO = "audio_postprocessing"
OUTPUT_TYPE = "circular_array"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="acoustic_orchestrator.backends.pyroomacoustics.worker")
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--variant-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--completion-path", type=Path, required=True)
    parser.add_argument("--failure-path", type=Path, required=True)
    parser.add_argument("--events-path", type=Path, default=None)
    return parser.parse_args(argv)


def load_job(args: argparse.Namespace):
    """Read and validate the snapshot. ``InvalidRenderInput`` means ``invalid_input``."""
    from acoustic_orchestrator.backends.pyroomacoustics.job import load_render_job, parse_snapshot

    data = args.snapshot.read_bytes()
    if sha256_bytes(data) != args.manifest_sha256:
        # Bytes that were not the attempt's input are never attributed.
        raise RuntimeError("snapshot hash does not match --manifest-sha256")
    return load_render_job(parse_snapshot(data))


def simulate(job, events: EventWriter) -> dict:
    """RIRs, T30, synthesis and noise. Returns everything needed to publish."""
    from acoustic_orchestrator.backends.pyroomacoustics import simulation, synthesis, t30

    details = {"output_type": OUTPUT_TYPE}
    events.emit("step_started", "simulation", **details, step=STEP_RIR)
    seed_applied = simulation.seed_package(job.seed)
    parameters = simulation.IsmParameters.from_manifest(job.parameters)
    room = simulation.build_room(job.room_description, job.materials, parameters, job.fs)
    rirs = simulation.compute_rirs(room, [item.position_backend_m for item in job.sources], job.microphones_backend_m)
    timing = simulation.runtime_timing(room)
    reverberation, t30_pairs = t30.measure_t30(rirs, job.fs)
    events.emit("step_completed", "simulation", **details, step=STEP_RIR)

    events.emit("step_started", "simulation", **details, step=STEP_AUDIO)
    prepared, rendered, offsets = [], [], []
    for item, source_rirs in zip(job.sources, rirs, strict=True):
        audio, source_fs = synthesis.read_audio(Path(item.source["audio_path"]))
        gain = item.source.get("gain_db")
        source = synthesis.prepare_source(audio, source_fs, job.fs, 0.0 if gain in (None, []) else gain)
        prepared.append(source)
        rendered.append(synthesis.render_source(source.signal, source_rirs, trim_reverb_tail=job.trim_reverb_tail))
        start = item.source.get("start_time_s")
        offsets.append(synthesis.source_offset_samples(0.0 if start in (None, []) else start, job.fs))
    mix, _ = synthesis.mix_sources(rendered, offsets, job.num_channels, job.target_duration_s, job.fs)
    audio, noise = synthesis.apply_background_noise(mix, job.fs, job.manifest.get("background_noise"), job.seed)
    events.emit("step_completed", "simulation", **details, step=STEP_AUDIO)
    return {"audio": audio, "noise": noise, "prepared": prepared, "reverberation": reverberation,
            "t30_pairs": t30_pairs, "timing": timing, "seed_applied": seed_applied,
            "rir_lengths": [[len(rir) for rir in row] for row in rirs],
            "effective_max_order": simulation.effective_max_order(job.room_description, parameters)}


def publish(job, result: dict, args: argparse.Namespace, events: EventWriter) -> None:
    from acoustic_orchestrator.backends.pyroomacoustics.sidecar import build_sidecar
    from acoustic_orchestrator.backends.pyroomacoustics.synthesis import write_wav

    render = job.manifest["render"]
    wav_path, metadata_path = Path(render["output_wav_path"]), Path(render["output_metadata_path"])
    events.emit("stage_started", "artifact_writing")
    write_wav(wav_path, result["audio"], job.fs)
    events.emit("artifact_written", "artifact_writing", output_type=OUTPUT_TYPE, path=wav_path.as_posix(), kind="wav")
    sidecar = build_sidecar(
        job=job, prepared_sources=result["prepared"], num_samples=int(result["audio"].shape[0]),
        background_noise=result["noise"], reverberation=result["reverberation"], t30_pairs=result["t30_pairs"],
        timing=result["timing"], rir_lengths=result["rir_lengths"], seed_applied=result["seed_applied"],
        package_version=importlib.metadata.version("pyroomacoustics"),
        effective_max_order=result["effective_max_order"],
    )
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                             encoding="utf-8")
    events.emit("artifact_written", "artifact_writing", output_type=OUTPUT_TYPE, path=metadata_path.as_posix(),
                kind="metadata")
    write_completion(
        snapshot_path=args.snapshot, attempt_id=args.attempt_id, variant_id=args.variant_id,
        manifest_sha256=args.manifest_sha256, scene_id=job.manifest["scene_id"], output_type=OUTPUT_TYPE,
        completion_path=args.completion_path, artifacts=[("wav", wav_path.as_posix()), ("metadata", metadata_path.as_posix())],
    )
    events.emit("stage_completed", "artifact_writing")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    events = EventWriter(args.events_path, args.attempt_id)
    print("[INFO] Pyroomacoustics static render started", flush=True)
    print(f"[INFO] Config: {args.snapshot}", flush=True)
    try:
        events.emit("stage_started", "initialization")
        try:
            job = load_job(args)
        except InvalidRenderInput as input_error:
            try:
                write_failure(snapshot_path=args.snapshot, attempt_id=args.attempt_id, variant_id=args.variant_id,
                              manifest_sha256=args.manifest_sha256, failure_path=args.failure_path,
                              completion_path=args.completion_path,
                              identifier="BinaScape:Pyroomacoustics:InvalidInput", message=str(input_error))
            except Exception as report_error:
                print(f"[WARN] No se pudo publicar el informe funcional: {report_error}", file=sys.stderr)
            raise
        print("[INFO] Config loaded", flush=True)
        details = {"output_type": OUTPUT_TYPE}
        events.emit("stage_completed", "initialization", **details)
        events.emit("stage_started", "simulation", **details)
        result = simulate(job, events)
        events.emit("stage_completed", "simulation", **details)
        publish(job, result, args, events)
        print("[INFO] Static render completed successfully", flush=True)
        events.emit("entrypoint_completed", "")
        return 0
    except Exception as error:
        events.emit("error", "", error={"identifier": type(error).__name__, "message": str(error)})
        print(f"[ERROR] Static render failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
