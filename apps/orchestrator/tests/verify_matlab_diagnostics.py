"""Opt-in Windows MATLAB/ITA/RAVEN acceptance, using a fresh output directory.

From repository root:
uv run --project apps/orchestrator python apps/orchestrator/tests/verify_matlab_diagnostics.py --output-root <new-directory>
"""

import argparse
import json
import subprocess
from pathlib import Path
from uuid import uuid4

import yaml

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.config.validator import validate_config
from acoustic_orchestrator.pipeline.matlab_runner import render_manifest_with_matlab
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes


REPOSITORY = Path(__file__).resolve().parents[3]


def verify(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    config = load_config(REPOSITORY / "configs/experiments/circular_array_only.yml")
    config.experiment.experiment_id = f"diagnostics_{uuid4().hex}"
    config.outputs.artifact_root = root
    config.outputs.run_name = "valid_render"
    config.execution.num_simulations = 1
    config.execution.num_workers = 1
    config.execution.resume_if_possible = False
    config.hearing_degradation.enabled = False
    config.hearing_degradation.input_targets = []
    # One broad bin keeps this process/diagnostics smoke bounded like the existing array smoke.
    distribution = config.room_sampling.reverberation.distribution
    distribution.range_s.min = 0.1
    distribution.range_s.max = 1.5
    distribution.bin_width_s = 1.4
    validate_config(config)
    config_path = root / "config.yml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    _, functional_summary = render_static_scenes(config_path)
    assert functional_summary["completed_variants"] == 1, functional_summary
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    records = []
    for path in layout["matlab_diagnostics_dir"].glob("*/invocation.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["exit_code"] == 0 and record["process_status"] == "succeeded", record
        assert record["entrypoint_completed"] is True, record
        assert record["last_completed_stage"] == "artifact_writing", record
        assert record["diagnostic_errors"] == [], record
        assert all(a["write_confirmed"] and a["observed_after"] for a in record["artifacts"])
        assert Path(record["console_path"]).stat().st_size > 0
        events = [json.loads(line) for line in Path(record["events_path"]).read_text(encoding="utf-8").splitlines()]
        assert all(e["attempt_id"] == record["attempt_id"] for e in events)
        assert [e["step"] for e in events if e["type"].startswith("step_")] == [
            "raven_run", "raven_run", "audio_postprocessing", "audio_postprocessing",
        ]
        records.append({"summary_path": str(path), "exit_code": record["exit_code"],
                        "duration_s": record["duration_s"], "events": len(events)})
    assert len(records) == functional_summary["render_jobs"]

    invalid = root / "invalid.json"
    invalid.write_text('{"schema_version": "unsupported"}', encoding="utf-8")
    invalid_diagnostics = root / "invalid_attempts"
    try:
        render_manifest_with_matlab(invalid, diagnostics_dir=invalid_diagnostics)
    except subprocess.CalledProcessError as exc:
        error_code = exc.returncode
    else:
        raise AssertionError("Invalid manifest unexpectedly succeeded")
    invalid_path = next(invalid_diagnostics.glob("*/invocation.json"))
    failure = json.loads(invalid_path.read_text(encoding="utf-8"))
    assert failure["exit_code"] == error_code != 0
    assert failure["process_status"] == "failed" and failure["entrypoint_completed"] is False
    assert failure["failure_location"] == "initialization"
    assert failure["matlab_error"]["message"]
    assert Path(failure["console_path"]).stat().st_size > 0
    report = {"render": functional_summary, "successful_attempts": records,
              "invalid_attempt": {"summary_path": str(invalid_path), "exit_code": error_code,
                                  "failure_location": failure["failure_location"]}}
    (root / "acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    verify(parser.parse_args().output_root.resolve())
