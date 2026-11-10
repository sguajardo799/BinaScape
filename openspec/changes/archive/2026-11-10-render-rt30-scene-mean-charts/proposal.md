# Proposal

## Why

The output analyzer already computes complete per-scene RT30 means and exposes them as auditable metrics (`rt30_scene_mean_sabine_*`, `rt30_scene_mean_raven_*`, `rt30_scene_mean_scatter_*` in `metrics.json`), but no chart is ever drawn and `report.html` never shows them. On `outputs/directivity_experiment101` all 1000 scenes have complete Sabine and RAVEN means, yet the report contains zero references to them, so the main sampling-validation figure (mean RT distribution versus sampling quotas) is missing.

## What Changes

- Render one SVG per `scene_histogram` metric (Sabine and RAVEN) in `analysis/images/`, drawn from the metric's own `bin_edges_s`, `bin_counts`, `underflow_count`, `overflow_count` and `count_axis_max`, without re-binning.
- When the metric carries sampling `target_counts`, overlay them on the histogram so observed counts can be compared with the sampling quotas.
- Render one SVG per `scene_scatter` metric (Sabine versus RAVEN complete scene means) with an identity reference line.
- Add a "RT30 scene means" subsection to the "Acoustics and sources" section of `report.html` that embeds these charts with their population, n, excluded count, unit and exclusion rule.
- Ensure that a population with no valid observations renders an explicit "No data / Not available" chart instead of failing.
- Non-goal: the summary `status` semantics (`partial` whenever any incident exists, including a missing optional `clarity_index.jsonl`) are unchanged by this change.

## Capabilities

### New Capabilities
- `output-analyzer-rt30-scene-distributions`: how the output analyzer presents complete per-scene RT30 mean distributions (histograms, sampling-quota reference and Sabine/RAVEN scatter) in its image exports and HTML report.

### Modified Capabilities
<!-- None: no existing spec covers the output analyzer. -->

## Impact

- Code: `apps/output-analyzer/src/output_analyzer/report.py` (chart writers, `_write_charts`, `_write_report`). `scene_distributions.py` and `loader.py` stay authoritative for values and are not expected to change.
- Outputs: new `images/rt30_scene_mean_*.svg` files and a new report subsection. Existing exports (`metrics.*`, `rt30_scene_means.*`, `summary.json`) keep their current shape and values.
- Tests: `apps/output-analyzer/tests/test_report.py`. The existing "every exported SVG has a visible legend" test will cover the new SVGs.
- No cross-component dependency: orchestrator, MATLAB and Clarity outputs are only read.
