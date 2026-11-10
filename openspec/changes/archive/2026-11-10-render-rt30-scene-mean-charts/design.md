# Design

## Context

See proposal.md for motivation. Current state in `apps/output-analyzer/src/output_analyzer/`:

- `scene_distributions.scene_metrics(data)` returns, per band group, two `scene_histogram` metrics (Sabine, RAVEN) and one `scene_scatter` metric, all with `section="scene_means"`. They carry the final binning (`bin_edges_s`, `bin_counts`, `underflow_count`, `overflow_count`, `count_axis_max`), the sampling reference (`target_counts`, `edge_source`, `reference_unavailable_reason`) and the observations.
- `report._write_charts` appends these metrics at the end (`metrics.extend(scene_metrics(data))`) but writes no SVG for them, so `charts` has no entry for their keys.
- `report._write_report.grid(section)` looks up `charts[metric["key"]]` for every metric in a section, and the HTML calls only `grid("general")`, `grid("acoustics")` and `grid("quality")`. Calling `grid("scene_means")` today would raise `KeyError`.
- The generic `_write_histogram` re-bins values into 12 equal-width bins between the minimum and maximum. That contradicts the exported scene-mean bins, so it cannot be reused for these charts.
- `summary.json` records metrics without observations and does not list chart paths, so new SVGs do not change it.

## Goals / Non-Goals

**Goals:**
- Draw the scene-mean charts only from values already in the metric dictionaries. The chart is a pure projection of `metrics.json`.
- Keep the change inside `report.py`. The computation modules stay untouched.

**Non-Goals:**
- Changing binning, exclusion rules or sampling-context logic in `scene_distributions.py` or `loader.py`.
- Changing summary `status` semantics or the treatment of the optional `clarity_index.jsonl` incident.
- Restyling or refactoring existing charts.

## Decisions

1. **Dedicated writers driven by precomputed bins.** Add `_write_scene_histogram(path, metric)` and `_write_scene_scatter(path, metric)` in `report.py`, reusing `_svg_shell`, `_axes`, `_numeric_x_ticks`, `_numeric_y_ticks`, `_label`, `_write_empty_svg` and `_append_legend`. Bars are positioned by the real `bin_edges_s` on a linear x axis, so non-uniform edges also render correctly.
   - Alternative rejected: extending `_write_histogram` with an optional `edges`/`counts` path. That mixes two binning contracts in one function and risks regressing the existing histograms.

2. **Dispatch on `chart_type` in `_write_charts`.** After `scene_metrics(data)`, iterate over the returned metrics. Write `scene_histogram` and `scene_scatter` charts to `images/<key>.svg` and register them in `charts[key]`. This guarantees that every metric in `section="scene_means"` has a chart before `grid` runs.
   - Alternative rejected: making `grid` skip metrics without a chart. That would hide the same bug silently in the future.

3. **Underflow/overflow as side annotations.** Render them as labeled values (or narrow labeled bars) outside the `[first edge, last edge]` span, for example "< 0.2 s: 53" and "> 0.9 s: 29". The edge bins keep their exported counts.

4. **Sampling targets as reference marks.** When `target_counts` is not null, draw one horizontal tick or outlined marker per bin at the target height, in a color distinct from the bars, with its own legend entry. When it is null, add `reference_unavailable_reason` to the legend. The count axis uses `count_axis_max`, which already includes targets and under/overflow.

5. **Scatter with identity line.** Both axes share one range: the min and max over all Sabine and RAVEN values, with small padding. A y = x line is drawn, and the point count equals `n_valid`. Exclusions are reported only through the legend's `excluded=` count.

6. **Report placement.** Inside the existing "Acoustics and sources" `<section>`, add `<h3>RT30 scene means</h3>` with a one-line explanation (complete mean over selected bands; metadata, not WAV measurement) followed by `grid("scene_means")`. Captions use the same `grid` formatter, and scene metrics already carry `population`, `unit`, `n_valid`, `n_excluded` and `exclusion_rule`.

7. **Legend content.** It follows the existing legend contract checked by `test_every_exported_svg_has_visible_legend_and_metric_population` ("Population:", "excluded=", "unit:"), plus central frequencies from `selected_frequencies_hz` and "Bin edges: sampling_batch|data_derived".

## Risks / Trade-offs

- [1000-point scatter enlarges the inline SVG in `report.html`, which is already about 83 MB] → Use a compact `<circle>` with short attributes and no per-point titles. The added size is on the order of tens of KB, which is negligible.
- [Empty `bin_edges_s` when no observations exist (`shared_edges` returns `[]`)] → Fall back to `_write_empty_svg` with "No data / Not available", then append the legend. Covered by a test.
- [The `selection_unavailable` fallback group produces metrics with no observations] → Same empty-chart path. Keys include the band group, so file names do not collide.
- [Floating-point drift between bar position and tick labels] → Ticks are derived from the same edges list used to place the bars.
