from __future__ import annotations

from output_analyzer.loader import load_run
from output_analyzer.report import build_analysis


def test_build_analysis_writes_data_images_and_html(sample_run):
    data = load_run(sample_run)
    artifacts = build_analysis(data)

    assert artifacts["report"].exists()
    assert artifacts["summary"].exists()
    assert artifacts["joined_sources_jsonl"].exists()
    assert artifacts["joined_sources_csv"].exists()
    assert artifacts["degraded_jsonl"].exists()
    assert artifacts["degraded_csv"].exists()
    assert (sample_run / "analysis" / "images" / "hist_global_doa.svg").exists()
    assert (sample_run / "analysis" / "images" / "hist_receiver_relative_doa.svg").exists()
    assert (sample_run / "analysis" / "images" / "hist_employed_hrtf.svg").exists()
    assert (sample_run / "analysis" / "images" / "hist_room_dimensions.svg").exists()
    assert (sample_run / "analysis" / "images" / "hist_room_area.svg").exists()
    assert (sample_run / "analysis" / "images" / "degraded_profiles.svg").exists()
    assert (sample_run / "analysis" / "images" / "heatmap_locations_speech.svg").exists()
    assert (sample_run / "analysis" / "images" / "heatmap_receiver_locations.svg").exists()

    heatmap = (sample_run / "analysis" / "images" / "heatmap_locations_speech.svg").read_text(encoding="utf-8")
    assert ">0<" in heatmap
    assert ">8<" in heatmap
    assert ">6<" in heatmap

    report = artifacts["report"].read_text(encoding="utf-8")
    assert "Output Analysis Report" in report
    assert "joined_render_sources.jsonl" in report
    assert "heatmap receiver locations" in report
    assert "hist room dimensions" in report
