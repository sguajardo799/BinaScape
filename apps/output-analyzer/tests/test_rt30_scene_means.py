from __future__ import annotations

import json
import csv

import pytest

from output_analyzer.loader import load_run
from output_analyzer.report import build_analysis


def edit_json(path, update):
    doc = json.loads(path.read_text(encoding="utf-8"))
    update(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


def manifest(run):
    return next((run / "metadata/manifests/scene").glob("*.json"))


def sidecar(run):
    return next((run / "output_audio/render").rglob("*.json"))


def test_complete_means_ignore_extra_bands_and_equivalent_variants(sample_run):
    edit_json(manifest(sample_run), lambda d: d["reverberation_sampling"]["rt30_by_band_s"].update({"8000": 99}))
    original = sidecar(sample_run)
    duplicate = json.loads(original.read_text())
    duplicate["hrtf_id"] = "other"
    (original.parent / "other__render.json").write_text(json.dumps(duplicate))
    data = load_run(sample_run)
    assert len(data.rt30_scene_means) == 1
    row = data.rt30_scene_means[0]
    assert row["selected_frequencies_hz"] == [125, 250]
    assert row["sabine_mean_s"] == 0.5
    assert row["raven_mean_s"] == 0.875
    assert row["difference_mean_s"] == 0.375
    assert row["sabine_n_valid_bands"] == row["raven_n_valid_bands"] == 2


@pytest.mark.parametrize("selection", [None, [], [125, 125], [125, -1], [True], ["125"], [float("nan")]])
def test_invalid_selection_is_unavailable(sample_run, selection):
    edit_json(manifest(sample_run), lambda d: d["reverberation_sampling"].update(mean_bands_hz=selection))
    row = load_run(sample_run).rt30_scene_means[0]
    assert row["band_group"] == "selection_unavailable"
    assert row["sabine_mean_s"] is row["raven_mean_s"] is None
    assert row["selection_exclusion_reason"]
    assert str(manifest(sample_run)) in row["provenance"]


@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf")])
def test_incomplete_sabine_does_not_suppress_raven(sample_run, value):
    edit_json(manifest(sample_run), lambda d: d["reverberation_sampling"]["rt30_by_band_s"].update({"125": value}))
    row = load_run(sample_run).rt30_scene_means[0]
    assert row["sabine_mean_s"] is None
    assert row["sabine_unavailable_frequencies_hz"] == [125]
    assert row["sabine_exclusion_reason"]
    assert row["raven_mean_s"] == 0.875
    assert row["difference_mean_s"] is None


def test_conflicted_band_excludes_complete_raven_not_sabine(sample_run):
    original = sidecar(sample_run)
    duplicate = json.loads(original.read_text())
    duplicate["hrtf_id"] = "other"
    duplicate["summary"]["room"]["reverberation"]["t30_s"][0] = 3
    (original.parent / "other__render.json").write_text(json.dumps(duplicate))
    data = load_run(sample_run)
    row = data.rt30_scene_means[0]
    assert row["sabine_mean_s"] == 0.5
    assert row["raven_mean_s"] is None
    assert row["raven_n_valid_bands"] == 1
    assert row["raven_unavailable_frequencies_hz"] == [250]
    assert row["raven_cause_ids"]
    assert len(row["provenance"]) == 3


def test_scene_without_render_retains_sabine_and_missing_band_reason(sample_run):
    sidecar(sample_run).unlink()
    row = load_run(sample_run).rt30_scene_means[0]
    assert row["sabine_mean_s"] == 0.5
    assert row["raven_mean_s"] is None
    assert row["raven_unavailable_frequencies_hz"] == [125, 250]


def test_missing_sabine_band_is_not_a_partial_complete_mean(sample_run):
    edit_json(manifest(sample_run), lambda d: d["reverberation_sampling"]["rt30_by_band_s"].pop("125"))
    row = load_run(sample_run).rt30_scene_means[0]
    assert row["sabine_mean_s"] is None
    assert row["sabine_n_valid_bands"] == 1


def test_scene_mean_exports_are_additive_and_round_trip_nulls(sample_run):
    edit_json(manifest(sample_run), lambda d: d["reverberation_sampling"]["rt30_by_band_s"].pop("125"))
    data = load_run(sample_run)
    artifacts = build_analysis(data)
    expected = data.rt30_scene_means
    assert json.loads(artifacts["rt30_scene_means_json"].read_text()) == expected
    assert [json.loads(line) for line in artifacts["rt30_scene_means_jsonl"].read_text().splitlines()] == expected
    with artifacts["rt30_scene_means_csv"].open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["sabine_mean_s"] == ""
    assert json.loads(rows[0]["selected_frequencies_hz"]) == [125, 250]
    summary = json.loads(artifacts["summary"].read_text())
    assert summary["rt30_scene_means"] == expected
    assert summary["rt30_matched_means"][0]["n_pairs"] == 1
    assert summary["rt30_matched_means"][0]["sabine_mean_s"] == 0.5
    for stem in ("rt30_pairs", "rt30_records", "rt30_consolidated"):
        assert json.loads(artifacts[f"{stem}_json"].read_text()) == getattr(data, stem)
