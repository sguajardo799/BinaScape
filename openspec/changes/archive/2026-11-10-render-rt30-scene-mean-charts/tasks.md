# Tasks

All work is in `apps/output-analyzer`. Run tests with `python -m pytest` from `apps/output-analyzer`.

## 1. Output analyzer — scene-mean histogram chart

- [x] 1.1 Add a failing test in `tests/test_report.py` that builds the analysis for `sample_run` and asserts `images/rt30_scene_mean_sabine_<group>.svg` and `..._raven_<group>.svg` exist, with one bar per exported `bin_counts` entry. Verify it fails before 1.2.
- [x] 1.2 Implement `_write_scene_histogram` in `report.py`. Bars come from `bin_edges_s`/`bin_counts` on the `count_axis_max` scale, with labeled underflow/overflow and target marks when `target_counts` is present. Dispatch it from `_write_charts` for `chart_type == "scene_histogram"` and register `charts[key]`. Verify the 1.1 test passes.
- [x] 1.3 Add tests for underflow/overflow labels (non-zero counts visible, edge bins unchanged), for target marks present versus absent with `reference_unavailable_reason` in the legend, and for the empty-population "No data / Not available" chart. Verify they pass.

## 2. Output analyzer — scene-mean scatter chart

- [x] 2.1 Add a failing test asserting `images/rt30_scene_mean_scatter_<group>.svg` exists, contains `n_valid` points and an identity line. Verify it fails first.
- [x] 2.2 Implement `_write_scene_scatter` with shared axis range and a y = x line, dispatched for `chart_type == "scene_scatter"`, plus the empty-population path. Verify the 2.1 test passes.

## 3. Output analyzer — legends and report section

- [x] 3.1 Append legends to every scene-mean SVG (population, n, excluded, unit, central frequencies, bin edge source). Verify `test_every_exported_svg_has_visible_legend_and_metric_population` passes and add an assertion for the frequencies and edge source on `rt30_scene_mean_*` files.
- [x] 3.2 Add the "RT30 scene means" `<h3>` and `grid("scene_means")` inside the "Acoustics and sources" section of `_write_report`. Add a test asserting the heading and the inline SVG of each scene-mean metric appear in `report.html`, and that the test passes.
- [x] 3.3 Add a regression test that `rt30_scene_means.json`, the `rt30_scene_mean_*` entries of `metrics.json` and `summary.json` `status` are unchanged by chart rendering. Verify the full suite `python -m pytest` passes.

## 4. Integration check

- [x] 4.1 Run `output-analyzer analyze outputs/directivity_experiment101 --output-dir <scratch dir>` outside the run folder. Confirm the three `rt30_scene_mean_*.svg` files exist, `report.html` shows the "RT30 scene means" subsection, the Sabine bars match the quotas (143/143/143/142/143/143/143), and RAVEN shows underflow 53 / overflow 29.

## Workflow follow-up

- Archive the change after review and merge into `develop`.
