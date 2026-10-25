# Design

## Context

See `proposal.md` for motivation. This design is planned behavior, not a description of features already implemented.

Current implementation in `apps/output-analyzer`:

- `loader.py` retains selected bands in `sampling_records[].reverberation_sampling`, all Sabine method bands in `rt30_records`, and authoritative RAVEN scene/frequency values in `rt30_consolidated`.
- `summary_for` computes `rt30_matched_means` using available valid frequency pairs. These may be partial means and therefore cannot always represent the configured sampling metric.
- `report.py::_write_charts` pools all method bands into histograms. `_write_histogram` uses 12 automatic intervals, independently determined per chart.
- `build_analysis` writes normalized exports before charts; `_write_report` embeds SVGs. Existing tests require legends, explicit populations, unavailable states and unchanged consolidation semantics.
- `sampling_batches` already retains bin edges and per-bin target counts. No experiment YAML read is necessary.

Observed evidence in `outputs/directivity_experiment/analysis`:

| Complete mean over 125–8000 Hz | Sabine | RAVEN |
|---|---:|---:|
| Unique scenes | 1000 | 1000 |
| Mean (s) | 0.5928323425 | 0.5809069980 |
| Minimum (s) | 0.2005000550 | 0.1467076564 |
| Maximum (s) | 0.9997425809 | 1.5190819252 |
| Below 0.2 / above 1.0 s | 0 / 0 | 55 / 22 |

Sabine interval counts are `[125, 127, 124, 127, 124, 125, 124, 124]`; RAVEN counts are `[107, 117, 101, 134, 136, 143, 129, 56]`, plus its two tails. These are optional local regression evidence, not universal expectations. The single batch reports target counts of 125 and matches the Sabine observations.

## Goals / Non-Goals

**Goals:**
- Make the controlled sampling metric visible while retaining independently available method data.
- Derive auditable numeric populations once and render exactly those observations.
- Add outputs without changing current frequency-level contracts.

**Non-Goals:**
- Changing sampler quotas, materials, RAVEN output, schemas or RT30 numerical equivalence tolerances.
- Estimating RT from WAVs, certifying acoustic accuracy or requiring RAVEN to follow the Sabine target distribution.
- Replacing current partial matched-pair means or introducing plotting dependencies/CLI switches.
- Inferring batch membership or target quotas when existing metadata cannot establish scope.

## Decisions

### 1. Add an authoritative complete-mean population in the loader

Add `AnalysisData.rt30_scene_means` after current consolidation. Emit one row per unique normalized scene, including scenes without usable RT metadata. Fields:

- `scene_id`, `selected_frequencies_hz`, `band_group`, `n_expected_bands`;
- `sabine_mean_s`, `raven_mean_s`, `difference_mean_s` (only when both complete means exist);
- per-method valid-band counts, unavailable frequency lists and exclusion reasons;
- existing cause identifiers and metadata paths for traceability.

Validate the raw selected list before canonicalizing it to a sorted tuple: require a nonempty list of unique finite positive numeric frequencies. Do not silently deduplicate or hardcode seven octaves. Derive Sabine from normalized method records and RAVEN from consolidated records, using frequency identity rather than positional indices. A finite positive value must exist at every selected frequency for that method. Preserve independent Sabine means when RAVEN is absent/conflicted.

Keep `summary.rt30_matched_means` and room-card tables unchanged. Add the new complete population separately and export `rt30_scene_means.{json,jsonl,csv}`. This avoids changing existing consumers or hiding incomplete-RAVEN scenes from the Sabine distribution.

**Rejected alternative:** reuse matched means directly. It is shorter but silently changes frequency support after a band exclusion. Using only paired complete scenes for both histograms would instead bias the sampling check toward render availability.

### 2. Partition by selected frequencies and resolve batch context conservatively

Use a stable band-group identity based on the canonical frequency tuple, never Python's randomized hash. Invalid selection rows form an explicitly unavailable group rather than contaminating another denominator.

For each group, accept batch context only when a single deduplicated batch unambiguously covers the analyzed scene population: one group, complete manifest coverage, batch `manifest_count` and `accepted_realizations` matching that population. Validate finite strictly increasing bin edges. Validate targets separately: require one nonnegative integer target per interval, mapped by bin index. Retain provenance to the contributing manifest paths and batch export record.

When batch scope is ambiguous (including multiple batches or selected-band groups), do not combine quotas or associate batches by matching counts alone. Use a deterministic fallback of 12 shared equal-width intervals over the union of both methods' valid means in the group. For a constant value, use symmetric padding of `max(abs(value) * 0.05, 0.01)` seconds; for no values, render unavailable without invented intervals. Missing targets alone do not discard valid configured edges. Mark edge source and reference-unavailability reason in metrics.

**Rejected alternatives:** reading the current YAML can misrepresent an older run; deriving uniform targets from counts invents an experiment contract; a new multi-batch identity protocol would expand scope across producers.

### 3. Use dedicated paired panels and a scatter renderer

Add dedicated SVG generation using existing SVG primitives; leave the generic histogram default intact for unrelated charts.

- A dedicated scene-mean report section precedes per-frequency diagnostics.
- Each band group gets two aligned histogram panels with identical interval labels and count scale; include target counts in scale selection so reference lines remain visible.
- Configured ranges get labeled underflow/overflow bars on both panels, including zero counts. For comparisons, keep these categorical tails visually distinct from finite-width in-range bars.
- Classify values once with explicit left-closed/right-open intervals and a closed final maximum. Do not round values for membership, clamp outliers or use RT equivalence tolerance for histogram bins.
- Draw authoritative target counts only on the Sabine panel, with visible text that they are planned Sabine quotas, not RAVEN limits. Targets can vary by interval.
- Scatter uses X=Sabine, Y=RAVEN, common numeric extents and equal plot scaling, an equality line and full observed range. Group legends include selected bands and paired exclusions.
- Every SVG, including unavailable states, retains visible population/count/unit legends, standalone export and inline HTML embedding.

Retain `rt30_sabine_estimate`, `rt30_raven_rpf_gett30` and `rt30_difference` chart keys and filenames; label the first two as pooled scene/frequency distributions. New chart keys use the `rt30_scene_mean_` prefix with deterministic group suffixes.

**Rejected alternative:** overlaying all values in one translucent histogram obscures different valid populations and the sampling reference. Independent auto bins prevent direct comparison.

### 4. Make plots reproducible through additive metrics

Use the new scene-mean rows as the only source of scene-level chart observations. Store method/group keys, scene IDs, selected frequencies, valid/excluded counts, exclusion reasons, mean values, edge source, finite edges, interval closure, interval counts, tail counts and target counts/provenance in the new metrics. Scatter observations store both coordinates and scene ID. Chart population descriptions explicitly distinguish unique scenes from unique scene/frequency records.

Add corresponding summary entries without removing existing keys. Preserve nested JSON/JSONL and existing CSV serialization rules. Ensure standalone scene-mean exports and metrics jointly reconstruct all counts and points without reopening input files.

**Rejected alternative:** SVG-only diagnostics are not auditable; modifying existing frequency metrics to mean scenes breaks consumers and current tests.

## Risks / Trade-offs

- Complete means exclude more RAVEN scenes than partial matched means → report per-method reasons/counts and retain existing matched-band diagnostics.
- Many distinct selected-band sets can create many panels → deterministic grouping, concise titles and one section per group; never silently merge definitions.
- Existing batch identity is limited → conservative fallback without targets; no inferred associations across multiple batches.
- Floating-point interval boundaries can lose/double-count scenes → membership uses stored boundaries and explicit final-bin closure, tested at every edge and tails.
- Legacy tests locate metrics by prefixes → preserve legacy metric ordering/keys and update new tests to select exact new keys.
- Real run outputs are local and large → portable synthetic tests are mandatory; real-data checks are optional and write to a separate output directory.

## Migration Plan

1. Add complete-mean normalization and additive exports with unit tests.
2. Add shared bin/metric derivation, dedicated SVGs and report integration with rendering tests.
3. Document observation units, unavailable behavior and the distinction between Sabine targets and RAVEN results.
4. Run the full analyzer test suite. If the local directivity run is available, generate a report under a fresh separate `--output-dir` and compare the evidence above; do not overwrite the original analysis during verification.
5. Rollback requires reverting only the implementation of these additive outputs. No producer migration, audio regeneration or metadata rewrite is needed; old reports remain unchanged until explicitly regenerated.
