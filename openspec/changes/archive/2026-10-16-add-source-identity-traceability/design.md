# Design

## Context

See `proposal.md` for motivation and `specs/source-identity-traceability/spec.md` for the planned behavior contract.

Observed implementation boundaries:

- `apps/orchestrator/src/acoustic_orchestrator/config/models.py` already exposes `SourceTypeConfig.audio_dir`; the loader resolves it relative to the experiment configuration.
- `config/validator.py` currently checks for any immediate file, whereas `experiment/sampler.py::_list_files` filters only immediate WAVs. These checks both exclude roots containing only ID folders and disagree about sidecar-only directories.
- `_build_audio_pools` and `_draw_audio_path` implement per-scene file selection without replacement until pool exhaustion. `_sample_sources` creates a fresh scene-local `source_id` for every source.
- `experiment/scene_builder.py::_build_source_manifest` explicitly copies allowed source fields, so adding fields to sampled records alone is insufficient.
- `pipeline/matlab_runner.py` deep-copies the manifest and can rewrite `audio_path` using prepared WAVs from `pipeline/runtime_audio.py`. Their names contain the scene source ID, not database identity.
- MATLAB's `core/build_sources_from_config.m` explicitly constructs `ctx.sources`. `core/build_static_source_summary.m` constructs a second explicit representation, whose fields must match `core/preallocate_static_source_summaries.m`. `core/export_render_metadata.m` already exports the resulting `summary.sources`.

This design is included because provenance crosses the Python/MATLAB contract and several explicit field-copy boundaries.

## Goals / Non-Goals

**Goals:**
- Derive identity once, while the original configured directory is known.
- Keep one candidate-discovery rule for validation and sampling.
- Use additive scalar string fields compatible with uniform MATLAB struct arrays and old manifests.
- Preserve random-number consumption and flat-input selection order.

**Non-Goals:**
- Identity-aware balancing, uniqueness constraints, train/test partitioning, or per-ID weights.
- Arbitrary recursive layouts, filename parsing, lookup tables, or a dataset registry.
- Reorganizing existing assets, modifying background-noise discovery, changing audio processing, or implementing dynamic rendering.
- New analyzer reports or copying provenance into Clarity sidecars; Clarity's existing link to render metadata remains the downstream traceability route.

## Decisions

### 1. Extend the existing directory contract without configuration flags

Collect WAVs at `audio_dir/*.wav` and `audio_dir/<identity>/*.wav`, accepting `.WAV` and other case variants. Allow both layouts in the same directory; ignore non-WAVs and deeper descendants. Immediate child folder names are opaque IDs, preserved exactly rather than constrained to an `ID<number>` pattern. Empty child folders contribute no candidates and do not invalidate an otherwise usable root.

Implement one small source-audio discovery helper in a neutral orchestrator module, for example `acoustic_orchestrator/source_audio.py`, used by both validation and sampling. Keep the existing sampler helper as a thin delegate if useful for local compatibility; do not make config validation import the sampler. Return paths rather than introduce a parallel model hierarchy. Sort by the relative path using native path ordering so root-only inputs preserve the existing `sorted(Path)` behavior on the same platform. Serialize relative paths with forward slashes independently of that ordering.

Validation first verifies an existing directory and only then enumerates candidates, preserving actionable missing/not-directory errors. A candidate pool must contain at least one eligible WAV; a valid header remains an audio-read concern, not a new exhaustive validation step.

Alternatives: recursive globbing would silently assign meaning to sessions and other deep folders; an identity-layout switch adds configuration without a demonstrated need; separate validation logic would continue the present mismatch.

### 2. Add two provenance strings, independent of scene identity

For every newly sampled source, compute its path relative to the configured audio root before any preparation:

| Field | Grouped recording | Flat recording |
| --- | --- | --- |
| `source_id` | `src_0001` | `src_0002` |
| `identity_id` | `ID02` | `""` |
| `audio_relpath` | `ID02/recording.wav` | `recording.wav` |
| `audio_path` | Original absolute WAV path | Original absolute WAV path |

`identity_id` is scoped to the configured source database, not a globally unique person identifier. `event_type` and the recording path remain available as context. `audio_relpath` disambiguates identical basenames and does not become the runtime loading path. Flat filenames containing IDs are not parsed. If `audio_dir` itself points to a folder called `ID02`, its direct WAVs are still flat input with unknown identity: use the database parent to obtain folder-derived identity.

New manifests always include both strings, including empty identities, to avoid heterogeneous source objects for mixed layouts. Extend the scene builder's explicit copy boundary. Legacy helper inputs without the fields can default to empty strings rather than deriving identity from an absolute path.

Alternatives: replacing `source_id` confuses scene instances with database subjects; `speaker_id` is speech-specific; filename inference is database-specific; optional absent fields on only some new sources complicate MATLAB object-array decoding. A nested provenance object or an additional original absolute path is unnecessary for the confirmed scope.

### 3. Do not change sampling policy or add random draws

Continue building one file pool per event type and drawing files with the existing removal/reset behavior. Derive metadata deterministically after selecting the path. Do not first choose an ID and then a recording: that would introduce identity balancing and change probability weights. Identities with more files therefore remain more likely to be sampled. Two sources can have the same `identity_id` even before the file pool is exhausted.

For unchanged flat inputs, preserve existing file ordering and random-number consumption. Test seeded baseline compatibility rather than only checking that two new runs match each other. Grouped and mixed inputs naturally have a new candidate set and are not expected to reproduce former flat-input choices.

### 4. Preserve provenance through each explicit boundary

```text
audio_dir/ID02/recording.wav
             |
             v
sampled source --> scene_builder --> saved scene manifest
                                          |
                                          v
                           runtime receiver manifest
                           audio_path may be rewritten
                           provenance stays unchanged
                                          |
                                          v
                             MATLAB source context
                                          |
                                          v
                               summary.sources
                                          |
                                          v
                               render metadata JSON
```

The runtime writer's existing deep copy should already retain new fields; add regression tests rather than duplicate provenance logic there. Never recover identity from prepared filenames. No change to trimmed filename generation, output names, or acoustic computation is needed.

In MATLAB, carry both fields from manifest sources into `ctx.sources`, then into the static source summary. Update both the summary constructor and preallocation template in the same implementation change, with matching field names and ordering. Apply the existing optional-string conventions: missing or empty fields become `''`, yielding JSON empty strings. Keep legacy manifests and existing schema versions valid. Exercise normalization of mixed source objects as well as source construction. The generic metadata exporter should need no new business logic; verify it preserves the added summary fields.

Alternatives: adding fields only to the exporter is too late because earlier transformations discard them; teaching each receiver renderer to rediscover identity would duplicate logic and fail after trimming.

### 5. Bound the public compatibility commitment

This is an additive source-record extension, with no required YAML changes or schema-version bump. Existing flat WAV selection remains compatible; newly generated flat records gain an empty identity and a nonempty original relative filename. Existing MATLAB source manifests remain valid even if one or both fields are missing, and their missing fields export as empty strings.

The authoritative exported provenance is `summary.sources` in render metadata when enabled, not the orchestrator's compact output-index snapshot. Keep current Clarity handoff, degraded-sidecar structure, analyzer behavior, and output naming unchanged. Document this boundary so users know where to inspect identity.

## Risks / Trade-offs

- [An immediate directory might be a category rather than a person] -> Document that every immediate folder is treated as an opaque database identity; do not claim semantic person recognition.
- [Uneven recording counts imply uneven identity frequency] -> Preserve file-based selection and explicitly document that balancing is outside this stage.
- [MATLAB struct-array field mismatch can break rendering] -> Update source templates and constructors together; test flat/grouped mixtures and legacy missing fields without RAVEN.
- [Validation could accidentally change background-noise behavior] -> Scope the shared helper to source audio and retain separate noise strategy discovery.
- [Original path information is lost after trimming] -> Capture `audio_relpath` before preparation and test unchanged fields across receiver variants.
- [External strict consumers may reject additive JSON fields] -> Document the extension, retain old fields and schema versions, and provide flat and grouped examples; no historical artifacts are rewritten.
- [MATLAB/RAVEN may be unavailable in automated environments] -> Run MATLAB helper/export tests independently where possible and report any unavailable live-render smoke test explicitly.

## Migration Plan

1. Implement and verify orchestrator and MATLAB support together before generating grouped-database runs.
2. Add documentation/examples for the directory layout and additive metadata contract without moving existing assets.
3. Existing flat configurations need no edits; grouped configurations point `audio_dir` at the parent of the ID folders. Existing manifests and historical metadata remain valid and are not backfilled.
4. Rollback is a code rollback for future runs. Preserve generated artifacts; an older orchestrator will again require flat audio directories, and an older MATLAB renderer will not guarantee export of the new provenance fields.
