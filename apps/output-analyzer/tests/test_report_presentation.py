from __future__ import annotations

import copy
import json
import re

from output_analyzer.loader import load_run
from output_analyzer.report import build_analysis

HIDDEN = [
    "hist_room_length",
    "hist_room_width",
    "rt30_sabine_estimate",
    "rt30_raven_rpf_gett30",
    "sampling_attempts",
    "sampling_requested_bin",
    "surface_area",
    "hist_employed_hrtf",
]


def _metrics(artifacts):
    rows = json.loads(artifacts["metrics_json"].read_text(encoding="utf-8"))
    return {row["key"]: row for row in rows}


def _report(artifacts):
    return artifacts["report"].read_text(encoding="utf-8")


def _two_scene_data(sample_run):
    data = load_run(sample_run)
    other = copy.deepcopy(data.scenes[0])
    other.update(scene_id="bigger_scene", room_area_m2=80.0, room_volume_m3=240.0)
    data.scenes.append(other)
    return data


def test_hidden_charts_absent_but_metrics_kept(sample_run):
    data = _two_scene_data(sample_run)
    artifacts = build_analysis(data)
    images = artifacts["report"].parent / "images"
    metrics = _metrics(artifacts)
    report = _report(artifacts)
    for key in HIDDEN:
        assert key in metrics, key
        assert not (images / f"{key}.svg").exists(), key
        assert key not in artifacts, key
    assert "Envelope X extent" not in report
    assert "Real surface area" not in report


def test_hidden_chart_removed_from_reused_output_dir(sample_run):
    data = _two_scene_data(sample_run)
    images = sample_run / "analysis" / "images"
    images.mkdir(parents=True)
    stale = images / "surface_area.svg"
    stale.write_text("<svg/>", encoding="utf-8")
    build_analysis(data)
    assert not stale.exists()


def test_kept_charts_present(sample_run):
    data = _two_scene_data(sample_run)
    artifacts = build_analysis(data)
    report = _report(artifacts)
    for key in ("hist_room_area", "hist_distance"):
        svg = artifacts[key].read_text(encoding="utf-8")
        assert svg in report


def test_constant_metric_collapses_to_summary_line(sample_run):
    data = load_run(sample_run)
    for source in data.sources:
        source["gain_db"] = 0.0
    artifacts = build_analysis(data)
    images = artifacts["report"].parent / "images"
    report = _report(artifacts)
    assert not (images / "hist_gain.svg").exists()
    line = re.search(r'<li class="constant-metric">Declared gain[^<]*</li>', report)
    assert line is not None
    text = line.group(0)
    assert ": 0 dB" in text and f"n={len(data.sources)}" in text
    assert _metrics(artifacts)["hist_gain"]["mean"] == 0.0


def test_constant_categorical_metric_collapses(sample_run):
    data = load_run(sample_run)
    for row in data.render_scenes:
        row["background_noise_applied"] = False
    artifacts = build_analysis(data)
    images = artifacts["report"].parent / "images"
    assert not (images / "background_noise.svg").exists()
    report = _report(artifacts)
    assert re.search(
        r'<li class="constant-metric">Declared background noise application: False boolean; n=\d+',
        report,
    )


def test_empty_metrics_listed_not_drawn(sample_run):
    data = load_run(sample_run)
    data.degraded_records = []
    artifacts = build_analysis(data)
    images = artifacts["report"].parent / "images"
    report = _report(artifacts)
    for key in ("degraded_profiles", "degraded_status", "degraded_asymmetry"):
        assert not (images / f"{key}.svg").exists()
        assert key not in artifacts
    for title in (
        "Hearing profiles",
        "Degradation status",
        "Declared ear asymmetry on common bands",
    ):
        assert re.search(
            r'<li class="empty-metric">' + re.escape(title) + r": Not available; n=0; excluded=0",
            report,
        ), title


def test_scene_mean_charts_exempt_from_collapse(sample_run):
    # One scene gives constant scene means; the scene-mean charts must still render.
    artifacts = build_analysis(load_run(sample_run))
    keys = [key for key in _metrics(artifacts) if key.startswith("rt30_scene_mean_")]
    assert len(keys) == 3
    assert all(key in artifacts for key in keys)


def _tables(report):
    return re.findall(r"<table>.*?</table>", report, re.S)


def _data_rows(table):
    return table.count("<tr>") - 1


def test_hrtf_coverage_table_sorted_by_count_then_identity(sample_run):
    data = load_run(sample_run)
    base = data.render_scenes[0]
    data.render_scenes = [
        dict(base, variant_id=f"v{i}", employed_hrtf=hrtf)
        for i, hrtf in enumerate(["b.daff", "a.daff", "c.daff", "c.daff", "b.daff"])
    ]
    report = _report(build_analysis(data))
    block = re.search(r'<div class="hrtf-coverage">(.*?)</div>', report, re.S).group(1)
    rows = re.findall(r"<tr><td>([^<]*)</td><td>(\d+)</td></tr>", block)
    assert rows == [("b.daff", "2"), ("c.daff", "2"), ("a.daff", "1")]
    assert "<th>HRTF identity</th><th>Render variants</th>" in block


def test_large_run_has_no_record_dumps_and_names_exports(sample_run):
    data = load_run(sample_run)
    for name in (
        "rt30_records",
        "rt30_consolidated",
        "rt30_comparisons",
        "rt30_pairs",
        "coverage",
        "materials",
        "sampling_records",
        "render_scenes",
    ):
        rows = getattr(data, name)
        setattr(data, name, [dict(row, copy_index=i) for i in range(60) for row in rows])
    report = _report(build_analysis(data))
    hrtf = re.search(r'<div class="hrtf-coverage">(.*?)</div>', report, re.S).group(1)
    for table in _tables(report):
        if table in hrtf:
            continue
        assert _data_rows(table) <= 50, table[:200]
    exports = re.search(r'<section id="data-exports">.*?</section>', report, re.S).group(0)
    for stem in (
        "rt30_records",
        "rt30_pairs",
        "rt30_comparisons",
        "rt30_consolidated",
        "coverage",
        "materials",
        "sampling_records",
    ):
        assert f"<td>{stem}</td>" in exports, stem
    assert "copy_index" not in report


def test_incident_table_is_bounded_and_points_to_export(sample_run):
    data = load_run(sample_run)
    data.incidents = [
        {
            "incident_id": f"i{i}",
            "category": "test_category",
            "entity": f"scene:s{i}",
            "field": "field",
            "path": "p",
            "detail": f"detail-{i}",
        }
        for i in range(120)
    ]
    report = _report(build_analysis(data))
    block = re.search(r'<div class="incidents">(.*?)</div>', report, re.S).group(1)
    assert _data_rows(_tables(block)[0]) == 50
    assert "<th>Category</th><th>Entity</th><th>Field</th><th>Detail</th>" in block
    assert "Showing 50 of 120 incidents" in block and "incidents.jsonl" in block
    assert "detail-49" in block and "detail-50" not in block


def test_general_parameters_are_readable_text(sample_run):
    data = load_run(sample_run)
    data.general_parameters["sample_rate_hz"] = {"44100.0": 3000}
    data.general_parameters["render_configuration"] = [{"scene_id": "hidden-marker"}]
    report = _report(build_analysis(data))
    section = re.search(r'<section id="general-parameters">.*?</section>', report, re.S).group(0)
    assert "<td>sample_rate_hz</td><td>44100.0 (n=3000)</td>" in section
    assert "<td>rt30_numerical_tolerance</td><td>absolute_s: 1e-06; relative: 1e-06; aggregation: " in section
    assert "{" not in re.search(r"<td>sample_rate_hz</td><td>[^<]*</td>", section).group(0)
    assert "hidden-marker" not in report and "render_configuration" not in report


def _card(report, scene_id):
    return re.search(
        r'<article class="carousel-card" data-slide data-scene-id="'
        + re.escape(scene_id)
        + r'".*?</article>',
        report,
        re.S,
    ).group(0)


def test_card_is_compact_with_inline_hrtfs_and_pose_table(sample_run):
    data = load_run(sample_run)
    data.render_scenes.append(dict(data.render_scenes[0], hrtf_id="second_hrtf", variant_id="v2"))
    report = _report(build_analysis(data))
    card = _card(report, "scene_static_0001")
    assert '<p class="card-hrtfs">HRTF/render variants: binaural_hrtf, second_hrtf</p>' in card
    pose = re.search(r'<div class="card-poses">(.*?)</div>', card, re.S).group(1)
    assert "<th>Role</th><th>Identity</th><th>X (m)</th><th>Y (m)</th><th>Z (m)</th>" in pose
    assert "<tr><td>Receiver</td><td>scene_static_0001</td><td>4</td><td>1.5</td><td>3</td></tr>" in pose
    assert "<tr><td>Source</td><td>src_0001</td><td>2</td><td>1.5</td><td>2</td></tr>" in pose
    assert "<details>" not in card
    assert "<svg " in card and 'class="scene-rt30"' in card
    assert "metadata_path" not in card


def test_presentation_rules_keep_every_export_and_metric(sample_run):
    from output_analyzer.loader import summary_for

    data = load_run(sample_run)
    data.degraded_records = []
    expected = {
        name: copy.deepcopy(getattr(data, attr))
        for name, attr in [
            ("scenes", "scenes"),
            ("sources", "sources"),
            ("render_scenes", "render_scenes"),
            ("degraded_metadata", "degraded_records"),
            ("incidents", "incidents"),
            ("rt30_records", "rt30_records"),
            ("rt30_pairs", "rt30_pairs"),
            ("rt30_comparisons", "rt30_comparisons"),
            ("rt30_consolidated", "rt30_consolidated"),
            ("materials", "materials"),
            ("sampling_records", "sampling_records"),
            ("coverage", "coverage"),
        ]
    }
    expected_status = summary_for(data)["status"]
    artifacts = build_analysis(data)
    for name, records in expected.items():
        assert json.loads(artifacts[f"{name}_json"].read_text(encoding="utf-8")) == records, name
    metrics = _metrics(artifacts)
    # Hidden, constant and empty metrics keep their full metrics.json rows.
    surface = metrics["surface_area"]
    assert surface["n_valid"] == len([m for m in data.materials if m.get("surface_area_m2") is not None])
    assert len(surface["observations"]) == surface["n_valid"]
    assert metrics["hist_employed_hrtf"]["counts"]
    assert metrics["degraded_status"]["n_valid"] == 0 and metrics["degraded_status"]["counts"] == {}
    assert metrics["hist_n_sources"]["mean"] == 2.0
    summary = json.loads(artifacts["summary"].read_text(encoding="utf-8"))
    assert summary["status"] == expected_status
    assert {row["key"] for row in summary["report_metrics"]} == set(metrics)
