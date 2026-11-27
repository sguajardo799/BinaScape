# Proposal

## Why

Source databases organized as one folder per identity cannot currently be used through `source_types.audio_dir`, because discovery and validation only consider files at the root. Explicit identity metadata is also needed to trace selected recordings through rendering, including when runtime preparation replaces their original audio paths.

## What Changes

- Extend source audio discovery to WAVs directly under `audio_dir` and WAVs directly inside its immediate child directories, with each child directory name serving as the database identity.
- Add `identity_id` and `audio_relpath` to generated source records and carry them through scene manifests, runtime manifests, and rendered metadata.
- Keep flat directories supported with an empty identity; do not infer IDs from filenames.
- Preserve file-based sampling, including possible reuse of the same identity within a scene. Identity is descriptive only, not a sampling constraint or balancing mechanism.
- Document the supported one-level layout, metadata fields, compatibility behavior, and verification examples.

## Capabilities

### New Capabilities

- `source-identity-traceability`: Bounded discovery of identity-organized source audio and preservation of database identity and original relative recording paths across the static rendering pipeline.

### Modified Capabilities

None. The existing `circular-microphone-array` and `clarity-input-eligibility` capabilities do not own source database discovery or identity provenance.

## Impact

- Orchestrator: source directory validation, WAV discovery and pool construction, sampled source records, explicit source manifest construction, and runtime-manifest regression coverage.
- MATLAB: source context construction and preallocated/static source summaries feeding `summary.sources` in render metadata.
- Public contracts: additive per-source metadata fields; no new YAML keys, required identity configuration, output naming changes, or schema-version migration.
- Documentation and tests: orchestrator examples/README and MATLAB metadata documentation/tests. No asset migration or new dependency is required.
- Clarity continues linking its sidecars to the input render metadata; this change does not duplicate source identities into degraded sidecars or extend the output analyzer.
