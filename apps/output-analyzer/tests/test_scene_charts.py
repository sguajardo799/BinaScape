from __future__ import annotations

import xml.etree.ElementTree as ET
import copy

import pytest

from output_analyzer.loader import load_run
from output_analyzer.report import _write_scene_histogram, _write_scene_scatter, build_analysis
from output_analyzer.scene_distributions import scene_metrics

NS = {"svg": "http://www.w3.org/2000/svg"}


def configured_metrics(sample_run):
    data = load_run(sample_run)
    data.sampling_batches = [dict(
        manifest_count=1, accepted_realizations=1, bin_edges_s=[0.2, 0.6, 1.0],
        bins=[dict(index=0, target_count=125), dict(index=1, target_count=125)],
    )]
    data.rt30_scene_means[0]["raven_mean_s"] = 1.5
    return scene_metrics(data)


def test_histogram_scales_labels_tails_targets_and_legends(sample_run, tmp_path):
    metrics = configured_metrics(sample_run)
    scales = []
    for row in metrics[:2]:
        path = tmp_path / f"{row['method']}.svg"
        _write_scene_histogram(path, row)
        root = ET.parse(path).getroot()
        text = " ".join(root.itertext())
        scales.append(root.find("svg:g[@class='scene-histogram']", NS).get("data-count-max"))
        assert "Selected bands (Hz): 125, 250" in text
        assert "underflow" in text and "overflow" in text
        assert "[0.2, 0.6)" in text and "[0.6, 1]" in text
        legend = root.find("svg:g[@class='chart-legend']", NS)
        assert "Population: unique scenes; n=1; excluded=0; unit: s." in " ".join(legend.itertext())
        bins = root.findall(".//svg:rect[@class='scene-bin']", NS)
        assert sum(int(b.get("data-count")) for b in bins) == 1
        targets = root.findall(".//svg:line[@class='planned-target']", NS)
        assert len(targets) == (2 if row["method"] == "sabine" else 0)
        if row["method"] == "raven":
            assert bins[-1].get("data-count") == "1"
            assert "not constrained" in text
    assert scales == ["125", "125"]


def test_empty_histogram_keeps_unavailable_legend_and_no_bars(sample_run, tmp_path):
    data = load_run(sample_run)
    data.rt30_scene_means[0].update(sabine_mean_s=None, raven_mean_s=None)
    row = scene_metrics(data)[0]
    path = tmp_path / "empty.svg"
    _write_scene_histogram(path, row)
    root = ET.parse(path).getroot()
    assert not root.findall(".//svg:rect[@class='scene-bin']", NS)
    text = " ".join(root.itertext())
    assert "Not available" in text and "n=0; excluded=1" in text


@pytest.mark.parametrize("values", [(0.5, 1.5), (0.5, 0.5), (None, 0.5)])
def test_scatter_points_ranges_constant_and_unavailable(sample_run, tmp_path, values):
    data = load_run(sample_run)
    row = data.rt30_scene_means[0]
    row.update(sabine_mean_s=values[0], raven_mean_s=values[1])
    partial = copy.deepcopy(row)
    partial.update(scene_id="excluded", raven_mean_s=None, raven_exclusion_reason="missing band")
    data.rt30_scene_means.append(partial)
    metric = scene_metrics(data)[2]
    path = tmp_path / "scatter.svg"
    _write_scene_scatter(path, metric)
    root = ET.parse(path).getroot()
    points = root.findall(".//svg:circle[@class='scene-point']", NS)
    assert len(points) == (1 if values[0] is not None else 0)
    text = " ".join(root.itertext())
    assert "Sabine complete mean RT30 (s)" in text
    assert "RAVEN complete mean T30 (s)" in text
    assert f"excluded={metric['n_excluded']}" in text
    if points:
        group = root.find("svg:g[@class='scene-scatter']", NS)
        assert group.get("data-scale-x") == group.get("data-scale-y")
        low, high = float(group.get("data-axis-min")), float(group.get("data-axis-max"))
        assert low < min(values) and high > max(values)
        assert root.find(".//svg:line[@class='equality-line']", NS) is not None
        assert "Equality line: RAVEN = Sabine" in text
    else:
        assert "Not available" in text


def test_report_embeds_scene_charts_before_frequency_diagnostics(sample_run):
    artifacts = build_analysis(load_run(sample_run))
    report = artifacts["report"].read_text(encoding="utf-8")
    assert report.index('id="rt30-scene-means"') < report.index("RT30 aligned comparison by central frequency")
    assert "pooled scene/frequency values" in report
    assert "<img " not in report
    keys = [key for key in artifacts if key.startswith("rt30_scene_mean_")]
    assert len(keys) == 3
    for key in keys:
        assert artifacts[key].read_text(encoding="utf-8") in report
        assert "bands_125p0_250p0" in key
    for key in ("rt30_sabine_estimate", "rt30_raven_rpf_gett30", "rt30_difference"):
        assert artifacts[key].name == key + ".svg"


def test_empty_report_embeds_all_scene_unavailable_states(tmp_path):
    artifacts = build_analysis(load_run(tmp_path))
    report = artifacts["report"].read_text(encoding="utf-8")
    assert 'id="rt30-scene-means"' in report
    keys = [key for key in artifacts if key.startswith("rt30_scene_mean_")]
    assert len(keys) == 3
    for key in keys:
        svg = artifacts[key].read_text(encoding="utf-8")
        assert "Not available" in svg and svg in report
