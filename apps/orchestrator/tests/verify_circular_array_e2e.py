"""Opt-in acceptance checks against installed MATLAB/RAVEN and Clarity.

Run from the repository root with a fresh artifact directory, for example:
uv run --project apps/orchestrator python apps/orchestrator/tests/verify_circular_array_e2e.py render --output-root <directory>
Use the same directory with the clarity phase after render succeeds.
"""

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import wave

import yaml

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.models import ReceiverOutputConfig
from acoustic_orchestrator.config.validator import validate_config
from acoustic_orchestrator.pipeline.output_index import load_variant_index, load_clarity_index
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes


REPOSITORY = Path(__file__).resolve().parents[3]


def _save(config, path: Path) -> None:
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")


def _verify_outputs(config, manifests: list[Path], expected: int) -> dict:
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    records = load_variant_index(layout["render_index_path"])
    assert len(manifests) == 1 and len(records) == expected
    scene = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert len(scene["sources"]) == 2
    source_positions = [source["position_m"] for source in scene["sources"]]
    center = scene["receiver"]["position_m"]
    assert not math.isclose(math.dist(source_positions[0], center), math.dist(source_positions[1], center), abs_tol=1e-4)
    expected_array = scene["receiver"]["circular_array"]
    for record in records.values():
        assert record["status"] == "completed" and record["validation"]["ok"], record
        is_array = record["output_type"] == "circular_array"
        with wave.open(record["rendered_wav_path"], "rb") as audio:
            assert audio.getnchannels() == (8 if is_array else 2)
            assert audio.getframerate() == 44100
            assert audio.getnframes() == 2 * 44100
            if is_array:
                samples = audio.readframes(audio.getnframes())
                stride = audio.getnchannels() * audio.getsampwidth()
                assert any(samples[0::stride] != samples[k * audio.getsampwidth()::stride] for k in range(1, 8))
        metadata = json.loads(Path(record["render_metadata_path"]).read_text(encoding="utf-8"))
        assert metadata["output_type"] == record["output_type"]
        t30 = metadata["summary"]["room"]["reverberation"]["t30_s"]
        assert len(t30) == 10 and all(math.isfinite(value) and value > 0 for value in t30)
        for source, observed in zip(scene["sources"], metadata["summary"]["sources"], strict=True):
            expected_position = [source["position_m"][0], source["position_m"][1], -source["position_m"][2]]
            assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(expected_position, observed["position_m"], strict=True))
        if is_array:
            assert record["receiver_ir_path"] is None
            assert "hrtf_id" not in metadata and "hrtf_path" not in metadata
            assert metadata["receiver"]["coordinate_frame"] == "public_xyz_y_up"
            actual_array = metadata["receiver"]["circular_array"]
            assert actual_array["num_microphones"] == 8
            for actual, planned in zip(actual_array["microphones"], expected_array["microphones"], strict=True):
                assert actual["microphone_index"] == planned["microphone_index"]
                assert actual["channel_index"] == planned["channel_index"]
                assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(actual["position_m"], planned["position_m"], strict=True))
        else:
            selected = next(item for item in scene["receiver"]["hrtfs"] if item["hrtf_id"] == record["output_type"])
            assert Path(metadata["hrtf_path"]) == Path(selected["hrtf_path"])
            # The installed adapter writes projects in RavenInput. Check the
            # actual serialized selection, not merely the metadata's claim.
            rpf_path = config.raven.base_rpf_file.parent.parent / f"{metadata['project_name']}.rpf"
            assignments = [line.split("=", 1)[1].strip() for line in rpf_path.read_text().splitlines()
                           if line.strip().startswith("ProjectPath_HRTFDB =")]
            assert assignments and Path(assignments[0]) == Path(selected["hrtf_path"]), assignments
    return {"variants": sorted(record["output_type"] for record in records.values()), "index_path": str(layout["render_index_path"])}


def verify_render(root: Path) -> None:
    report = {}
    for case, expected in [("array_only", 1), ("mixed", 4)]:
        config = load_config(REPOSITORY / "configs/experiments" / (
            "circular_array_only.yml" if case == "array_only" else "circular_array_mixed.yml"
        ))
        config.outputs.artifact_root = root
        config.outputs.run_name = case
        config.hearing_degradation.output_dir = root / case / "output_audio" / "degraded"
        config.execution.overwrite_existing = True
        config.execution.resume_if_possible = True
        config.hearing_degradation.enabled = False
        config.hearing_degradation.input_targets = []
        distribution = config.room_sampling.reverberation.distribution
        distribution.range_s.min = 0.1
        distribution.range_s.max = 1.5
        distribution.bin_width_s = 1.4
        if case == "mixed":
            config.receiver_outputs.binaural_hrtf.file_pattern = "id01_HRTF.daff"
            for side in ["front", "rear"]:
                output = ReceiverOutputConfig(
                    enabled=True, ir_catalog_path=REPOSITORY / "assets/hartf" / side,
                    file_pattern=f"id01_HARTF_{side}.daff", output_subdir=f"bte_{side}_hartf",
                    num_channels=2, required=True,
                )
                setattr(config.receiver_outputs, f"bte_{side}_hartf", output)
        validate_config(config)
        path = root / f"{case}.yml"
        _save(config, path)
        manifests, first = render_static_scenes(path)
        assert first["render_jobs"] >= expected and first["completed_variants"] == expected, first
        report[case] = _verify_outputs(config, manifests, expected)
        _, resumed = render_static_scenes(path)
        assert resumed["render_jobs"] == 0 and resumed["resumed_variants"] == expected, resumed
        report[case].update(first_summary=first, resume_summary=resumed)
        print(f"PASS {case}: {expected} rendered and resumed variants", flush=True)
    (root / "render_verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def verify_clarity(root: Path) -> None:
    config = load_config(root / "mixed.yml")
    config.hearing_degradation.output_dir = root / "mixed" / "output_audio" / "degraded"
    config.hearing_degradation.enabled = True
    config.hearing_degradation.input_targets = ["binaural_hrtf"]
    config.hearing_degradation.runner.auto_submit = True
    validate_config(config)
    path = root / "mixed_clarity.yml"
    _save(config, path)
    _, summary = render_static_scenes(path)
    assert summary["render_jobs"] == 0 and summary["resumed_variants"] == 4, summary
    assert summary["clarity"]["completed_jobs"] == 1, summary
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    records = load_clarity_index(layout["clarity_index_path"])
    assert len(records) == 1
    for record in records.values():
        assert record["output_type"] == "binaural_hrtf" and record["status"] == "completed"
        output = Path(record["expected_output_wav_path"])
        assert output.is_file() and Path(record["expected_output_metadata_path"]).is_file()
        assert output.name == Path(record["input_wav_path"]).name
        with wave.open(str(output), "rb") as audio:
            assert audio.getnchannels() == 2 and audio.getframerate() == 44100
    rejected = []
    for target in ["circular_array", "bte_front_hartf", "bte_rear_hartf"]:
        invalid = deepcopy(config)
        invalid.hearing_degradation.input_targets = [target]
        invalid_path = root / f"invalid_{target}.yml"
        _save(invalid, invalid_path)
        try:
            render_static_scenes(invalid_path)
        except ValueError as exc:
            assert target in str(exc) and "no elegible" in str(exc), exc
            rejected.append(target)
        else:
            raise AssertionError(f"Ineligible target accepted: {target}")
    degraded = list(layout["degraded_output_root"].rglob("*.wav"))
    assert len(degraded) == 1 and "binaural_hrtf" in degraded[0].parts
    (root / "clarity_verification.json").write_text(
        json.dumps({"summary": summary, "rejected_targets": rejected, "degraded_wavs": [str(p) for p in degraded]}, indent=2), encoding="utf-8"
    )
    print("PASS Clarity: only HRTF degraded; array and both HARTF targets rejected", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["render", "clarity"])
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    if args.phase == "render":
        verify_render(args.output_root.resolve())
    else:
        verify_clarity(args.output_root.resolve())
