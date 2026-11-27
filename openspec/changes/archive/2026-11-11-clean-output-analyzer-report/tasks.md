# Tasks

All tasks target `apps/output-analyzer` (the only affected component). Run tests with
`uv run --project apps/output-analyzer --extra dev pytest apps/output-analyzer/tests`.

## 1. Chart presentation rules

- [x] 1.1 Add a RED test in `tests/test_report.py` asserting no SVG and no embedded chart for `hist_room_length`, `hist_room_width`, `rt30_sabine_estimate`, `rt30_raven_rpf_gett30`, `sampling_attempts`, `sampling_requested_bin`, `surface_area`, `hist_employed_hrtf`, while `metrics.json` still contains all of them; verify it fails before 1.3
- [x] 1.2 Add RED tests for constant collapse (fixture with constant `gain_db` → no `hist_gain.svg`, summary line with title/value/unit/n) and empty listing (no degraded records → no `degraded_*.svg`, titles listed as Not available); verify they fail before 1.3
- [x] 1.3 In `report.py`, implement the `HIDDEN_METRICS` set and constant/empty presentation decisions in `_write_charts` (scene-mean metrics exempt), keep every `metrics` row unchanged, and render constant lines and Not-available lists per section in `_write_report`; verify 1.1 and 1.2 pass
- [x] 1.4 Update `test_every_exported_svg_has_visible_legend_and_metric_population` so the `sampling_receiver_attempt` empty-SVG assertion targets the Not-available listing instead; verify the full suite passes

## 2. Tables and raw dumps

- [x] 2.1 Add RED tests: HRTF coverage table sorted by count desc then id; report on the `sample_run` fixture contains no `<table>` with more than 50 data rows and names `rt30_records`, `rt30_pairs`, `rt30_comparisons`, `rt30_consolidated`, `coverage`, `materials`, `sampling_records`; bounded incident table shows ≤50 rows, total count and `incidents.jsonl`; verify they fail first
- [x] 2.2 Remove Acoustics and Quality `_records_table` dumps, add the HRTF coverage table, the bounded incident table and an export index (stem → observation unit) in the Data exports section; format `rt_by_band` numbers to 4 significant digits; verify 2.1 passes
- [x] 2.3 Add a RED test for readable general parameters (`{"44100.0": 3000}` → `44100.0 (n=3000)`, nested dict → `key: value; ...`, `render_configuration` still absent), implement the formatter, and verify it passes together with `test_configuration_hidden_only_in_presentation_and_preserved_in_exports`

## 3. Per-scene cards

- [x] 3.1 Add RED tests: card lists HRTF identities inline, has a pose table with receiver/source x, y, z in m, and contains no `<details>` record tables; verify they fail first
- [x] 3.2 Rewrite card assembly in `_spatial_carousel` and `_scene_rt30` (drop audit and provenance dumps, inline HRTF list, compact pose table) keeping carousel controls unchanged; verify 3.1 and `test_carousels_keep_one_card_per_scene_and_independent_controls` pass
- [x] 3.3 Translate card labels to English (`Not available — variant conflict`, `Additional — outside comparable mean`, `Sabine mean`, `RAVEN mean`, …) and update `test_scene_rt30_*` assertions accordingly; verify those tests pass

## 4. Export invariance and docs

- [x] 4.1 Add a test that runs `build_analysis` on `sample_run` and asserts every JSON/JSONL/CSV export and `summary.json` equals a baseline produced by the same input with presentation-only data (compare against values captured before chart filtering, e.g. `metrics.json` keys for hidden charts present with original `n_valid`/`mean`/`counts`); verify it passes
- [x] 4.2 Update `apps/output-analyzer/README.md` "Report and geometry" and validation paragraphs to describe hidden charts, constant/empty collapse, bounded tables, compact cards and the export index; verify by re-reading that no paragraph still claims raw dumps or Spanish card labels in HTML

## 5. Integration check

- [x] 5.1 Run the full output-analyzer test suite and verify it passes
- [x] 5.2 Run `uv run --project apps/output-analyzer output-analyzer analyze outputs/directivity_experiment101 --output-dir <scratch dir>` and verify the report is under 20 MB (baseline 80 MB), contains the RT30 scene means section, and every export file is byte-identical to the existing `outputs/directivity_experiment101/analysis` exports

## Workflow follow-up

- Archive the change after review so `output-analyzer-report` becomes a main spec and the RT30 scene-distributions requirement is updated.
