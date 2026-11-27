# Proposal

## Why

`report.html` has become unusable as a reading document. On the reference run
`outputs/directivity_experiment101` (1000 scenes, 3000 render variants) it weighs
**80 MB** with 7015 HTML tables and 2014 `<details>` blocks. Most of that weight
is raw normalized records dumped as wide JSON-cell tables (40 MB in the
per-scene carousel cards, 34 MB in Acoustics, 9 MB in Quality) that duplicate
files already exported as JSON/JSONL/CSV. Several charts also carry no
information: constant distributions (gain 0 dB, 1 source per scene, `speech`
only, no background noise, no fallback), empty populations (degraded metadata
with n=0), duplicated series (`attempts` equals `scene_attempt` on every record)
and band-pooled RT30 histograms superseded by the scene-mean charts.

## What Changes

Field-by-field evaluation of the current report (observed on the reference run):

| Report element | Verdict | Reason |
|---|---|---|
| Population counts table | Keep | Core denominators |
| General parameters table | Keep, reformat | Small; render values as readable text, not JSON strings |
| `hist_room_area`, `hist_room_volume`, `hist_room_height` | Keep | Real geometry distributions |
| `hist_room_length`, `hist_room_width` (X/Z envelope extents) | Remove from report | Bounding extents; the README itself says they are not a substitute for real area |
| `geometry_types` | Keep | Sampling balance by geometry |
| `hist_employed_hrtf` (bar per HRTF file) | Replace by compact table | Dozens of categories make an unreadable bar chart |
| `hist_n_sources`, `hist_gain`, `source_events`, `background_noise`, `sampling_matched_requested_bin`, `sampling_fallback_used` | Collapse when constant | One bar/one value; show a one-line summary instead |
| DoA (global, receiver-relative), distance, elevation, source and receiver wall distance, start time | Keep | Spatial sampling distributions |
| `rt30_sabine_estimate`, `rt30_raven_rpf_gett30` (all bands pooled) | Remove from report | Mixes frequencies in one histogram; superseded by RT30 scene-mean charts |
| `rt30_difference` | Keep | Aligned-pair bias summary |
| RT30 scene-mean histograms and scatter | Keep | Primary RT30 audit |
| RT30 aligned comparison by band table | Keep, format numbers | 7 rows, readable |
| Details: matched means, comparisons, pairs, consolidated, records (up to 10000 rows each) | Remove from HTML | Already exported; replace by pointers to export files |
| Carousel card: floor plan + RT30 per-band table | Keep | Per-scene view |
| Carousel card: HRTF/variant records table | Replace by inline list | Only identities are useful |
| Carousel card: RT30 audit dumps (4 tables) and pose provenance dumps | Replace by compact pose summary | Raw records live in exports |
| `sampling_attempts` | Remove from report | Duplicates `sampling_scene_attempt` on the reference run (0/1000 differ) |
| `sampling_requested_bin` | Remove from report | Same information as target marks on the scene-mean histogram; `obtained_bin` kept |
| `degraded_profiles`, `degraded_status`, `degraded_asymmetry` | Hide when empty | Render only when n_valid > 0 |
| `surface_area` | Remove from report | Low analytical value; `material_coverage` kept |
| Incident counts table, population definitions | Keep | Quality overview |
| Quality details dumps (coverage, materials, sampling, render variants) | Remove from HTML | Exported; pointers instead |
| Incidents dump | Bound | Show at most a fixed number of rows plus pointer to `incidents.jsonl` |
| Mixed Spanish/English labels in RT30 card tables | Unify to English | Consistency with the rest of the report |

Behavior summary:
- Report presentation only: charts not shown are no longer written as SVG; their
  `metrics.json` entries and every data export stay unchanged.
- **BREAKING (presentation)**: HTML no longer contains raw record tables; audits
  use the exported files. Spanish card labels (`No disponible`, `Adicional`,
  `Promedio Sabine`, …) become English.

## Capabilities

### New Capabilities
- `output-analyzer-report`: Content and readability rules of `report.html` and
  its `images/` SVGs: which charts are shown, collapse of constant/empty
  metrics, bounded tables, per-scene card content, pointers to exports, and
  export invariance.

### Modified Capabilities
- `output-analyzer-rt30-scene-distributions`: the "Existing sections unaffected"
  scenario of "Scene-mean section in the HTML report" no longer holds; existing
  sections follow `output-analyzer-report`.

## Impact

- Code: `apps/output-analyzer/src/output_analyzer/report.py` (`_write_charts`,
  `_write_report`, `_spatial_carousel`, `_scene_rt30`, `_records_table`).
- Tests: `apps/output-analyzer/tests/test_report.py` (Spanish strings, card
  details assertions, legend loop over all SVGs).
- Docs: `apps/output-analyzer/README.md` report section.
- No change to `loader.py`, `scene_distributions.py`, CLI, exported data files,
  `metrics.*` or `summary.json`. No other component consumes `report.html`.
