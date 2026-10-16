# Design

## Context

See `proposal.md` for motivation and the two delta specs for planned behavior. This design is not a claim of existing array support.

Observed integration constraints:
- `ReceiverOutputsConfig` currently requires `binaural_hrtf`; every output model requires a catalog and channel count. Loader and validator enumerate three fixed output names.
- `sampler._sample_hrtfs` samples catalog files for all enabled outputs. `scene_builder.build_static_manifest` indexes the first HRTF to choose primary output paths.
- `render_pipeline` plans and resumes variants by iterating `receiver.hrtfs`; `matlab_runner` creates single-HRTF runtime manifests. Index records require `receiver_ir_path` and metadata matching is HRTF-oriented.
- MATLAB validation normalizes a nonempty HRTF list. Receiver construction requires an active HRTF. Static rendering hardcodes BRIR generation and two-channel convolution/mixing. The entrypoint and metadata exporter also identify runs using `hrtf_id`.
- Schema 2.0/3.0 public poses use Y vertical; the MATLAB adapter reflects Z. Its existing helper ignores roll and is not a full rigid-body rotation.
- Background-noise fitting already accepts mono replication or exact channel matches. Clarity's standalone processor requires stereo; orchestrator target checks currently allow HARTF.

## Goals / Non-Goals

**Goals:**
- Extend existing explicit config, manifest, and variant machinery with a small array branch rather than a receiver plugin framework.
- Keep physical microphone positions and channel identity consistent across Python, MATLAB, WAVs, metadata, and resume validation.
- Preserve existing catalog-backed receiver rendering and legacy manifest loading.

**Non-Goals:**
- Redesign sampling, scheduling, output layout, RT30 quotas, or the MSBG algorithm.
- Simulate spatial diffuse background noise: existing post-mix noise semantics remain explicit.
- Introduce arbitrary geometry, array tilt, directivity catalogs, per-microphone WAVs, beamforming, or dynamic rendering.

## Decisions

### 1. Separate array config from catalog config

Add a literal `CircularArrayOutputConfig` alongside the existing catalog-backed model. Make `binaural_hrtf` optional, matching the other optional outputs, and add optional `circular_array`. Absence means no array. Use `enabled: false` and `required: false` defaults, with `output_subdir: circular_array`; enabled arrays require `num_microphones` and `radius_m`.

Planning defaults: strict integer count N >= 2; finite radius > 0; derive channel count from N. Disabled arrays can omit geometry; supplied geometry must still be valid. Reuse safe-relative-subdirectory and `required`/`enabled` consistency checks. At least one output must be enabled. Apply catalog path resolution only to catalog-backed outputs.

Alternative: force arrays into `ReceiverOutputConfig` with fake DAFF paths. Rejected because it misrepresents geometry-driven rendering and perpetuates catalog dependencies.

Example of the proposed public YAML:

```yaml
receiver_outputs:
  circular_array:
    enabled: true
    num_microphones: 8
    radius_m: 0.05
    output_subdir: circular_array
    required: true
hearing_degradation:
  enabled: false
  input_targets: []
  hearing_profiles_path: path/to/profiles.json
```

This excerpt does not remove other required experiment sections or change the existing profile-path rules.

### 2. Instantiate public geometry in the scene sampler

Keep one logical receiver. After sampling center/yaw, derive ordered positions with `theta_k = yaw + 360*k/N` and offsets `r*[cos(theta_k),0,sin(theta_k)]`. Ignore pitch/roll for the array only; do not modify their existing treatment for source placement or binaural receivers.

Planning conventions: zero-based `microphone_index`, one-based `channel_index`, microphone 0 on +X at zero yaw, angular order toward +Z. Use a small shared geometry helper in the orchestrator, not a parallel rendering engine. Store concrete geometry in the scene manifest; MATLAB validates count/order/radius consistency with numerical tolerance and reflects each position into RAVEN coordinates exactly once.

Reuse polygon containment and sampling retries for each microphone, including L-shaped/trapezoidal rooms. When source/receiver distance is checked, check the source against each microphone as well as the existing center constraint. Do not globally increase user sampling margins or replace valid placements with a conservative bounding-circle test.

Alternative: represent microphones as independently sampled receivers. Rejected because it loses the rigid geometry, single-array identity, and deterministic channel mapping.

### 3. Add an array descriptor and explicit runtime selection

Keep `receiver.hrtfs` for existing catalog entries. Add `receiver.circular_array` containing `output_type`, N, radius, center, yaw, and ordered microphone records with indices and `position_m`. Array-only manifests use an empty HRTF list. Retain schema 3.0 as an additive extension; older backend versions are not expected to accept array manifests. Existing schema 1.0/2.0/3.0 HRTF-only loading remains supported.

Generalize local variant enumeration to produce catalog variants plus one `circular_array` variant. Each array variant uses the existing `{scene_id}__{output_type}` identity and path rules. Preserve numbered HRTF identities. Choose the first planned variant deterministically for legacy primary output paths without indexing a nonexistent first HRTF.

Runtime manifests select one output through `render.active_output_type`. Catalog runtime manifests retain only their selected HRTF and remove the inactive array descriptor; array runtime manifests retain the descriptor and have no HRTFs. Shared prepared-source rewrites, seed, room, duration, and noise data remain intact. MATLAB direct scene rendering can normalize all descriptors into its existing run list when no explicit selection is present.

For new run records, use `output_type` as canonical identity; retain `hrtf_id` and `hrtf_path` on catalog runs for compatibility. Arrays must not manufacture HRTF identity/path values. Allow a null `receiver_ir_path` for arrays while preserving strings on catalog variants. Update entrypoint logging, output normalization/naming, and metadata batch summaries accordingly.

Alternative: replace the entire receiver schema with a polymorphic list. Rejected as unnecessary migration for one new receiver type.

### 4. Render array room responses without binaural filtering

Add an explicit array branch in MATLAB: construct omnidirectional receiver positions, enable RIR generation, disable BRIR generation, and retrieve source/microphone room responses. Keep the existing catalog BRIR branch unchanged. Generalize shared convolution and mixing to expected channel count N while preserving source timing, gain, duration/tail handling, and single global clipping protection.

Confirmed source-channel policy: for the array branch only, downmix multichannel source WAVs using the arithmetic mean across source channels before peak normalization and source gain. Mono sources remain unchanged. One acoustic point source feeds every microphone response; do not map stereo source channels to microphones. Record the original source channel count and downmix policy in source metadata. Keep existing HRTF/HARTF source handling unchanged.

Do not infer an API method name or response dimension order from the current repository: it only exercises `getBinauralImpulseResponseItaAudio`. The first implementation verification task must characterize the installed RAVEN RIR API using a small fixture with at least two sources and four receivers, validating source/channel order, absolute timing, sample rate, and T30 retrieval.

Prefer batched receivers if the installed API preserves the required responses. A per-microphone RIR loop is an acceptable backend adaptation if necessary, using identical scene/source settings and deterministic seed controls, preserving the backend time origin without independently aligning channels. If neither path satisfies the acoustic contract, stop and report a backend blocker; never duplicate a center RIR or approximate the array from a BRIR.

Reuse existing noise fitting for mono and N-channel layers; reject incompatible noise assets. Mono replication is correlated post-mix noise, not a spatial acoustic model.

### 5. Make provenance and resume array-aware

Metadata includes `output_type: circular_array` and a `receiver.circular_array` descriptor in public coordinates, with each microphone's channel mapping. If backend positions are reported, label them separately as RAVEN coordinates. Preserve `summary.output_num_channels`, source summaries, effective seed, and `summary.room.reverberation.t30_s`, which is consumed by render acceptance logic.

Extend array output validation to inspect WAV channel count/sample rate and compare array count, radius, center, yaw, and ordered positions against the planned descriptor. When metadata is disabled, compare the saved runtime descriptor plus WAV properties; if provenance is unavailable, do not resume as completed. Do not require an IR file for arrays. Preserve current catalog metadata/index compatibility and resume behavior. Inspect output-analyzer readers for assumptions before changing metadata consumers; no new analyzer feature is required.

Alternative: identify arrays only by filename and channel count. Rejected because different radii/yaws can share both and would incorrectly resume stale output.

### 6. Restrict Clarity by receiver type, not channel count

Validate `hearing_degradation.input_targets` against the HRTF-only base-target policy, even when degradation is disabled. Enabled degradation additionally requires `binaural_hrtf` to be present and enabled. Empty targets are valid only with degradation disabled. Reject HARTF and arrays explicitly, including N=2 arrays.

Use the existing numeric suffix normalization for generated HRTF variants at runtime, not arbitrary user target aliases. Apply eligibility at both job creation and submission/issue validation so stale jobs cannot bypass the config policy. Mixed runs still render every enabled receiver output, but generate degradation jobs only for selected valid HRTFs. The standalone backend and degraded file layout stay unchanged.

Alternative: silently filter invalid targets. Rejected because it conceals a user configuration error and an intentional compatibility break.

## Risks / Trade-offs

- [Installed RAVEN RIR API/order is unverified] -> Characterize it first with a multi-source fixture; keep mocked tests separate from real-backend evidence and block completion if the physical contract is unmet.
- [Coordinate reflection reverses apparent angular direction] -> Establish ordering in public coordinates, reflect positions only in MATLAB, and test yaw 0/90 with asymmetric source placement.
- [Render cost grows with N and source count] -> Prefer batched RIR retrieval; preserve existing worker isolation and avoid an unrequested scheduling redesign.
- [Existing HARTF degradation configs break intentionally] -> Provide named-target errors and migration examples while retaining HARTF rendering.
- [New metadata without `hrtf_id` exposes consumer assumptions] -> Update run export/index paths and test analyzer compatibility; do not encode arrays as fake HRTFs.
- [Array extents reduce valid placements] -> Check actual microphone positions, reuse bounded retries, and preserve diagnostic sampling failures.
- [Noise replication is physically limited] -> Document its existing post-mix semantics rather than claiming diffuse-field simulation.

## Migration Plan

1. Ship coordinated orchestrator/MATLAB contract support; array manifests require the updated MATLAB backend. Keep legacy HRTF/HARTF manifests readable.
2. Existing render-only and HRTF-degradation configurations require no edits. Configs listing HARTF degradation targets must remove those targets; HARTF-only users must disable degradation and use an empty target list.
3. Supply array-only and mixed examples, documenting planning defaults, geometry, channel order, and noise limitations. The local example YAML currently uses older room dimensions while current models require geometry; reconcile the affected examples with current models rather than copying the obsolete structure.
4. Run Python/MATLAB regressions and installed-RAVEN smoke verification before claiming array support. Preserve T30 acceptance/retry behavior for array-only scenes.
5. Rollback: disable/remove the array block before using an older backend. Keep new multichannel artifacts isolated under their output subdirectory; older releases are not expected to process new array manifests or indexes. Do not revert user artifacts or discard generated outputs.
