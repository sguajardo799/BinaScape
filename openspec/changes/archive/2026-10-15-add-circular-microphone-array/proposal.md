# Proposal

## Why

The static pipeline currently renders only catalog-backed HRTF/HARTF receiver outputs. Users need raw circular-array recordings with configurable microphone count and radius, independently of binaural receiver catalogs.

## What Changes

- Add optional `receiver_outputs.circular_array` configuration for a uniformly spaced, horizontal, omnidirectional array without head or housing effects.
- Support array-only scenes and array outputs alongside existing HRTF/HARTF variants, using the sampled receiver center and yaw.
- Render one raw N-channel WAV and metadata sidecar per array variant through MATLAB/RAVEN room impulse responses, with deterministic microphone ordering.
- Extend scene/runtime manifests, output planning, indexing, validation, and resume handling to distinguish arrays from catalog-backed IR variants.
- Validate microphone locations against room geometry and configured source/receiver separation.
- **BREAKING**: reject HARTF and circular-array targets in `hearing_degradation.input_targets`; Clarity processing is restricted to selected binaural HRTF outputs, including their numbered variants. HARTF rendering remains available.
- Preserve existing HRTF/HARTF rendering configurations and legacy MATLAB manifests except for the explicitly narrowed Clarity eligibility.

## Capabilities

### New Capabilities

- `circular-microphone-array`: Configuration, horizontal geometry, raw multichannel rendering, output provenance, and coexistence with catalog-backed receiver outputs.
- `clarity-input-eligibility`: HRTF-only eligibility and explicit rejection of HARTF/array degradation targets. This documents a planned restriction of existing behavior, not a new degradation engine.

### Modified Capabilities

None. The project currently has no durable OpenSpec capabilities.

## Impact

- Orchestrator: `config/models.py`, `loader.py`, `validator.py`; `experiment/sampler.py`, geometry helpers, and `scene_builder.py`; `pipeline/output_paths.py`, `matlab_runner.py`, `render_pipeline.py`, `output_index.py`, and `clarity_handoff.py`.
- MATLAB: static manifest validation, receiver construction, RIR retrieval and convolution/mixing, static entrypoint, output naming, and metadata export. Existing ITA Toolbox/RAVEN dependencies remain; their multi-receiver RIR contract must be verified on the installed backend.
- Contracts: additive array descriptors, explicit output identity, N-channel audio validation, microphone/channel metadata, and rejection of previously accepted HARTF Clarity targets.
- Documentation/examples and Python/MATLAB regression tests; check output-analyzer consumption of the added metadata without broadening its feature set.
- Static rendering only. Dynamic rendering, beamforming, arbitrary array layouts, tilt, measured microphone directivity, housing scattering, and multichannel MSBG processing remain out of scope.
