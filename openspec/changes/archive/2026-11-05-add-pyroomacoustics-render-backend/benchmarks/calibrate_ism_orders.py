"""Task 7.7 calibration benchmark for the Pyroomacoustics ISM orders.

Runs the real static pipeline (sampler, Sabine stratification, treatments,
canonical T30 gate with resampling) and the real worker processes on:

- the ``circular_array_only`` room ranges (shoebox), and
- an L-shape range taken from ``static_example`` (extruded polygon),

for several ``max_order`` / ``max_order_non_shoebox`` values, and reports per
configuration: worker wall time per attempt, T30 valid-band rate, canonical
rejection rate and scene exhaustion.

Usage (repository root, extra installed)::

    uv run --package acoustic-orchestrator --extra dev --extra pyroomacoustics \
        python openspec/changes/add-pyroomacoustics-render-backend/benchmarks/calibrate_ism_orders.py \
        --scenes 6 --workers 8 --out /tmp/ism_calibration.json

Synthetic material spectra stand in for the licensed RAVEN material database,
which is not part of the repository. They are typical untreated values; the
existing treatment catalog adds absorption to reach the configured RT targets.
"""

import argparse
import json
import math
from pathlib import Path
import shutil
import statistics
import struct
import tempfile
import time
import wave

import yaml

REPOSITORY = Path(__file__).resolve().parents[4]
THIRD_OCTAVES = [20 * 2 ** (i / 3) for i in range(31)]
# Untreated octave-band absorption, interpolated in log-frequency to 31 bands.
OCTAVE_SPECTRA = {
    "bricks": {125: 0.03, 250: 0.03, 500: 0.03, 1000: 0.04, 2000: 0.05, 4000: 0.07, 8000: 0.07},
    "wood": {125: 0.15, 250: 0.11, 500: 0.10, 1000: 0.07, 2000: 0.06, 4000: 0.07, 8000: 0.07},
    "plaster": {125: 0.013, 250: 0.015, 500: 0.02, 1000: 0.03, 2000: 0.04, 4000: 0.05, 8000: 0.05},
}
L_SHAPE = {"type": "l_shape", "probability": 1.0, "outer_length_m": {"min": 5.0, "max": 6.0},
           "outer_width_m": {"min": 4.0, "max": 5.0}, "cutout_length_m": {"min": 1.0, "max": 2.0},
           "cutout_width_m": {"min": 1.0, "max": 2.0}, "removed_corners": ["north_east"]}


def third_octave(spectrum: dict[int, float]) -> list[float]:
    keys = sorted(spectrum)
    values = []
    for frequency in THIRD_OCTAVES:
        if frequency <= keys[0]:
            values.append(spectrum[keys[0]])
        elif frequency >= keys[-1]:
            values.append(spectrum[keys[-1]])
        else:
            low = max(k for k in keys if k <= frequency)
            high = min(k for k in keys if k >= frequency)
            weight = 0 if high == low else math.log2(frequency / low) / math.log2(high / low)
            values.append(spectrum[low] + weight * (spectrum[high] - spectrum[low]))
    return values


def write_wav(path: Path, seconds: float, frequency: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * 44100)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        audio.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * frequency * n / 44100)))
                                   for n in range(frames)))


def workspace(root: Path, geometry: str, order: int, scenes: int, workers: int, attempts: int) -> Path:
    for number in range(2):
        write_wav(root / "assets" / "events" / "train" / "speech" / f"speech_{number:02d}.wav", 1.0, 300.0 * (number + 1))
    for material, spectrum in OCTAVE_SPECTRA.items():
        values = ", ".join(f"{value:.4f}" for value in third_octave(spectrum))
        target = root / "assets" / "materials" / material / f"{material}.mat"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(["[Material]", f"name= {material}", "notes= synthetic benchmark",
                                     f"absorp= {values}", "scatter= " + ", ".join(["0.1"] * 31),
                                     "interpol= " + ", ".join(["1"] * 31)]), encoding="utf-8")
    (root / "configs" / "hearing").mkdir(parents=True)
    shutil.copy(REPOSITORY / "configs" / "hearing" / "example_profile.yaml", root / "configs" / "hearing")
    raw = yaml.safe_load((REPOSITORY / "configs" / "experiments" / "circular_array_only.yml").read_text(encoding="utf-8"))
    raw.pop("raven")
    raw["render"] = {"backend": "pyroomacoustics", "sample_rate_hz": 44100}
    raw["pyroomacoustics"] = {"max_order": order, "max_order_non_shoebox": order, "air_absorption": True}
    raw["execution"].update(num_simulations=scenes, num_workers=workers)
    raw["scene_validation"]["max_scene_attempts"] = attempts
    raw["outputs"]["run_name"] = f"bench_{geometry}_{order}"
    raw["receiver_outputs"]["circular_array"]["num_microphones"] = 3
    if geometry == "l_shape":
        raw["room_sampling"]["geometry"]["shape_mix"] = [L_SHAPE]
    path = root / "configs" / "experiments" / "bench.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def measure(geometry: str, order: int, scenes: int, workers: int, attempts: int) -> dict:
    from acoustic_orchestrator.config.loader import load_config
    from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
    from acoustic_orchestrator.pipeline.render_pipeline import RenderStaticRunError, render_static_scenes

    with tempfile.TemporaryDirectory() as directory:
        config_path = workspace(Path(directory), geometry, order, scenes, workers, attempts)
        config = load_config(config_path)
        layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
        started = time.perf_counter()
        error = None
        try:
            _, summary = render_static_scenes(config_path)
        except RenderStaticRunError as exc:
            summary, error = exc.summary, repr(exc.__cause__)
        elapsed = time.perf_counter() - started
        outcomes = [json.loads(p.read_text()) for p in layout["render_attempts_dir"].rglob("outcome.json")]
        durations = [o["duration_s"] for o in outcomes if isinstance(o.get("duration_s"), (int, float))]
        rejections, invalid_bands = 0, 0
        for path in layout["scene_manifest_dir"].glob("*.json"):
            for rejection in json.loads(path.read_text())["reverberation_sampling"].get("raven_t30_rejections", []):
                rejections += 1
                invalid_bands += len(rejection.get("invalid_bands", []))
        failures = [json.loads(line) for line in layout["sampling_failures_path"].read_text().splitlines()
                    if line.strip()] if layout["sampling_failures_path"].exists() else []
        for failure in failures:
            for rejection in failure.get("raven_t30_rejections", []):
                rejections += 1
                invalid_bands += len(rejection.get("invalid_bands", []))
        t30_exhausted = sum(1 for failure in failures if failure.get("stage") == "raven_t30")
        accepted = summary["completed_variants"]
        canonical_attempts = accepted + rejections
        t30_accepted = {}
        for metadata in (layout["render_outputs_root"]).rglob("*__render.json"):
            payload = json.loads(metadata.read_text())
            t30_accepted[payload["scene_id"]] = round(payload["summary"]["room"]["reverberation"]["mean_t30_s"], 3)
        return {
            "geometry": geometry, "max_order": order, "scenes_requested": scenes,
            "scenes_accepted": accepted, "scenes_t30_exhausted": t30_exhausted,
            "scenes_planning_failures": len(failures) - t30_exhausted,
            "canonical_attempts": canonical_attempts, "t30_rejections": rejections,
            "canonical_rejection_rate": rejections / canonical_attempts if canonical_attempts else None,
            "t30_valid_band_rate": 1 - invalid_bands / (10 * canonical_attempts) if canonical_attempts else None,
            "worker_attempts": len(outcomes),
            "worker_wall_time_s": {"median": statistics.median(durations) if durations else None,
                                   "max": max(durations) if durations else None},
            "accepted_mean_t30_s_by_scene": dict(sorted(t30_accepted.items())),
            "batch_elapsed_s": round(elapsed, 1), "error": error,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenes", type=int, default=6)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--attempts", type=int, default=50, help="scene_validation.max_scene_attempts")
    parser.add_argument("--shoebox-orders", type=int, nargs="+", default=[30, 50, 80, 100])
    parser.add_argument("--l-shape-orders", type=int, nargs="+", default=[6, 8])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for geometry, orders in (("shoebox", args.shoebox_orders), ("l_shape", args.l_shape_orders)):
        for order in orders:
            result = measure(geometry, order, args.scenes, args.workers, args.attempts)
            print(json.dumps(result), flush=True)
            results.append(result)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
