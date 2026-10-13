from __future__ import annotations

import json

import pytest

from output_analyzer.loader import load_run
from output_analyzer.report import build_analysis, _write_plan


def test_build_analysis_exports_auditable_populations_and_embeds_svg(sample_run):
    data = load_run(sample_run)
    artifacts = build_analysis(data)
    report = artifacts["report"].read_text(encoding="utf-8")
    summary = json.loads(artifacts["summary"].read_text(encoding="utf-8"))
    assert "<svg " in report
    assert "<img " not in report
    assert report.count('id="general-parameters"') == 1
    for geometry in ("shoebox", "l_shape", "trapezoid"):
        assert f'id="geometry-{geometry}"' in report
    assert "Expected coverage unavailable" in report or "coverage" in report
    for name, records in [
        ("scenes", data.scenes),
        ("sources", data.sources),
        ("rt30_pairs", data.rt30_pairs),
        ("incidents", data.incidents),
    ]:
        exported = json.loads(artifacts[f"{name}_json"].read_text(encoding="utf-8"))
        assert exported == records
    metrics = json.loads(artifacts["metrics_json"].read_text(encoding="utf-8"))
    distance = next(row for row in metrics if row["key"] == "hist_distance")
    assert distance["n_valid"] + distance["n_excluded"] == len(data.sources)
    assert summary["unique_scene_count"] == len(data.scenes)
    assert "joined_render_sources.jsonl" in report


def test_exact_plan_preserves_l_shape_offsets_equal_scale_and_escapes(tmp_path):
    path = tmp_path / "plan.svg"
    scene = {
        "scene_id": "<scene>",
        "footprint_vertices_m": [
            [10, 20],
            [14, 20],
            [14, 22],
            [12, 22],
            [12, 24],
            [10, 24],
        ],
        "receiver_x_m": 11,
        "receiver_z_m": 21,
    }
    source = {
        "source_id": "<source>",
        "source_type": "speech",
        "source_x_m": 13,
        "source_z_m": 23,
    }
    assert _write_plan(path, scene, [source])
    svg = path.read_text(encoding="utf-8")
    assert "<polygon " in svg
    assert 'data-x="10' in svg and 'data-z="20' in svg
    assert "&lt;scene&gt;" in svg and "&lt;source&gt;" in svg
    assert "X (m)" in svg and "Z (m)" in svg
    assert "Receiver" in svg and "speech" in svg
    assert 'data-scale-x="' in svg
    import re

    scale_x, scale_z = re.search(
        r'data-scale-x="([^"]+)" data-scale-z="([^"]+)"', svg
    ).groups()
    assert scale_x == scale_z


def test_empty_run_reports_unavailable_and_incidents(tmp_path):
    data = load_run(tmp_path)
    artifacts = build_analysis(data)
    report = artifacts["report"].read_text(encoding="utf-8")
    assert "No analyzable data" in report or "sin datos analizables" in report
    assert "Not available" in report
    assert json.loads(artifacts["scenes_json"].read_text(encoding="utf-8")) == []


def test_write_failure_remains_operational_error(sample_run, tmp_path):
    occupied = tmp_path / "file"
    occupied.write_text("occupied", encoding="utf-8")
    with pytest.raises(OSError):
        build_analysis(load_run(sample_run), occupied)


def test_rt30_distribution_uses_loader_consolidation_without_reclassifying(sample_run):
    data = load_run(sample_run)
    baseline = [row["value_s"] for row in data.rt30_consolidated]
    # The report must use the authoritative consolidated mean, not raw variant rows.
    data.rt30_records.extend(
        dict(row, variant_id="another-hrtf", value_s=99)
        for row in list(data.rt30_records)
        if "RAVEN" in row["method"]
    )
    data.rt30_consolidated[0]["value_s"] += 5e-7
    artifacts = build_analysis(data)
    metrics = json.loads(artifacts["metrics_json"].read_text(encoding="utf-8"))
    raven = next(row for row in metrics if row["key"].startswith("rt30_raven"))
    assert raven["n_valid"] == len(baseline)
    assert raven["n_excluded"] == 0
    assert raven["observations"][0]["value"] == data.rt30_consolidated[0]["value_s"]
    assert (
        json.loads(artifacts["rt30_consolidated_json"].read_text(encoding="utf-8"))
        == data.rt30_consolidated
    )
    data.rt30_consolidated[0]["value_s"] = None
    artifacts = build_analysis(data)
    metrics = json.loads(artifacts["metrics_json"].read_text(encoding="utf-8"))
    raven = next(row for row in metrics if row["key"].startswith("rt30_raven"))
    assert raven["n_valid"] == len(baseline) - 1
    assert raven["n_excluded"] == 1


def test_rt30_comparison_reports_excluded_candidates(sample_run):
    data = load_run(sample_run)
    data.rt30_comparisons.append(
        {"scene_id": "conflicting-scene", "frequency_hz": 500, "difference_s": None}
    )
    artifacts = build_analysis(data)
    metrics = json.loads(artifacts["metrics_json"].read_text(encoding="utf-8"))
    comparison = next(row for row in metrics if row["key"] == "rt30_difference")
    assert comparison["n_valid"] == len(data.rt30_pairs)
    assert comparison["n_excluded"] == 1
    assert (
        json.loads(artifacts["rt30_comparisons_json"].read_text(encoding="utf-8"))
        == data.rt30_comparisons
    )


def test_sampling_charts_preserve_zero_false_and_missing_denominators(sample_run):
    data = load_run(sample_run)
    data.sampling_records = [
        {
            "scene_id": "one",
            "attempts": 0,
            "fallback_used": False,
            "requested_bin": "low",
        },
        {
            "scene_id": "two",
            "attempts": 3,
            "fallback_used": True,
            "requested_bin": "high",
        },
        {"scene_id": "three", "attempts": None, "fallback_used": None},
    ]
    artifacts = build_analysis(data)
    metrics = {
        row["key"]: row
        for row in json.loads(artifacts["metrics_json"].read_text(encoding="utf-8"))
    }
    assert metrics["sampling_attempts"]["n_valid"] == 2
    assert metrics["sampling_attempts"]["n_excluded"] == 1
    assert metrics["sampling_attempts"]["mean"] == 1.5
    assert metrics["sampling_fallback_used"]["counts"] == {"False": 1, "True": 1}
    assert metrics["sampling_fallback_used"]["n_excluded"] == 1
    assert metrics["sampling_receiver_attempt"]["n_valid"] == 0
    assert metrics["sampling_receiver_attempt"]["n_excluded"] == 3


def test_configuration_hidden_only_in_presentation_and_preserved_in_exports(sample_run):
    data = load_run(sample_run)
    data.general_parameters["render_configuration"] = {
        "private_marker": "configuration-only-sentinel"
    }
    artifacts = build_analysis(data)
    report = artifacts["report"].read_text(encoding="utf-8")
    assert "render_configuration" not in report
    assert "configuration-only-sentinel" not in report
    exported = json.loads(
        artifacts["general_parameters_json"].read_text(encoding="utf-8")
    )
    assert (
        exported["render_configuration"]
        == data.general_parameters["render_configuration"]
    )
    assert json.loads(artifacts["summary"].read_text(encoding="utf-8"))[
        "unique_scene_count"
    ] == len(data.scenes)


def test_carousels_keep_one_card_per_scene_and_independent_controls(sample_run):
    data = load_run(sample_run)
    scene = data.scenes[0]
    data.scenes.extend(
        [
            dict(scene, scene_id="second-scene"),
            dict(scene, scene_id="single-L", geometry_type="l_shape"),
        ]
    )
    data.render_scenes.append(
        dict(data.render_scenes[0], variant_id="new-hrtf-variant")
    )
    report = build_analysis(data)["report"].read_text(encoding="utf-8")
    assert report.count('data-carousel="') == 3
    assert report.count('class="carousel-card"') == 3
    assert "new-hrtf-variant" in report
    assert 'data-scene-id="second-scene"' in report
    assert "data-scene-select" in report and 'aria-live="polite"' in report
    assert "ArrowLeft" in report and "ArrowRight" in report and "Home: 0" in report
    assert "setInterval" not in report and "setTimeout" not in report
    assert "0 of 0 — Not available: no valid floor plans." in report
    import re

    empty = re.search(
        r'<section class="carousel" id="geometry-trapezoid".*?</section>', report, re.S
    ).group()
    singleton = re.search(
        r'<section class="carousel" id="geometry-l_shape".*?</section>', report, re.S
    ).group()
    assert empty.count(" disabled") == 3
    assert singleton.count(" disabled") == 3


def test_every_exported_svg_has_visible_legend_and_metric_population(sample_run):
    import xml.etree.ElementTree as ET

    data = load_run(sample_run)
    artifacts = build_analysis(data)
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    for path in artifacts.values():
        if path.suffix != ".svg":
            continue
        svg = ET.parse(path).getroot()
        legend = svg.find("svg:g[@class='chart-legend']", namespace)
        assert legend is not None, path
        text = " ".join(legend.itertext())
        assert "Legend" in text
        if path.name.startswith("plan_"):
            assert "Room contour" in text and "Receiver (square)" in text
        else:
            assert "Population:" in text and "excluded=" in text and "unit:" in text
        if path.name == "rt30_difference.svg":
            assert "RAVEN − Sabine (s)" in text and "Central frequencies (Hz)" in text
        if path.name == "sampling_receiver_attempt.svg":
            assert "No data / Not available" in text
            assert legend.find("svg:rect", namespace) is None


def test_plan_without_poses_has_no_actor_legend_symbols(tmp_path):
    import xml.etree.ElementTree as ET

    path = tmp_path / "plan.svg"
    assert _write_plan(
        path,
        {"scene_id": "empty", "footprint_vertices_m": [[0, 0], [4, 0], [4, 3], [0, 3]]},
        [{"source_id": "missing", "source_type": "speech"}],
    )
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    legend = ET.parse(path).getroot().find("svg:g[@class='chart-legend']", namespace)
    assert legend.find("svg:rect", namespace) is None
    assert legend.find("svg:circle", namespace) is None
    assert legend.find("svg:line", namespace) is not None
    text = " ".join(legend.itertext())
    assert "n=0; excluded=1; unit: receiver pose" in text
    assert "n=0; excluded=1; unit: source pose" in text


def _rt30_card(report, scene_id):
    import re

    return re.search(
        r'<section class="scene-rt30" data-scene-rt30="'
        + scene_id
        + r'">(.*?)</section>',
        report,
        re.S,
    ).group(1)


def test_scene_rt30_table_common_means_extra_bands_and_identity(sample_run):
    import copy

    data = load_run(sample_run)
    data.rt30_records.append(
        dict(
            scene_id="scene_static_0001",
            method="Sabine estimate",
            frequency_hz=8000,
            value_s=9,
        )
    )
    data.rt30_consolidated.append(
        dict(
            scene_id="scene_static_0001",
            method="RAVEN rpf.getT30",
            frequency_hz=8000,
            value_s=10,
            n_variants=1,
        )
    )
    other = copy.deepcopy(data.scenes[0])
    other["scene_id"] = "other_scene"
    data.scenes.append(other)
    report = build_analysis(data)["report"].read_text(encoding="utf8")
    first = _rt30_card(report, "scene_static_0001")
    second = _rt30_card(report, "other_scene")
    assert "0.875" in first and "0.5" in first
    assert "8000" in first and "Adicional" in first
    assert "n=2" in first and "125, 250 Hz" in first
    assert first.index("125</td>") < first.index("250</td>") < first.index("8000</td>")
    assert "n=0" in second and "No disponible" in second
    assert "0.875" not in second
    assert report.count("data-scene-rt30=") == 2


def test_scene_rt30_conflict_keeps_sabine_and_excludes_only_bad_pair(sample_run):
    render = next((sample_run / "output_audio/render").rglob("*__render.json"))
    doc = json.loads(render.read_text())
    doc["hrtf_id"] = "other"
    doc["summary"]["room"]["reverberation"]["t30_s"][0] = 9
    render.with_name("other__render.json").write_text(json.dumps(doc))
    data = load_run(sample_run)
    first = _rt30_card(
        build_analysis(data)["report"].read_text(encoding="utf8"), "scene_static_0001"
    )
    assert "conflicto" in first and "No disponible" in first
    assert "n=1" in first and "excluidas=1" in first
    assert "0.75" in first and "0.5" in first
    assert "n_variants" in first and "rt30_conflict" in first


def test_scene_rt30_no_pairs_never_imputes_zero(sample_run):
    render = next((sample_run / "output_audio/render").rglob("*__render.json"))
    doc = json.loads(render.read_text())
    doc["summary"]["room"]["reverberation"]["t30_s"] = [None, None]
    render.write_text(json.dumps(doc))
    first = _rt30_card(
        build_analysis(load_run(sample_run))["report"].read_text(encoding="utf8"),
        "scene_static_0001",
    )
    assert "n=0" in first and "excluidas=2" in first
    assert "Promedio Sabine: No disponible" in first
    assert "Promedio RAVEN: No disponible" in first
