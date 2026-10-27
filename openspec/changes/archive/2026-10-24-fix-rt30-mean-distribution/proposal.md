# Proposal

## Why

Output Analyzer currently plots pooled scene/frequency RT30 values rather than the distribution of mean RT30 per scene, making the configured sampling distribution difficult to verify. In `outputs/directivity_experiment/analysis`, the two method histograms contain 10,000 observations each, whereas the experiment controls 1,000 scene means over seven selected bands.

## What Changes

- Add a prominent distribution of arithmetic mean RT30 per unique scene, separating Sabine estimates from consolidated RAVEN T30 and explicitly naming the selected frequency bands.
- Use shared interval boundaries and count scales for aligned method panels; prefer authoritative sampling batch bin edges and show the Sabine target counts when their scope is unambiguous.
- Preserve and display RAVEN means outside the sampling range; the Sabine sampling target is not a RAVEN acceptance criterion.
- Add a scene-level Sabine-versus-RAVEN scatter plot with an equality line, using complete, identical selected band sets.
- Export auditable scene means, exclusions, bin counts, reference provenance and plotting metadata. Retain existing per-frequency exports, matched-pair summaries and chart filenames, while clarifying their observation units.
- Handle missing bands, conflicting variants, missing batch metadata and heterogeneous selected band sets without imputing values or silently changing the mean definition.

## Capabilities

### New Capabilities

- `rt30-scene-distributions`: Metadata-only, scene-level RT30 mean distributions, sampling-target context and paired method visualization with reproducible exports.

### Modified Capabilities

None. The current capability inventory does not define analyzer RT30 distributions. This addition preserves existing renderer, sampler and analyzer consolidation contracts.

## Impact

- `apps/output-analyzer/src/output_analyzer/loader.py`: additive normalized scene-mean derivation from existing selected-band metadata and authoritative consolidated values.
- `apps/output-analyzer/src/output_analyzer/report.py`: additive exports, distribution/scatter SVGs, chart metrics and a dedicated report section.
- `apps/output-analyzer/tests/test_loader.py`, `test_report.py`, fixtures and `apps/output-analyzer/README.md`: acceptance coverage and usage/observation-unit documentation.
- Inputs remain schema 3.0 metadata and existing sampling batches. No YAML/CLI contract change, new plotting dependency, WAV analysis, resampling, renderer execution or Clarity processing is required.
- Existing run artifacts are evidence, not edited fixtures. Regeneration is an explicit verification step with a separate output directory.
