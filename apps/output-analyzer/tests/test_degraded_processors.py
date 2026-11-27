"""Degraded records per processor (msbg/whis), legacy layout and calibration exports."""

from __future__ import annotations

import csv
import json
import shutil

from output_analyzer.loader import load_run, summary_for
from output_analyzer.report import build_analysis

VARIANT = "scene_static_0001__binaural_hrtf"


def _legacy_sidecar(run):
    return run / "output_audio/degraded/binaural_hrtf/mild_loss" / f"{VARIANT}.json"


def _as_processor(run, processor: str, *, status: str = "completed", k: float = 95.0):
    """Copy the fixture sidecar into the per-processor layout with calibration fields."""
    doc = json.loads(_legacy_sidecar(run).read_text(encoding="utf-8"))
    doc["job_id"] = f"clarity__{VARIANT}__mild_loss__{processor}"
    doc["status"] = status
    doc["processor"] = {"name": processor}
    doc["outer_middle_ear"] = "itu"
    doc["calibration"] = {
        "mode": "presentation_level",
        "presentation_level_db_spl": 65.0,
        "effective_scale_db_spl": k,
        "channels": {"left": {"leq_db_spl": 66.2}, "right": {"leq_db_spl": 63.5}},
    }
    target = run / "output_audio/degraded" / processor / "binaural_hrtf/mild_loss" / f"{VARIANT}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc), encoding="utf-8")
    return target


def _two_processor_run(run, *, whis_status: str = "completed"):
    _as_processor(run, "msbg")
    _as_processor(run, "whis", status=whis_status)
    shutil.rmtree(run / "output_audio/degraded/binaural_hrtf")
    return run


def test_run_with_two_processors_keeps_both_records_without_duplicates(sample_run):
    data = load_run(_two_processor_run(sample_run))

    records = sorted(data.degraded_records, key=lambda r: r["processor"])
    assert [r["processor"] for r in records] == ["msbg", "whis"]
    assert all(r["variant_id"] == VARIANT and r["hearing_profile_id"] == "mild_loss" for r in records)
    assert all(r["legacy"] is False for r in records)
    assert not [i for i in data.incidents if i["category"] == "duplicate_identity"]


def test_calibration_fields_are_exported_per_record(sample_run):
    artifacts = build_analysis(load_run(_two_processor_run(sample_run)))

    exported = json.loads(artifacts["degraded_metadata_json"].read_text(encoding="utf-8"))
    whis = next(r for r in exported if r["processor"] == "whis")
    assert whis["effective_scale_db_spl"] == 95.0
    assert whis["presentation_level_db_spl"] == 65.0
    assert whis["left_leq_db_spl"] == 66.2 and whis["right_leq_db_spl"] == 63.5
    assert whis["calibration_mode"] == "presentation_level" and whis["outer_middle_ear"] == "itu"
    with artifacts["degraded_metadata_csv"].open(encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert {"processor", "legacy", "effective_scale_db_spl", "left_leq_db_spl", "right_leq_db_spl"} <= set(header)


def test_status_summary_and_charts_are_broken_down_by_processor(sample_run):
    data = load_run(_two_processor_run(sample_run, whis_status="failed"))
    artifacts = build_analysis(data)

    summary = summary_for(data)
    assert summary["degraded_statuses_by_processor"] == {"msbg": {"completed": 1}, "whis": {"failed": 1}}
    assert summary["degraded_processors"] == {"msbg": 1, "whis": 1}
    metrics = {row["key"]: row for row in json.loads(artifacts["metrics_json"].read_text(encoding="utf-8"))}
    assert metrics["degraded_status_whis"]["counts"] == {"failed": 1}
    assert metrics["degraded_status_msbg"]["counts"] == {"completed": 1}
    assert "degraded_status" not in metrics
    report = artifacts["report"].read_text(encoding="utf-8")
    assert "Degradation status (whis)" in report and "Degradation status (msbg)" in report


def test_previous_layout_sidecars_load_as_legacy_msbg(sample_run):
    mutate_doc = json.loads(_legacy_sidecar(sample_run).read_text(encoding="utf-8"))
    mutate_doc.pop("processor")
    _legacy_sidecar(sample_run).write_text(json.dumps(mutate_doc), encoding="utf-8")

    data = load_run(sample_run)
    artifacts = build_analysis(data)

    [record] = data.degraded_records
    assert record["processor"] == "msbg" and record["legacy"] is True
    assert record["effective_scale_db_spl"] is None
    assert summary_for(data)["degraded_legacy_count"] == 1
    metrics = {row["key"] for row in json.loads(artifacts["metrics_json"].read_text(encoding="utf-8"))}
    assert {"degraded_profiles", "degraded_status", "degraded_asymmetry"} <= metrics


def test_single_processor_run_keeps_the_historical_charts(sample_run):
    _as_processor(sample_run, "msbg")
    shutil.rmtree(sample_run / "output_audio/degraded/binaural_hrtf")

    artifacts = build_analysis(load_run(sample_run))

    metrics = {row["key"] for row in json.loads(artifacts["metrics_json"].read_text(encoding="utf-8"))}
    assert {"degraded_profiles", "degraded_status", "degraded_asymmetry"} <= metrics
    assert not {key for key in metrics if key.startswith("degraded_status_")}
