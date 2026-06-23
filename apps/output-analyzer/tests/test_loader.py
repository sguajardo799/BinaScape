from __future__ import annotations

import json

from output_analyzer.loader import load_run, summary_for


def test_load_run_joins_render_metadata_with_scene_manifest(sample_run):
    data = load_run(sample_run)

    assert len(data.render_scenes) == 1
    assert len(data.render_sources) == 2
    assert data.render_scenes[0]["room_area_m2"] == 48.0
    assert data.render_scenes[0]["employed_hrtf"] == "id25_HRTF.v17.ir.daff"

    source = next(record for record in data.render_sources if record["source_id"] == "src_0001")
    assert source["source_type"] == "speech"
    assert source["distance_m"] is not None
    assert source["global_doa_deg"] is not None
    assert source["receiver_relative_doa_deg"] is not None

    summary = summary_for(data)
    assert summary["render_scene_count"] == 1
    assert summary["source_types"] == {"speech": 1, "traffic": 1}


def test_load_run_summarizes_degraded_metadata_without_audio_inspection(sample_run):
    data = load_run(sample_run)

    assert len(data.degraded_records) == 1
    record = data.degraded_records[0]
    assert record["hearing_profile_id"] == "mild_loss"
    assert record["processor_name"] == "msbg"
    assert record["left_mean_loss_db"] == 15.0
    assert record["right_mean_loss_db"] == 20.0
    assert record["ear_asymmetry_db"] == 5.0


def test_load_run_uses_index_files_when_available(sample_run):
    (sample_run / "indexes").mkdir()
    (sample_run / "indexes" / "render_index.jsonl").write_text(
        json.dumps({"scene_id": "scene_static_0001", "status": "completed"}) + "\n",
        encoding="utf-8",
    )

    data = load_run(sample_run)

    assert data.render_index == [{"scene_id": "scene_static_0001", "status": "completed"}]
