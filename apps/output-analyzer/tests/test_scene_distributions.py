from __future__ import annotations

import copy

import pytest

from output_analyzer.loader import load_run
from output_analyzer.scene_distributions import sampling_context, scene_groups, shared_edges, bin_counts, scene_metrics


@pytest.fixture
def scoped_data(sample_run):
    data = load_run(sample_run)
    data.sampling_batches = [dict(
        manifest_count=1, accepted_realizations=1,
        bin_edges_s=[0.2, 0.6, 1.0],
        bins=[dict(index=1, target_count=2), dict(index=0, target_count=3)],
    )]
    return data


def test_scoped_batch_retains_explicit_edges_and_indexed_targets(scoped_data):
    context = sampling_context(scoped_data, scene_groups(scoped_data))
    assert context["bin_edges_s"] == [0.2, 0.6, 1.0]
    assert context["target_counts"] == [3, 2]
    assert context["edge_source"] == "sampling_batch"
    assert context["reference_unavailable_reason"] is None
    assert len(context["reference_provenance"]) == 2


@pytest.mark.parametrize("edges", [None, [], [0.2], [0.2, None], [1, 0.2], [0.2, 0.2], [0.2, float("inf")]])
def test_invalid_batch_edges_fall_back(scoped_data, edges):
    scoped_data.sampling_batches[0]["bin_edges_s"] = edges
    context = sampling_context(scoped_data, scene_groups(scoped_data))
    assert context["bin_edges_s"] == []
    assert context["edge_source"] == "data_derived"
    assert context["target_counts"] is None


@pytest.mark.parametrize("mutation", ["missing", "multiple", "coverage", "accepted", "ineligible", "groups"])
def test_ambiguous_scope_never_infers_targets(scoped_data, mutation):
    if mutation == "missing":
        scoped_data.sampling_batches = []
    elif mutation == "multiple":
        scoped_data.sampling_batches *= 2
    elif mutation == "coverage":
        scoped_data.sampling_batches[0]["manifest_count"] = 2
    elif mutation == "accepted":
        scoped_data.sampling_batches[0]["accepted_realizations"] = 2
    elif mutation == "ineligible":
        scoped_data.sampling_batches[0]["eligible"] = False
    else:
        row = copy.deepcopy(scoped_data.rt30_scene_means[0])
        row.update(scene_id="other", band_group="bands_500p0", selected_frequencies_hz=[500])
        scoped_data.rt30_scene_means.append(row)
    context = sampling_context(scoped_data, scene_groups(scoped_data))
    assert context["bin_edges_s"] == []
    assert context["target_counts"] is None
    assert context["reference_unavailable_reason"]


@pytest.mark.parametrize("bins", [None, [], [{"index": 0, "target_count": -1}, {"index": 1, "target_count": 2}], [{"index": 0, "target_count": 1}, {"index": 0, "target_count": 1}]])
def test_invalid_targets_do_not_discard_valid_edges(scoped_data, bins):
    scoped_data.sampling_batches[0]["bins"] = bins
    context = sampling_context(scoped_data, scene_groups(scoped_data))
    assert context["bin_edges_s"] == [0.2, 0.6, 1.0]
    assert context["target_counts"] is None
    assert context["reference_unavailable_reason"]


def test_all_exact_boundaries_and_tails_are_counted_once():
    edges = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    values = [0.1] + edges + [1.1]
    counts = bin_counts(values, edges)
    assert counts == dict(bin_counts=[1, 1, 1, 1, 1, 1, 1, 2], underflow_count=1, overflow_count=1)
    assert sum(counts["bin_counts"]) + counts["underflow_count"] + counts["overflow_count"] == len(values)


def test_fallback_edges_cover_union_not_each_method(scoped_data):
    row = scoped_data.rt30_scene_means[0]
    row.update(sabine_mean_s=0.1, raven_mean_s=1.5)
    edges = shared_edges([row], {"bin_edges_s": []})
    assert len(edges) == 13
    assert edges[0] == 0.1 and edges[-1] == 1.5
    assert bin_counts([0.1], edges)["bin_counts"][0] == 1
    assert bin_counts([1.5], edges)["bin_counts"][-1] == 1


def test_constant_and_empty_fallback(scoped_data):
    row = scoped_data.rt30_scene_means[0]
    row.update(sabine_mean_s=0.5, raven_mean_s=0.5)
    edges = shared_edges([row], {"bin_edges_s": []})
    assert edges[0] == 0.475 and edges[-1] == 0.525
    assert sum(bin_counts([0.5, 0.5], edges)["bin_counts"]) == 2
    row.update(sabine_mean_s=None, raven_mean_s=None)
    assert shared_edges([row], {"bin_edges_s": []}) == []
    assert bin_counts([], []) == dict(bin_counts=[], underflow_count=0, overflow_count=0)


def test_scene_metrics_reconstruct_counts_points_and_exclusions(scoped_data):
    row = copy.deepcopy(scoped_data.rt30_scene_means[0])
    row.update(scene_id="partial", raven_mean_s=None, raven_exclusion_reason="missing band")
    scoped_data.rt30_scene_means.append(row)
    sabine, raven, scatter = scene_metrics(scoped_data)
    for panel in (sabine, raven):
        reconstructed = bin_counts([r["value"] for r in panel["observations"]], panel["bin_edges_s"])
        assert all(panel[key] == value for key, value in reconstructed.items())
        assert panel["n_valid"] + panel["n_excluded"] == 2
        assert panel["selected_frequencies_hz"] == [125, 250]
    assert sabine["n_valid"] == 2
    assert raven["n_excluded"] == 1
    assert raven["exclusions"][0]["reason"] == "missing band"
    assert scatter["n_valid"] == scatter["n_excluded"] == 1
    point = scatter["observations"][0]
    assert point["sabine_s"] == scoped_data.rt30_scene_means[0]["sabine_mean_s"]
    assert point["raven_s"] == scoped_data.rt30_scene_means[0]["raven_mean_s"]
    before = scene_metrics(scoped_data)
    scoped_data.rt30_records.extend(dict(r, value_s=99) for r in list(scoped_data.rt30_records))
    assert scene_metrics(scoped_data) == before


def test_group_keys_are_deterministic_and_selections_are_not_pooled(scoped_data):
    row = copy.deepcopy(scoped_data.rt30_scene_means[0])
    row.update(scene_id="other", band_group="bands_500p0", selected_frequencies_hz=[500])
    scoped_data.rt30_scene_means.append(row)
    before = scene_metrics(scoped_data)
    scoped_data.rt30_scene_means.reverse()
    after = scene_metrics(scoped_data)
    assert [r["key"] for r in before] == [r["key"] for r in after]
    assert len(before) == 6
    assert all(r["n_valid"] == 1 and r["target_counts"] is None for r in before)
