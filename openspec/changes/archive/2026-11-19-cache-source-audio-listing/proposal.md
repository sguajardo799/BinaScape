# Proposal

## Why

Profiling `configs/experiments/speech_directivity_experiment.yml` (2000 scenes, 3 workers) showed that 98% of static planning time is spent re-listing the source audio catalog: `_sample_sources` → `_build_audio_pools` → `list_source_wavs(audio_dir)` runs on every receiver attempt (334 calls for 40 scenes), and each call over `assets/events/all/speech/` (14,014 WAVs in 20 identity folders) takes about 2.15 s because it stats every file and sorts with `Path.relative_to` as key. Measured planning cost is about 9 s per scene with 3 workers, roughly 5 hours for the batch. The previous change (`static-plan-completion-dispatch`) improved dispatch and fingerprinting, but those were not the bottleneck, and parallel workers mostly compete for the same filesystem scans.

## What Changes

- List each source `audio_dir` at most once per process during a static preflight inspection, and reuse that listing for every scene and attempt sampled by that inspection (parent process and planning worker processes). A new inspection lists again.
- Make `list_source_wavs` cheaper without changing its result: enumerate with directory entries that carry file type information (no extra stat per file) and sort with a precomputed normalized relative-path key. Measured on the real catalog: 2.15 s → 0.09 s with an identical ordered result.
- Discovery rules (depth, extension case, ordering by relative path) and therefore sampled scenes and manifests stay exactly as today for any seed.
- **BREAKING (resume only)**: `source_audio.py` is part of the static plan's generation contract fingerprint, so editing it changes the contract. Batches whose plan was published before this change cannot be resumed; they require a fresh run (`resume_if_possible: false`, `overwrite_existing: true`). Decision (user, 2026-11-19): accept this instead of adding a contract-equivalence mechanism or moving the code out of the fingerprinted module.
- Out of scope: render-time acoustic resampling after a RAVEN T30 rejection keeps listing per call (it benefits from the faster listing only).

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `static-scene-planning`: adds requirements that source audio catalogs are listed once per inspection and process, consistently within an inspection and freshly in the next one. Existing requirements are unchanged.

`source-identity-traceability` discovery requirements are preserved unchanged (no delta).

## Impact

- Code: `apps/orchestrator/src/acoustic_orchestrator/source_audio.py` (listing implementation and an inspection-scoped cache), `pipeline/static_plan.py` (enable the cache for `inspect_static_plan` and its planning worker processes). `experiment/sampler.py` and `config/validator.py` keep calling `list_source_wavs`.
- Tests: `apps/orchestrator/tests/test_sampler_audio.py`, `tests/test_static_plan.py`.
- No configuration, CLI, manifest or plan format changes; no new dependencies. The plan's generation contract value changes (see BREAKING above).
- Risk: a different ordering would silently change sampled scenes for an existing seed; ordering equivalence is the acceptance gate (unit test on mixed-case names plus a differential check on the real catalog).
