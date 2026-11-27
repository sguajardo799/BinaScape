# Design

## Context

All presentation lives in `apps/output-analyzer/src/output_analyzer/report.py`.
`_write_charts` builds every `metrics.json` row and writes its SVG in one inner
`metric(...)` closure; `_write_report` embeds charts per `section` via `grid()`
and appends raw dumps through `_records_table`. Each carousel card
(`_spatial_carousel` + `_scene_rt30`) embeds up to 7 record tables. `metrics`
rows are persisted by `build_analysis` independently of what the HTML shows.
No other component reads `report.html` or `images/`. Motivation and the
field-by-field verdict: see proposal.md.

## Goals / Non-Goals

**Goals:**
- Separate "metric computed and exported" from "metric presented".
- Keep the report bounded in size independent of run size, except the
  per-scene carousel which scales linearly with compact cards.

**Non-Goals:**
- No change to `loader.py`, `scene_distributions.py`, CLI or any export.
- No new chart types, no pagination/lazy loading of carousel cards, no
  external assets.
- No redesign of the scene-mean SVGs or floor plans.

## Decisions

1. **Presentation decision after metric construction.** `metric(...)` keeps
   appending the full row to `metrics` for every key (exports unchanged), then
   decides presentation: skip SVG when the key is in a `HIDDEN_METRICS` set,
   when `n_valid == 0` (record as "empty"), or when the distinct-value count is
   1 (record as "constant" with value). `charts` only gets keys that were
   drawn; `_write_report` renders a "constant" line list and a "Not available"
   list per section from these records.
   *Alternative:* filter only in `_write_report` and keep writing all SVGs —
   rejected because the user wants uninformative images gone and `images/`
   would keep 30+ files nobody sees.
   *Alternative:* stop computing hidden metrics — rejected, it would change
   `metrics.json` (spec "Exports unchanged by presentation").

2. **Hidden set is explicit, not heuristic.** `hist_room_length`,
   `hist_room_width`, `rt30_sabine_estimate`, `rt30_raven_rpf_gett30`,
   `sampling_attempts`, `sampling_requested_bin`, `surface_area`,
   `hist_employed_hrtf`. `sampling_attempts` is hidden even when it differs
   from `scene_attempt` on another run; its values remain in `metrics.json`.
   Constant/empty collapse is the only data-driven rule.

3. **Scene-mean metrics exempt from collapse/empty rules.** They already have a
   specified "No data / Not available" SVG (existing capability); they bypass
   decision 1's data-driven rules.

4. **Raw dumps replaced by an export index.** Remove every `_records_table`
   dump in Acoustics and Quality; the "Data exports" section becomes a short
   table of file stem → observation unit (same content as README table), and
   Acoustics/Quality reference it. Keep `rt_by_band` (one row per band) with
   numbers formatted to 4 significant digits. Incidents use a dedicated bounded
   table (`INCIDENT_ROW_LIMIT = 50`).

5. **Compact cards.** Card = heading, inline HRTF list (comma-joined
   `hrtf_id`), plan SVG, `_scene_rt30` table + means line, and a small pose
   table (role, id, x, y, z in m) built from scene receiver fields and sources.
   The `<details>` with four RT30 audit tables and the pose-provenance dumps are
   removed. Card labels move to English.

6. **Readable parameter cells.** A `_readable(value)` formatter: dict →
   `"k: v; k: v"`, distribution dicts `{value: count}` → `"value (n=count)"`,
   lists joined by comma. Distribution detection: dict whose values are all
   ints and that is one of the observed-distribution keys from
   `general_parameters` (observed_schema_versions, backend, sample_rate_hz,
   project_name); other dicts use key: value.

## Risks / Trade-offs

- [Auditors lose in-page raw tables] → every dumped population is already an
  exported file; the report names each file and its observation unit.
- [Constant collapse hides a chart a reader expects] → the summary line keeps
  title, value, unit and n, so the information is still present.
- [Carousel still scales with scenes (1000 inline plans)] → accepted; the
  plans are the per-scene view the user relies on. Measured target: the
  reference run report drops from 80 MB to well under 20 MB; pagination is
  out of scope.
- [Spanish-to-English label change breaks existing tests/readers] → tests are
  updated in the same change; no external consumer of the HTML.

## Migration Plan

Regenerate reports with `output-analyzer analyze <run>`. Rollback = revert the
commit; exports are identical so no data migration is needed.
