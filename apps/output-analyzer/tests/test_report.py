from __future__ import annotations

import json

import pytest

from output_analyzer.loader import load_run
from output_analyzer.report import build_analysis, _write_plan, _write_scene_histogram


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
        if path.name.startswith("rt30_scene_mean_"):
            assert "Central frequencies (Hz): 125, 250" in text
            assert "Bin edges: data_derived." in text
    assert sum(path.name.startswith("rt30_scene_mean_") for path in artifacts.values()) == 3


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


SVG_NS = {"svg": "http://www.w3.org/2000/svg"}


def _scene_mean_metrics(artifacts):
    metrics = json.loads(artifacts["metrics_json"].read_text(encoding="utf-8"))
    return {row["key"]: row for row in metrics if row["key"].startswith("rt30_scene_mean_")}


def _svg_root(path):
    import xml.etree.ElementTree as ET

    return ET.parse(path).getroot()


def _class_elements(root, css_class):
    return [el for el in root.iter() if el.get("class") == css_class]


def test_scene_mean_histograms_draw_exported_bins(sample_run):
    data = load_run(sample_run)
    artifacts = build_analysis(data)
    histograms = {
        key: row for key, row in _scene_mean_metrics(artifacts).items()
        if row["chart_type"] == "scene_histogram"
    }
    assert {row["method"] for row in histograms.values()} == {"sabine", "raven"}
    for key, metric in histograms.items():
        path = artifacts[key]
        assert path.name == f"{key}.svg" and path.parent.name == "images"
        bars = _class_elements(_svg_root(path), "scene-bin")
        assert [int(bar.get("data-count")) for bar in bars] == metric["bin_counts"]
        assert [float(bar.get("data-lower")) for bar in bars] == metric["bin_edges_s"][:-1]
        assert [float(bar.get("data-upper")) for bar in bars] == metric["bin_edges_s"][1:]


def _histogram_metric(**overrides):
    metric = dict(
        key="rt30_scene_mean_raven_bands_500p0", method="raven", chart_type="scene_histogram",
        title="RAVEN consolidated T30 — complete mean RT30 per scene",
        population="unique scenes", unit="s", n_valid=10, n_excluded=0,
        selected_frequencies_hz=[500.0], edge_source="sampling_batch",
        interval_closure="[lower, upper), final interval [lower, upper]",
        bin_edges_s=[0.2, 0.5, 0.9], bin_counts=[3, 4], underflow_count=2, overflow_count=1,
        target_counts=[3, 3], reference_unavailable_reason=None, count_axis_max=4,
    )
    metric.update(overrides)
    return metric


def _legend_text(root):
    return " ".join(root.find("svg:g[@class='chart-legend']", SVG_NS).itertext())


def test_scene_histogram_labels_out_of_range_without_folding_edges(tmp_path):
    path = tmp_path / "hist.svg"
    _write_scene_histogram(path, _histogram_metric())
    root = _svg_root(path)
    assert [int(b.get("data-count")) for b in _class_elements(root, "scene-bin")] == [3, 4]
    assert [int(b.get("data-count")) for b in _class_elements(root, "scene-underflow")] == [2]
    assert [int(b.get("data-count")) for b in _class_elements(root, "scene-overflow")] == [1]
    text = " ".join(root.itertext())
    assert "< 0.2 s: 2" in text and "> 0.9 s: 1" in text
    assert "below 0.2 s = 2; above 0.9 s = 1" in _legend_text(root)


def test_scene_histogram_hides_zero_out_of_range_bars(tmp_path):
    path = tmp_path / "hist.svg"
    _write_scene_histogram(path, _histogram_metric(underflow_count=0, overflow_count=0))
    root = _svg_root(path)
    assert not _class_elements(root, "scene-underflow")
    assert not _class_elements(root, "scene-overflow")


def test_scene_histogram_target_marks_present_and_absent(tmp_path):
    present = tmp_path / "present.svg"
    _write_scene_histogram(present, _histogram_metric())
    root = _svg_root(present)
    assert [int(m.get("data-count")) for m in _class_elements(root, "scene-target")] == [3, 3]
    assert "Sampling target counts per bin" in _legend_text(root)
    absent = tmp_path / "absent.svg"
    reason = "Sampling batch scope unavailable or ambiguous"
    _write_scene_histogram(absent, _histogram_metric(target_counts=None, reference_unavailable_reason=reason))
    root = _svg_root(absent)
    assert not _class_elements(root, "scene-target")
    assert reason in _legend_text(root)


def test_scene_histogram_empty_population_renders_not_available(sample_run):
    data = load_run(sample_run)
    for row in data.rt30_scene_means:
        row.update(raven_mean_s=None, raven_exclusion_reason="missing band", difference_mean_s=None)
    artifacts = build_analysis(data)
    raven = next(
        key for key, row in _scene_mean_metrics(artifacts).items()
        if row.get("method") == "raven"
    )
    root = _svg_root(artifacts[raven])
    assert "No data / Not available" in " ".join(root.itertext())
    assert not _class_elements(root, "scene-bin")
    scatter = next(
        key for key, row in _scene_mean_metrics(artifacts).items()
        if row["chart_type"] == "scene_scatter"
    )
    root = _svg_root(artifacts[scatter])
    assert "No data / Not available" in " ".join(root.itertext())
    assert not _class_elements(root, "scene-point")
    assert artifacts["report"].exists()


def test_scene_mean_scatter_draws_paired_points_and_identity(sample_run):
    data = load_run(sample_run)
    artifacts = build_analysis(data)
    scatter = [
        row for row in _scene_mean_metrics(artifacts).values()
        if row["chart_type"] == "scene_scatter"
    ]
    assert len(scatter) == 1
    metric = scatter[0]
    path = artifacts[metric["key"]]
    assert path.name == f"{metric['key']}.svg" and path.parent.name == "images"
    root = _svg_root(path)
    points = _class_elements(root, "scene-point")
    assert len(points) == metric["n_valid"] > 0
    assert sorted((float(p.get("data-sabine")), float(p.get("data-raven"))) for p in points) == sorted(
        (o["sabine_s"], o["raven_s"]) for o in metric["observations"]
    )
    identity = _class_elements(root, "identity-line")
    assert len(identity) == 1
    values = [v for o in metric["observations"] for v in (o["sabine_s"], o["raven_s"])]
    low, high = float(identity[0].get("data-from")), float(identity[0].get("data-to"))
    assert low <= min(values) and high >= max(values)


def test_report_embeds_scene_mean_section_inside_acoustics(sample_run):
    import re

    data = load_run(sample_run)
    artifacts = build_analysis(data)
    report = artifacts["report"].read_text(encoding="utf-8")
    acoustics = re.search(r"<section><h2>Acoustics and sources</h2>.*?</section>", report, re.S).group(0)
    assert "<h3>RT30 scene means</h3>" in acoustics
    for key, metric in _scene_mean_metrics(artifacts).items():
        svg = artifacts[key].read_text(encoding="utf-8")
        assert svg in acoustics
        caption = f"{metric['population']}; unit: {metric['unit']}; n={metric['n_valid']}; excluded={metric['n_excluded']}."
        assert caption in acoustics
    assert "RT30 aligned comparison by central frequency" in acoustics


def test_scene_mean_chart_rendering_keeps_exports_and_status(sample_run):
    import copy

    from output_analyzer.loader import summary_for
    from output_analyzer.scene_distributions import scene_metrics

    data = load_run(sample_run)
    expected_means = copy.deepcopy(data.rt30_scene_means)
    expected_metrics = json.loads(json.dumps(scene_metrics(copy.deepcopy(data))))
    expected_status = summary_for(data)["status"]
    artifacts = build_analysis(data)
    assert json.loads(artifacts["rt30_scene_means_json"].read_text(encoding="utf-8")) == expected_means
    exported = [
        row for row in json.loads(artifacts["metrics_json"].read_text(encoding="utf-8"))
        if row["key"].startswith("rt30_scene_mean_")
    ]
    assert exported == expected_metrics
    summary = json.loads(artifacts["summary"].read_text(encoding="utf-8"))
    assert summary["status"] == expected_status
