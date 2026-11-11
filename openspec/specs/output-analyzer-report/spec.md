# output-analyzer-report Specification

## Purpose

Defines what the output analyzer's `report.html` and `images/` SVGs present, so the report stays readable and bounded in size for large runs while every value remains auditable through the exported data files.

## Requirements

### Requirement: Charts shown in the report
`report.html` and `images/` SHALL contain only the metric charts listed in the scenarios below, each subject to the constant and empty rules; RT30 scene-mean charts and floor plans are always kept.

#### Scenario: Kept chart set
- **WHEN** the analyzer runs on a run with non-constant, non-empty data for every metric
- **THEN** the only metric SVGs are `hist_room_height`, `hist_room_area`, `hist_room_volume`, `geometry_types`, `hist_global_doa`, `hist_receiver_relative_doa`, `hist_distance`, `hist_elevation`, `hist_wall_distance`, `receiver_wall_distance`, `hist_start_time`, `rt30_difference`, every `rt30_scene_mean_*`, `sampling_scene_attempt`, `sampling_receiver_attempt`, `sampling_obtained_bin`, `material_coverage` and the `degraded_*` charts, plus `plan_*` floor plans

#### Scenario: Removed charts absent
- **WHEN** the analyzer runs on any run
- **THEN** `report.html` and `images/` contain no chart for `hist_room_length`, `hist_room_width`, `rt30_sabine_estimate`, `rt30_raven_rpf_gett30`, `sampling_attempts`, `sampling_requested_bin` or `surface_area`

#### Scenario: Kept charts present
- **WHEN** the analyzer runs on a run whose scenes and sources have non-constant room area and source distance
- **THEN** `images/hist_room_area.svg` and `images/hist_distance.svg` exist and their SVG is embedded in `report.html`

### Requirement: Constant metrics collapse to a summary line
When every valid observation of a metric has the same value, the report SHALL show one text line stating the metric title, the single value, its unit and n, and SHALL NOT write or embed a chart for it.

#### Scenario: Constant gain
- **WHEN** all 1000 sources declare `gain_db` 0
- **THEN** `images/hist_gain.svg` does not exist and the report contains a line with "Declared gain", "0", "dB" and "n=1000"

#### Scenario: Constant categorical metric
- **WHEN** every render variant has `background_noise_applied` False
- **THEN** no `background_noise` chart is written and the report states the single value False with its n

### Requirement: Empty metrics are listed, not drawn
When a metric shown in the report has no valid observations, the report SHALL list its title under a "Not available" note with its excluded count and SHALL NOT write or embed a chart for it. This does not apply to `rt30_scene_mean_*` charts.

#### Scenario: No degraded metadata
- **WHEN** the run has no degraded records
- **THEN** no `degraded_*` SVG is written and the report lists "Hearing profiles", "Degradation status" and "Declared ear asymmetry on common bands" as not available

### Requirement: HRTF coverage as a compact table
The report SHALL present HRTF coverage as a table with one row per employed HRTF identity and its render variant count, sorted by count descending then identity, instead of a chart.

#### Scenario: Many HRTF identities
- **WHEN** render variants use 60 distinct HRTF identities
- **THEN** the report contains a two-column HRTF coverage table with 60 rows and no `hist_employed_hrtf` chart

### Requirement: No raw record dumps in the report
`report.html` SHALL NOT embed full tables of normalized records (RT30 records, consolidated values, comparisons, pairs, matched means, coverage, materials, sampling records, sampling failures, sampling batches, degraded records, render variants). It SHALL instead name the export files holding them.

#### Scenario: Large run report
- **WHEN** the analyzer runs on a run with 1000 scenes and 3000 render variants
- **THEN** `report.html` contains no table with more than 50 data rows other than the HRTF coverage count table, and names `rt30_records`, `rt30_pairs`, `rt30_comparisons`, `rt30_consolidated`, `coverage`, `materials` and `sampling_records` as exports

### Requirement: Bounded incident table
The quality section SHALL show at most 50 incident rows with category, entity, field and detail, state the total incident count, and point to `incidents.jsonl` when rows are omitted.

#### Scenario: More incidents than the bound
- **WHEN** the run has 120 incidents
- **THEN** the report shows 50 incident rows, states 120 total, and names `incidents.jsonl`

### Requirement: Compact per-scene cards
Each scene card in "Positions by geometry" SHALL contain the scene identity, its HRTF/variant identities as one inline list, the floor plan, the RT30 per-band table with comparable means, and a compact pose summary of receiver and source positions; it SHALL NOT embed raw record tables.

#### Scenario: Card content
- **WHEN** a scene has two render variants and one source
- **THEN** its card lists both HRTF identities inline, shows the floor plan and RT30 per-band table, shows the receiver and source coordinates in meters, and contains no `<details>` with record tables

### Requirement: English report labels
All report labels, including the per-scene RT30 table, SHALL be in English. Unavailable values SHALL read "Not available" followed by the reason.

#### Scenario: Missing RAVEN band
- **WHEN** a scene's RAVEN value for a band is conflicted
- **THEN** that cell reads "Not available — variant conflict" and the table header and means line use English labels

### Requirement: Readable general parameters
The general parameters table SHALL present each parameter's observed values and frequencies as readable text rather than serialized JSON, and SHALL keep omitting `render_configuration`.

#### Scenario: Observed sample rates
- **WHEN** `sample_rate_hz` is `{"44100.0": 3000}`
- **THEN** its cell reads "44100.0 (n=3000)" without JSON braces or quotes

#### Scenario: Nested configuration value
- **WHEN** `rt30_numerical_tolerance` is `{"absolute_s": 1e-06, "relative": 1e-06, ...}`
- **THEN** its cell lists each key and value as "absolute_s: 1e-06; relative: 1e-06; ..."

### Requirement: Exports unchanged by presentation
Report presentation rules SHALL NOT change the values or shape of any JSON, JSONL or CSV export, `metrics.json` entries included, nor how the summary `status` is derived.

#### Scenario: Export stability
- **WHEN** the analyzer is run on the same input before and after this change
- **THEN** every exported data file and `summary.json` is identical, including `metrics.json` entries for charts no longer shown
