# output-analyzer-rt30-scene-distributions Specification

## Purpose

Defines how the output analyzer presents complete per-scene RT30 mean distributions (Sabine estimate and RAVEN consolidated T30) as image exports and in the HTML report, so sampling quotas can be audited visually against the metadata-derived means.

## Requirements

### Requirement: Scene-mean histogram image per method
For every metric in `metrics.json` with `chart_type` `scene_histogram`, the analyzer SHALL write an SVG named `<metric key>.svg` in the analysis `images/` directory. Its bars SHALL use the metric's exported `bin_edges_s` and `bin_counts` exactly, without re-binning, and its count axis SHALL span the metric's `count_axis_max`.

#### Scenario: Complete run with sampling batch edges
- **WHEN** the analyzer runs on a run whose scene-mean metrics carry `edge_source` `sampling_batch`, `bin_edges_s` `[0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]` and seven `bin_counts`
- **THEN** `images/rt30_scene_mean_sabine_<band_group>.svg` and `images/rt30_scene_mean_raven_<band_group>.svg` exist, each drawing seven bars whose heights correspond to that method's `bin_counts`

#### Scenario: Sabine and RAVEN share one count scale
- **WHEN** both histograms of one band group are rendered
- **THEN** both use the same `count_axis_max` for their count axis, so bar heights are comparable between methods

### Requirement: Out-of-range scene means are visible
A scene-mean histogram SHALL show its `underflow_count` and `overflow_count` as labeled values whenever either is greater than zero, and SHALL NOT silently drop them or fold them into the edge bins.

#### Scenario: RAVEN means outside the sampling range
- **WHEN** the RAVEN metric reports `underflow_count` 53 and `overflow_count` 29
- **THEN** the RAVEN histogram shows both counts with labels identifying them as below and above the bin range, and the edge bins keep their exported `bin_counts`

### Requirement: Sampling quota reference on scene-mean histograms
When a scene-mean histogram metric carries non-null `target_counts`, the chart SHALL draw each bin's target count as a reference mark distinct from the observed bars and identify it in the legend. When `target_counts` is null, the chart SHALL omit the reference marks and state `reference_unavailable_reason` in the legend.

#### Scenario: Quotas available
- **WHEN** a histogram metric has `target_counts` `[143, 143, 143, 142, 143, 143, 143]`
- **THEN** the chart shows one target mark per bin at those counts and the legend names the marks as sampling target counts

#### Scenario: Quotas unavailable
- **WHEN** a histogram metric has `target_counts` null and `reference_unavailable_reason` "Sampling batch scope unavailable or ambiguous"
- **THEN** the chart has no target marks and its legend contains that reason

### Requirement: Scene-mean scatter image
For every metric with `chart_type` `scene_scatter`, the analyzer SHALL write `images/<metric key>.svg` that plots one point per paired observation, with Sabine mean on one axis and RAVEN mean on the other, both in seconds, and a y = x identity reference line.

#### Scenario: Paired complete means
- **WHEN** the scatter metric has 1000 observations with `sabine_s` and `raven_s`
- **THEN** the SVG contains 1000 points and an identity line spanning the shared axis range

### Requirement: Scene-mean chart legends
Every scene-mean SVG SHALL contain a visible legend group with the metric's population, `n_valid`, `n_excluded`, unit, the band group's selected central frequencies, and the bin edge source (`sampling_batch` or `data_derived`).

#### Scenario: Legend audit
- **WHEN** any `rt30_scene_mean_*.svg` is parsed
- **THEN** its legend text contains "Population:", "excluded=", "unit:", the central frequencies in Hz, and the edge source

### Requirement: Scene-mean section in the HTML report
`report.html` SHALL include a "RT30 scene means" subsection within "Acoustics and sources" that embeds every scene-mean histogram and scatter SVG, each with a caption stating population, unit, n, excluded count and exclusion rule. Other report sections SHALL follow the `output-analyzer-report` capability.

#### Scenario: Report embeds scene-mean charts
- **WHEN** the analyzer completes on a run with complete scene means
- **THEN** `report.html` contains the "RT30 scene means" heading and the inline SVG of each `rt30_scene_mean_*` metric

#### Scenario: Existing sections unaffected
- **WHEN** the report is generated after this change
- **THEN** the general, acoustics, positions-by-geometry, coverage and data-export sections render the charts and tables defined by `output-analyzer-report`, and the "RT30 scene means" subsection keeps every scene-mean chart

### Requirement: Empty scene-mean populations render explicitly
When a scene-mean metric has no valid observations, the analyzer SHALL still write its SVG with a "No data / Not available" message and complete the report without error.

#### Scenario: No complete scene means
- **WHEN** every scene lacks a complete mean for a method, or no selected-band group is available
- **THEN** the corresponding SVG states "No data / Not available", the report is written, and the analyzer exits successfully

### Requirement: Scene-mean exports and status unchanged
Rendering scene-mean charts SHALL NOT change the values or shape of `rt30_scene_means.*`, `metrics.*` or `summary.json`, and SHALL NOT change how the summary `status` is derived.

#### Scenario: Export stability
- **WHEN** the analyzer is run on the same input before and after this change
- **THEN** `rt30_scene_means.json`, the `rt30_scene_mean_*` entries of `metrics.json` and `summary.json` `status` are identical
