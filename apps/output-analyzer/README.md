# Output Analyzer

> Revision scope: this guide describes the implemented analyzer in
> `update-fix-output-analyzer` at `1a3b1c1`. The requested audit baseline is
> `develop` at `05f9b27`, whose analyzer does not yet contain the same schema
> checks, unique populations, per-frequency RT30 analysis or embedded SVG report.
> See the [documentation audit](../../docs/maintenance/documentation-audit.md)
> for the revision comparison. These differences do not reopen historical PRDs.

`output-analyzer` generates an auditable metadata-only report for a run directory. It supports **exactly schema 3.0** for scene manifests and render sidecars. Explicit older/future versions are excluded, and missing versions are incidents. Degraded metadata supports its producer schema 1.0. Index rows have no scene schema requirement; their fields follow their own current producers.

## Usage

```powershell
uv run --project apps/output-analyzer output-analyzer analyze outputs/sim_001
uv run --project apps/output-analyzer output-analyzer analyze outputs/sim_001 --output-dir outputs/sim_001/analysis
```

The default destination is `<run-dir>/analysis`. The root must be accessible and the destination writable. Malformed/incomplete metadata yields a partial report; no valid observations yields a report with incidents and an unavailable-data state. Infrastructure failures remain operational errors.

## Inputs and limits

- `metadata/manifests/scene/*.json`
- `output_audio/render/**/*__render.json` (including MATLAB singleton `sources` objects)
- `output_audio/degraded/**/*.json`
- `metadata/indexes/render_index.jsonl`
- `metadata/indexes/clarity_index.jsonl`
- `metadata/indexes/scene_sampling_failures.jsonl`

Only metadata files are opened. WAV contents and referenced audio, material and HRTF assets are never opened. A metadata-only copy of a run is sufficient. Missing optional indexes do not imply zero failures or complete coverage. Expected totals are available only when supplied by authoritative configuration/indexes.

## Report and geometry

`report.html` is self-contained: all charts are inline SVG and no network or sibling images are required. Separate SVG files remain available under `images/`.

The report has one general-parameters section, three spatial groups (`shoebox`, `l_shape`, `trapezoid`), global acoustics/source analysis, and metadata coverage/quality. General parameters retain distinct observed values and their frequencies. Each geometry has an independent manual carousel with one exact floor plan per scene, including concavity and offsets and equal axis scales. Previous/Next, direct scene selection and Left/Right/Home/End keyboard controls navigate without autoplay or wrapping. Empty and single-scene groups disable navigation. Cards show the run, scene identity and associated HRTF/variant identities without duplicating scenes. A square marks the receiver and colored circles mark sources/events. Expand pose provenance to audit the declared coordinates and margins.

Public coordinates are `[x,y,z]`, with Y vertical and `[x,z]` in the floor plane. Manifest poses take precedence. Documented RAVEN fallback converts `[x,y,z]` to public `[x,y,-z]`; unknown frames are excluded. Area is computed from the actual polygon and volume is area × height. X/Z dimensions are bounding extents, never a substitute for real area. Missing yaw does not become zero; coincident poses retain distance zero and have undefined angles.

RT30 Sabine estimates and RAVEN reported T30 are metadata, not WAV measurements. Comparisons join explicit central frequencies per scene. Differences are RAVEN minus Sabine; pair summaries use the same bands. Extra RAVEN bands remain in method distributions and exports. Equivalent variants are averaged once per scene/frequency. A conflict excludes only the affected RAVEN band and its comparison; other bands and independent Sabine estimates remain available. Raw variant/frequency records and consolidated scene/frequency means, extrema, spread, standard deviation, contributing/excluded variant counts and provenance remain auditable. No interpolation or array-index pairing is performed.

## Export observation units

All tabular populations below have `.json`, `.jsonl`, and `.csv` exports. JSON/JSONL preserve nested fields and nulls; CSV is a flat transport with serialized nested values. Null/missing values mean unavailable, never an imputed zero.

| File stem | Observation unit |
|---|---|
| `scenes` | Unique scene (`scene_id`), independent of HRTF count |
| `sources` | Unique scene/source identity |
| `render_scenes` | Unique scene/HRTF render variant; retained historical filename **does not mean unique scenes** |
| `joined_render_sources` | Source–variant row; retained historical filenames, not unique sources |
| `degraded_metadata` | Unique degraded record under the current producer contract |
| `rt30_records` | Individual method/frequency values with scene/variant provenance and comparison eligibility |
| `rt30_consolidated` | One RAVEN scene/frequency estimate, dispersion, variants and exclusion cause; no HRTF weighting |
| `rt30_comparisons` | All candidate scene/frequency comparisons, including excluded pairs and reasons |
| `rt30_pairs` | Consolidated scene/frequency pair, with Sabine, RAVEN and difference in seconds |
| `materials` | Scene/surface material and area, walls identified by `wall_ids` |
| `sampling_records` | Available sampling metadata/failure observations |
| `sampling_failures` | Parsed rows from the optional sampling failure index |
| `sampling_batches` | Unique batches, not one repeated batch per manifest |
| `coverage` | Scene/HRTF coverage and available authoritative expectations |
| `incidents` | Category/entity/field/path/detail for an affected observation |
| `metrics` | Chart metric, unit, population, valid/excluded counts and contributing values/identities |

`summary.json` contains run status, distinct populations, incidents and chart denominators. `general_parameters.json` contains the available grouped configuration and observed parameter distributions. Chart observations in `metrics.json` allow recomputing distributions without reading HTML; original normalized records retain full provenance.

An excluded count refers to the named normalized population and field. Documents rejected before normalization are reported separately in incidents. Category totals may overlap: use the distinct affected-entity total from the summary rather than summing categories. Event absence is excluded from event categories. Unknown expected coverage is unavailable, never 0% or 100%.

The quality section includes materials, HRTF reconciliation, sampling/batch/failure data, auditory profiles/status/common-band asymmetry and declared background noise. Missing metadata displays unavailable coverage; no effective SNR or audio level is inferred.

## Validation

```powershell
uv run --project apps/output-analyzer --extra dev pytest apps/output-analyzer/tests
```

Tests use small current-contract fixtures and do not require MATLAB, RAVEN or WAV files. The optional real integration reference `outputs/e2e_raven_rt30_binaural_20260924` should report 100 unique scenes, 200 render variants and 32/32/36 scenes by geometry; those numbers are not hardcoded expectations for other runs.

Generic geometry/identity equivalence retains tolerance 1e-8. RT30 uses separate shared constants `RT30_ABS_TOL_S = 1e-6` seconds and `RT30_REL_TOL = 1e-6` (dimensionless): every valid variant pair must satisfy `abs(a-b) <= max(RT30_ABS_TOL_S, RT30_REL_TOL * max(abs(a), abs(b)))`. This numerical resolution (one microsecond/one part per million) is not certified simulator precision or an acoustically meaningful threshold. It is fixed, not fitted per run. Constants and units are included in summary/general-parameter exports and consolidated rows. Numerical equivalence does not prove determinism. Sabine comparison eligibility follows explicit mean_bands_hz; method distributions retain all available explicit bands, including additional RAVEN bands.

Every exported/embedded SVG includes a visible legend: room contour and actor/event shapes/colors, histogram series and observation counts, categorical colors, RT30 frequencies/method/sign, or an unavailable-data state with valid/excluded counts and units. Legends wrap inside the SVG canvas.

The verbose `render_configuration` block is omitted entirely from HTML presentation, including expandable tables; it remains intact in `general_parameters.json` and audit exports. No population changes follow from hiding this field.

CLI quality diagnostics summarize categories, affected scenes/pairs and a bounded identity sample. Consult `incidents.jsonl` and `rt30_comparisons.jsonl` for complete causal details; derived exclusions are not independent failures.

Each room card in Positions by geometry includes a frequency table for Sabine RT30 and consolidated RAVEN T30 (seconds). Comparable arithmetic means reuse the same valid selected frequency pairs, with the frequency list and valid/excluded counts. Additional bands remain visible but never enter those means. Missing or conflicted values show unavailable with a reason, preserving the other method. Expand the room RT30 audit details for original values, variant support, spread, causes and provenance. The table moves with its room card using every carousel control.

## Documentation and pipeline context

This is an optional analysis application after artifact generation. It can read
existing run metadata without invoking the renderer or hearing-loss backend.

- [Documentation index](../../docs/README.md)
- [Shared artifact contracts](../../docs/architecture/contracts.md)
- [Development and testing](../../docs/development/testing.md)
- [Orchestrator](../orchestrator/README.md)
- [MATLAB renderer](../matlab/README.md)
- [Clarity backend](../clarity-backend/README.md)
