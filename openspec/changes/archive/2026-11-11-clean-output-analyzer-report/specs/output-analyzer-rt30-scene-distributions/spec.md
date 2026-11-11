# Spec Delta

## MODIFIED Requirements

### Requirement: Scene-mean section in the HTML report
`report.html` SHALL include a "RT30 scene means" subsection within "Acoustics and sources" that embeds every scene-mean histogram and scatter SVG, each with a caption stating population, unit, n, excluded count and exclusion rule. Other report sections SHALL follow the `output-analyzer-report` capability.

#### Scenario: Report embeds scene-mean charts
- **WHEN** the analyzer completes on a run with complete scene means
- **THEN** `report.html` contains the "RT30 scene means" heading and the inline SVG of each `rt30_scene_mean_*` metric

#### Scenario: Existing sections unaffected
- **WHEN** the report is generated after this change
- **THEN** the general, acoustics, positions-by-geometry, coverage and data-export sections render the charts and tables defined by `output-analyzer-report`, and the "RT30 scene means" subsection keeps every scene-mean chart
