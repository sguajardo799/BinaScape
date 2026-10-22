"""Opt-in physical Windows/MATLAB/ITA/RAVEN acceptance smoke in a new directory.

uv run --project apps/orchestrator python apps/orchestrator/tests/verify_render_acceptance.py --output-root <new-directory>

Only the harness appends an error after the entrypoint returns. Production MATLAB
code and acoustic artifacts are never modified to induce a failure.
"""

import argparse
import json
from pathlib import Path
import subprocess
from unittest.mock import patch
from uuid import uuid4

import yaml

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.validator import validate_config
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.render_attempt import inspect_completion, read_json_object
from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes


REPOSITORY = Path(__file__).resolve().parents[3]


def prepare_config(root: Path, phase: str) -> Path:
    config = load_config(REPOSITORY / "configs/experiments/circular_array_only.yml")
    config.experiment.experiment_id = f"acceptance_{phase}_{uuid4().hex[:12]}"
    config.outputs.artifact_root = root
    config.outputs.run_name = phase
    config.execution.num_simulations = 1
    config.execution.num_workers = 1
    config.execution.resume_if_possible = False
    config.execution.save_render_metadata = True
    config.hearing_degradation.enabled = False
    config.hearing_degradation.input_targets = []
    # Bounded smoke configuration, without weakening the T30 acceptance gate.
    config.scene_validation.max_scene_attempts = 5
    distribution = config.room_sampling.reverberation.distribution
    distribution.range_s.min = 0.1
    distribution.range_s.max = 1.5
    distribution.bin_width_s = 1.4
    validate_config(config)
    path = root / f"{phase}.yml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    return path


def verify_phase(config_path: Path, *, inject: bool) -> dict:
    real_run = subprocess.run
    injected_commands = []

    def run(command, **kwargs):
        if inject:
            # Mutate the runner-owned list so its final command evidence records
            # the actual invocation, including this harness-only suffix.
            command[2] += " error('BinaScape:AcceptanceSmoke:PostCompletion', 'Harness-only failure after entrypoint completion');"
            injected_commands.append(list(command))
        return real_run(command, **kwargs)

    with patch("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run):
        manifests, summary = render_static_scenes(config_path)
    assert len(manifests) == 1 and summary["completed_variants"] == 1, summary
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    records = load_variant_index(layout["render_index_path"])
    record = next(iter(records.values()))
    outcome = read_json_object(Path(record["attempt_outcome_path"]))
    assert outcome["commit_status"] == "committed", outcome
    assert outcome["render_validity"] == "valid", outcome
    assert inspect_completion(outcome)["status"] == "valid", outcome
    assert outcome["validation"]["acoustic_rejection"] is None, outcome
    diagnostic_path = layout["matlab_diagnostics_dir"] / outcome["attempt_id"] / "invocation.json"
    diagnostic = read_json_object(diagnostic_path)
    assert diagnostic["entrypoint_completed"] is True, diagnostic
    if inject:
        assert injected_commands, "The physical MATLAB process was not invoked"
        assert outcome["process_status"] == "failed" and outcome["exit_code"] == 1, outcome
        assert outcome["decision"] == "continue_with_warning", outcome
        assert outcome["warning"]["code"] == "matlab_process_failed_after_completion", outcome
        assert diagnostic["process_status"] == "failed" and diagnostic["exit_code"] == 1, diagnostic
        assert diagnostic["failure_location"] == "post_entrypoint", diagnostic
        assert summary["recovered_matlab_processes"] == 1, summary
    else:
        assert outcome["decision"] in ("continue", "continue_with_warning"), outcome
    return {"summary": summary, "attempt_id": outcome["attempt_id"], "outcome_path": str(record["attempt_outcome_path"]),
            "process_status": outcome["process_status"], "exit_code": outcome["exit_code"],
            "render_validity": outcome["render_validity"], "decision": outcome["decision"],
            "injected": inject}


def verify(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    report = {"normal": verify_phase(prepare_config(root, "normal"), inject=False),
              "post_completion": verify_phase(prepare_config(root, "post_completion"), inject=True)}
    report_path = root / "acceptance.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"evidence": str(report_path), **{
        phase: {key: data[key] for key in ("process_status", "exit_code", "render_validity", "decision", "attempt_id")}
        for phase, data in report.items()
    }}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    verify(parser.parse_args().output_root.resolve())
