# Tasks

## 1. Output Analyzer: complete scene-mean population and exports

- [x] 1.1 Add complete selected-band mean derivation and `rt30_scene_means` to `loader.py`, preserving current consolidation and matched-pair summaries; verify loader tests cover extra bands, shuffled frequency order, multiple equivalent variants and one row per unique scene.
- [x] 1.2 Add per-method unavailable reasons, selected-frequency groups and provenance; verify tests cover missing/duplicate/invalid selection lists, missing/nonpositive/nonfinite bands, conflicted RAVEN bands, scenes without renders and independently available Sabine means.
- [x] 1.3 Export `rt30_scene_means` in JSON/JSONL/CSV and additive summary entries through `report.py`; verify export round-trips, null handling and unchanged existing frequency exports and matched means in loader/report regression tests.
- [x] 1.4 Update the analyzer README export table and RT30 explanation with complete versus matched-band mean semantics; verify descriptions match the new fields and fixture exclusion behavior.

## 2. Output Analyzer: distribution metrics and sampling context

- [x] 2.1 Add deterministic selected-band grouping and conservative single-batch scope validation using existing normalized metadata; verify tests cover complete coverage, missing/invalid edges, multiple batches, heterogeneous bands and valid edges with unavailable targets.
- [x] 2.2 Derive shared bin edges and method counts, including configured underflow/overflow and deterministic union-based fallback; verify every exact boundary, constant/empty populations and count conservation with synthetic fixtures.
- [x] 2.3 Add reconstructible per-group histogram/scatter metrics with scene observations, exclusions, intervals, edge source and target provenance; verify tests independently reconstruct counts and coordinates from exports and ensure raw HRTF records cannot alter scene-level weighting.
- [x] 2.4 Document sampling-reference eligibility, automatic fallback and why targets apply only to Sabine; verify README examples distinguish planned target counts from observed counts and do not imply RAVEN acceptance limits.

## 3. Output Analyzer: SVG presentation and report integration

- [x] 3.1 Implement aligned scene-mean histogram panels with shared count scale, selected-band labels, Sabine target references and visible tails; verify SVG tests cover labels, shared scales, legends, valid/excluded counts and target visibility.
- [x] 3.2 Implement per-group Sabine-versus-RAVEN scatter SVGs with a visible equality line and equal axis ranges/scaling; verify tests cover one point per complete paired scene, out-of-range visibility, constant values and no paired data.
- [x] 3.3 Embed the new charts in a prominent scene-mean section before frequency diagnostics, keep standalone SVG exports and clarify legacy frequency chart titles; verify self-contained HTML, existing chart keys/filenames, deterministic group keys and unavailable states through report tests.
- [x] 3.4 Update the README report guide to explain the new panels, scatter axes and exclusions; verify the documented analysis command remains unchanged and the described section order matches generated HTML.

## 4. Output Analyzer: integration verification

- [x] 4.1 Run `uv run --project apps/output-analyzer --extra dev pytest apps/output-analyzer/tests`; verify all existing consolidation, metadata-only, SVG legend, room-card and export tests pass alongside the new scenarios.
- [x] 4.2 If `outputs/directivity_experiment` is available, analyze it into a fresh separate `--output-dir` and compare against the design evidence: 1000 complete means per method, eight Sabine counts `[125, 127, 124, 127, 124, 125, 124, 124]`, RAVEN tails 55/22 and unchanged 10,000-record frequency populations; record the result or explicit local-data unavailability without overwriting the original analysis.
- [x] 4.3 Visually inspect a synthetic report and, when available, the regenerated directivity report; verify readable interval labels and legends, shared panel scales, clear Sabine-only targets, visible RAVEN tails and a correctly labeled scatter equality line.

## Verification evidence

- Full analyzer suite: `uv run --project apps/output-analyzer --extra dev pytest apps/output-analyzer/tests` — **96 passed**.
- Ruff checks passed for both modified source files, the new distribution module and all three new test files; `git diff --check` passed.
- Real report: `outputs/directivity_experiment/analysis_rt30_mean_review/report.html`; original `analysis/` was not overwritten. Both methods and the scatter retain 1000 complete scenes; Sabine counts and RAVEN counts/tails match the design evidence. Existing method populations retain 10,000 values each and `rt30_pairs.json` equals the original export. The CLI reports only the existing optional missing `clarity_index.jsonl` incident, with no excluded RT30 pairs.
- Synthetic report: `outputs/rt30_synthetic_review/sim_test/analysis/report.html`, generated from the portable fixture with explicit batch targets.
- Visually inspected rendered Edge screenshots of both complete HTML reports: shared scales, readable interval labels/legends, Sabine-only quota lines, real RAVEN underflow/overflow counts 55/22 and scatter equality line confirmed. Screenshots retained under `C:/Users/sguaj/AppData/Local/Temp/opencode/rt30-real-charts.png` and `rt30-synthetic-charts.png`.
