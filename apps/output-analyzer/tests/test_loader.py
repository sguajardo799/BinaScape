from __future__ import annotations

import json

import pytest

from output_analyzer.loader import load_run, summary_for


def documents(run):
    return (
        next((run / "metadata/manifests/scene").glob("*.json")),
        next((run / "output_audio/render").rglob("*__render.json")),
    )


def mutate(path, update):
    data = json.loads(path.read_text())
    update(data)
    path.write_text(json.dumps(data))


def test_current_contract_and_zero_values(sample_run):
    data = load_run(sample_run)
    assert len(data.scenes) == 1
    assert len(data.sources) == 2
    assert data.scenes[0]["room_area_m2"] == 48
    assert data.sources[0]["gain_db"] == 0
    assert data.sources[0]["start_time_s"] == 0
    assert summary_for(data)["unique_scene_count"] == 1
    assert data.degraded_records[0]["ear_asymmetry_db"] == 5


def test_singleton_and_duplicate_variants(sample_run):
    scene, render = documents(sample_run)
    mutate(scene, lambda x: x.update(sources=x["sources"][:1]))
    mutate(render, lambda x: x["summary"].update(sources=x["summary"]["sources"][0]))
    first = load_run(sample_run)
    mutate(render, lambda x: x["summary"].update(sources=[x["summary"]["sources"]]))
    assert load_run(sample_run).sources == first.sources
    duplicate = render.with_name("copy__render.json")
    duplicate.write_text(render.read_text())
    data = load_run(sample_run)
    assert len(data.render_scenes) == 1
    assert len(data.sources) == 1
    assert summary_for(data)["incident_counts"]["duplicate_identity"] >= 1
    mutate(duplicate, lambda x: x.update(hrtf_id="other"))
    assert len(load_run(sample_run).render_scenes) == 2
    assert len(load_run(sample_run).sources) == 1


def test_l_shape_and_missing_orientation(sample_run):
    scene, _ = documents(sample_run)

    def change(x):
        x["room"]["geometry"].update(
            type="l_shape",
            footprint_vertices_m=[[0, 0], [4, 0], [4, 2], [2, 2], [2, 4], [0, 4]],
            wall_ids=[f"w{i}" for i in range(6)],
        )
        x["sources"][0]["position_m"] = [3, 1, 3]
        del x["receiver"]["orientation_deg"]

    mutate(scene, change)
    data = load_run(sample_run)
    assert data.scenes[0]["room_area_m2"] == 12
    assert data.sources[0]["inside_footprint"] is False
    assert data.sources[0]["receiver_relative_doa_deg"] is None


def test_frequency_alignment_and_conflicting_room_results(sample_run):
    _, render = documents(sample_run)
    data = load_run(sample_run)
    assert [(r["frequency_hz"], r["difference_s"]) for r in data.rt30_pairs] == [
        (125.0, 0.25),
        (250.0, 0.5),
    ]
    duplicate = render.with_name("other__render.json")
    doc = json.loads(render.read_text())
    doc["hrtf_id"] = "other"
    doc["summary"]["room"]["reverberation"]["t30_s"][0] = 9
    duplicate.write_text(json.dumps(doc))
    data = load_run(sample_run)
    assert [r["frequency_hz"] for r in data.rt30_pairs] == [125.0]
    assert any(i["category"] == "rt30_conflict" for i in data.incidents)


@pytest.mark.parametrize("schema", ["2.0", "4.0", None])
def test_bad_document_does_not_discard_independent_scene(sample_run, schema):
    _, render = documents(sample_run)
    mutate(render, lambda x: x.update(schema_version=schema))
    data = load_run(sample_run)
    assert len(data.scenes) == 1
    assert not data.render_scenes
    assert summary_for(data)["status"] == "partial"


def test_malformed_metadata_continues(sample_run):
    scene, render = documents(sample_run)
    mutate(render, lambda x: x["summary"].update(sources="invalid"))
    (scene.parent / "broken.json").write_text("{")
    indexes = sample_run / "metadata/indexes"
    indexes.mkdir(exist_ok=True)
    (indexes / "render_index.jsonl").write_text("[]\n{\n")
    data = load_run(sample_run)
    assert len(data.sources) == 2
    assert summary_for(data)["incident_counts"]["invalid_jsonl"] == 2


def test_missing_root_is_operational(tmp_path):
    with pytest.raises(OSError):
        load_run(tmp_path / "absent")


def test_no_valid_data(tmp_path):
    assert summary_for(load_run(tmp_path))["status"] == "no_analyzable_data"


def test_raven_fallback_and_unknown_frame(sample_run):
    scene, render = documents(sample_run)
    mutate(scene, lambda x: x.update(sources=[]))

    def raven(x):
        x["summary"]["render_backend"] = "RAVEN + ITA"
        for source in x["summary"]["sources"]:
            source["position_m"][2] *= -1
            source["source_start_time_s"] = source.pop("start_time_s")

    mutate(render, raven)
    data = load_run(sample_run)
    assert data.sources[0]["source_z_m"] == 2
    assert "RAVEN" in data.sources[0]["position_provenance"]
    mutate(render, lambda x: x["summary"].update(render_backend="unknown"))
    data = load_run(sample_run)
    assert data.sources[0]["source_z_m"] is None
    assert any(i["category"] == "unknown_coordinate_frame" for i in data.incidents)


def test_nonfinite_metadata_exports_and_coincident_angles(sample_run, tmp_path):
    from output_analyzer.report import build_analysis

    scene, _ = documents(sample_run)

    def change(x):
        x["sources"][0]["position_m"] = x["receiver"]["position_m"]
        x["sources"][0]["gain_db"] = float("nan")
        x["sampling"] = {"attempts": float("inf")}

    mutate(scene, change)
    data = load_run(sample_run)
    assert data.sources[0]["distance_m"] == 0
    assert data.sources[0]["global_doa_deg"] is None
    assert data.sources[0]["elevation_deg"] is None
    assert data.sources[0]["gain_db"] is None
    assert build_analysis(data, tmp_path / "result")["report"].exists()


def test_duplicate_band_and_missing_band_exclusions(sample_run):
    _, render = documents(sample_run)
    mutate(
        render,
        lambda x: x["summary"]["room"]["reverberation"].update(
            band_frequencies_hz=[125, 125], t30_s=[0.75, 0.75]
        ),
    )
    data = load_run(sample_run)
    assert not data.rt30_pairs
    assert len(data.rt30_comparisons) == 2
    assert summary_for(data)["populations"]["rt30_difference_s"]["n_excluded"] == 2


def test_conflicting_field_preserves_other_metrics(sample_run):
    scene, _ = documents(sample_run)
    duplicate = scene.with_name("duplicate.json")
    duplicate.write_text(scene.read_text())
    mutate(duplicate, lambda x: x["receiver"].update(orientation_deg={"yaw": 90}))
    data = load_run(sample_run)
    assert data.scenes[0]["room_area_m2"] == 48
    assert data.sources[0]["distance_m"] is not None
    assert data.sources[0]["receiver_relative_doa_deg"] is None


def test_metadata_only_never_opens_referenced_assets(sample_run, monkeypatch):
    from pathlib import Path

    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.suffix not in {".wav", ".mat", ".daff"}
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    assert len(load_run(sample_run).sources) == 2


def test_malformed_geometry_type_sources_and_index_encoding(sample_run):
    scene, _ = documents(sample_run)

    def change(x):
        x["room"]["geometry"]["type"] = []
        x["sources"] = "invalid"

    mutate(scene, change)
    indexes = sample_run / "metadata/indexes"
    indexes.mkdir(exist_ok=True)
    (indexes / "render_index.jsonl").write_bytes(b"\xff")
    data = load_run(sample_run)
    assert data.scenes[0]["n_sources"] is None
    assert data.scenes[0]["room_area_m2"] is None
    assert any(i["category"] == "invalid_index" for i in data.incidents)


def test_huge_integer_and_degraded_ambiguous_band(sample_run):
    scene, _ = documents(sample_run)
    mutate(scene, lambda x: x["sources"][0].update(gain_db=10**400))
    degraded = next((sample_run / "output_audio/degraded").rglob("*.json"))

    def change(x):
        x["job_id"] = []
        x["degradation_applied"]["left"]["loss_db_by_band"]["250.0"] = 90

    mutate(degraded, change)
    data = load_run(sample_run)
    assert data.sources[0]["gain_db"] is None
    assert data.degraded_records[0]["job_id"] is None
    assert data.degraded_records[0]["common_frequencies_hz"] == [500, 1000]


def test_overlapping_polygon_is_excluded(sample_run):
    scene, _ = documents(sample_run)
    mutate(
        scene,
        lambda x: x["room"]["geometry"].update(
            type="l_shape",
            footprint_vertices_m=[[0, 0], [4, 0], [2, 0], [2, 2], [0, 2]],
            wall_ids=["a", "b", "c", "d", "e"],
        ),
    )
    assert load_run(sample_run).scenes[0]["room_area_m2"] is None


def test_source_conflict_does_not_drop_unaffected_sources(sample_run):
    scene, _ = documents(sample_run)
    duplicate = scene.with_name("duplicate.json")
    duplicate.write_text(scene.read_text())
    mutate(duplicate, lambda x: x["sources"][0].update(gain_db=5))
    data = load_run(sample_run)
    assert len(data.sources) == 2
    assert data.sources[0]["gain_db"] is None
    assert data.sources[0]["source_x_m"] == 2
    assert data.sources[1]["gain_db"] == -2


def test_invalid_rt30_values_retained_for_denominators(sample_run):
    _, render = documents(sample_run)
    mutate(
        render, lambda x: x["summary"]["room"]["reverberation"].update(t30_s=[-1, 0.75])
    )
    data = load_run(sample_run)
    raven = [r for r in data.rt30_records if r["method"] == "RAVEN rpf.getT30"]
    assert len(raven) == 2
    assert sum(r["value_s"] is None for r in raven) == 1


@pytest.mark.parametrize("base", [0.1, 1.0, 10.0])
@pytest.mark.parametrize("factor,conflict", [(0, False), (0.999, False), (1.001, True)])
def test_rt30_specific_tolerance_boundaries(sample_run, base, factor, conflict):
    _, render = documents(sample_run)
    mutate(
        render,
        lambda d: d["summary"]["room"]["reverberation"].update(t30_s=[base, base]),
    )
    duplicate = render.with_name("second__render.json")
    duplicate.write_text(render.read_text())

    def alter(d):
        d["hrtf_id"] = "second"
        d["summary"]["room"]["reverberation"]["t30_s"][0] = base + factor * max(
            1e-6, 1e-6 * base
        )

    mutate(duplicate, alter)
    data = load_run(sample_run)
    assert any(i["category"] == "rt30_conflict" for i in data.incidents) == conflict
    band = next(r for r in data.rt30_consolidated if r["frequency_hz"] == 250)
    assert band["n_variants"] == 2
    assert band["spread_s"] == pytest.approx(factor * max(1e-6, 1e-6 * base))
    assert (band["value_s"] is None) == conflict
    assert len(data.rt30_pairs) == (1 if conflict else 2)
    assert all(
        r["eligible_for_comparison"]
        for r in data.rt30_records
        if r["method"] == "Sabine estimate"
    )
    if conflict:
        pair = next(r for r in data.rt30_comparisons if r["frequency_hz"] == 250)
        incident = next(i for i in data.incidents if i["category"] == "rt30_conflict")
        assert pair["cause_id"] == incident["incident_id"]


def test_rt30_equivalence_not_transitive_and_duplicate_does_not_weight(sample_run):
    _, render = documents(sample_run)
    mutate(
        render, lambda d: d["summary"]["room"]["reverberation"].update(t30_s=[0.1, 0.1])
    )
    for index, offset in enumerate([0.75e-6, 1.5e-6], 1):
        doc = json.loads(render.read_text())
        doc["hrtf_id"] = f"h{index}"
        doc["summary"]["room"]["reverberation"]["t30_s"][0] += offset
        render.with_name(f"h{index}__render.json").write_text(json.dumps(doc))
    data = load_run(sample_run)
    assert len(data.rt30_pairs) == 1
    before = data.rt30_consolidated
    render.with_name("copy__render.json").write_text(render.read_text())
    after = load_run(sample_run).rt30_consolidated
    assert [{k: v for k, v in row.items() if k != "provenance"} for row in after] == [
        {k: v for k, v in row.items() if k != "provenance"} for row in before
    ]


def test_rt30_missing_variant_band_keeps_valid_support(sample_run):
    _, render = documents(sample_run)
    doc = json.loads(render.read_text())
    doc["hrtf_id"] = "h2"
    doc["summary"]["room"]["reverberation"].update(
        band_frequencies_hz=[125], t30_s=[0.75]
    )
    render.with_name("h2__render.json").write_text(json.dumps(doc))
    data = load_run(sample_run)
    assert len(data.rt30_pairs) == 2
    band = next(r for r in data.rt30_consolidated if r["frequency_hz"] == 250)
    assert band["n_variants"] == 1
    assert band["n_variants_excluded"] == 1


def test_rt30_reordered_bands_preserve_mean_and_provenance(sample_run):
    _, render = documents(sample_run)
    doc = json.loads(render.read_text())
    doc["hrtf_id"] = "second"
    doc["summary"]["room"]["reverberation"].update(
        band_frequencies_hz=[125, 250], t30_s=[0.7500001, 1.0000001]
    )
    render.with_name("second__render.json").write_text(json.dumps(doc))
    data = load_run(sample_run)
    assert [r["raven_s"] for r in data.rt30_pairs] == pytest.approx(
        [0.75000005, 1.00000005]
    )
    assert all(
        r["n_variants"] == 2 and len(r["provenance"]) == 2 for r in data.rt30_pairs
    )
    assert data.general_parameters["rt30_numerical_tolerance"]["absolute_s"] == 1e-6


def test_cli_summarizes_causal_issues_and_keeps_export_details(
    sample_run, tmp_path, capsys
):
    from output_analyzer.cli import main

    _, render = documents(sample_run)
    doc = json.loads(render.read_text())
    doc["hrtf_id"] = "second"
    doc["summary"]["room"]["reverberation"]["t30_s"][0] = 9
    render.with_name("second__render.json").write_text(json.dumps(doc))
    result = tmp_path / "report"
    assert main(["analyze", str(sample_run), "--output-dir", str(result)]) == 0
    output = capsys.readouterr().out
    assert "1 excluded RT30 pairs" in output
    assert "rt30_conflict=1" in output
    assert "Sample identities:" in output
    assert "incidents.jsonl" in output
    assert len(output.splitlines()) <= 12
    incidents = [
        json.loads(line)
        for line in (result / "incidents.jsonl").read_text().splitlines()
    ]
    root = next(i for i in incidents if i["category"] == "rt30_conflict")
    child = next(i for i in incidents if i["category"] == "rt30_not_comparable")
    assert child["cause_id"] == root["incident_id"]


def test_invalid_variant_and_material_conflict_keep_distinct_causes(sample_run):
    _, render = documents(sample_run)
    mutate(
        render, lambda d: d["summary"]["room"]["reverberation"].update(t30_s=[-1, 0.75])
    )
    for index, value in enumerate([1, 2]):
        doc = json.loads(render.read_text())
        doc["hrtf_id"] = f"extra{index}"
        doc["summary"]["room"]["reverberation"]["t30_s"][0] = value
        render.with_name(f"extra{index}__render.json").write_text(json.dumps(doc))
    data = load_run(sample_run)
    conflict = next(i for i in data.incidents if i["category"] == "rt30_conflict")
    excluded = next(i for i in data.incidents if i["category"] == "rt30_not_comparable")
    invalid = next(i for i in data.incidents if i["category"] == "rt30_invalid_band")
    unavailable = next(
        i for i in data.incidents if i["category"] == "rt30_variant_band_unavailable"
    )
    assert excluded["cause_id"] == conflict["incident_id"]
    assert unavailable["cause_id"] == invalid["incident_id"]
    assert invalid["entity"].startswith("variant:")
