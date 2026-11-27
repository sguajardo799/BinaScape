# Spec Delta

## Purpose

Define the planned HRTF-only input eligibility policy for orchestrator configuration and Clarity handoff. This intentionally restricts the existing behavior that accepts enabled HARTF targets; it does not change the standalone MSBG stereo processing algorithm.

## ADDED Requirements

### Requirement: Reject ineligible degradation targets
The orchestrator SHALL accept only `binaural_hrtf` as a base target in `hearing_degradation.input_targets`. Explicit `bte_rear_hartf`, `bte_front_hartf`, or `circular_array` targets SHALL cause a configuration error naming the ineligible target, including when degradation is disabled. Unknown targets SHALL remain invalid.

#### Scenario: HARTF target rejected
- **WHEN** configuration selects either HARTF output as a degradation target
- **THEN** validation rejects it and explains that only binaural HRTF outputs are eligible, without disabling HARTF rendering

#### Scenario: Two-microphone array rejected
- **WHEN** configuration selects a two-microphone circular array as a degradation target
- **THEN** validation rejects the target despite its stereo channel count

#### Scenario: Disabled degradation with invalid targets
- **WHEN** degradation is disabled but its target list includes `circular_array` or a HARTF output
- **THEN** validation still reports the ineligible target instead of retaining a latent invalid selection

### Requirement: Enabled degradation requires selected HRTF output
When hearing degradation is enabled, the orchestrator SHALL require a nonempty target list selecting an enabled binaural HRTF output. Array-only or HARTF-only configurations SHALL require degradation to be disabled and its target list empty.

#### Scenario: Array-only with degradation enabled
- **WHEN** only an array is enabled and degradation is enabled with an empty target list
- **THEN** validation rejects the configuration because no eligible HRTF target is selected

#### Scenario: Disabled HRTF selected
- **WHEN** degradation targets `binaural_hrtf` but that receiver output is absent or disabled
- **THEN** validation rejects the unavailable target

#### Scenario: Render-only array
- **WHEN** only an array is enabled, degradation is disabled, and its target list is empty
- **THEN** eligibility validation succeeds and no Clarity jobs are created

### Requirement: HRTF-only Clarity handoff
The orchestrator SHALL create or submit Clarity jobs only for selected, completed, valid binaural HRTF variants. Numbered variants such as `binaural_hrtf__1` SHALL inherit base HRTF eligibility. HARTF and array variants SHALL remain render-only even in mixed experiments; runtime handoff checks SHALL prevent their submission independently of configuration validation.

#### Scenario: Mixed receiver outputs
- **WHEN** a scene completes two binaural HRTF variants, HARTF variants, and an array with degradation enabled for `binaural_hrtf`
- **THEN** only the two HRTF variants generate degradation jobs for the selected hearing profiles, and all other outputs remain indexed as render outputs

#### Scenario: Ineligible variant reaches handoff
- **WHEN** stale or externally constructed variant records attempt to submit a HARTF or array output to Clarity
- **THEN** the handoff blocks submission with an explicit ineligibility reason

#### Scenario: Incomplete HRTF rendering
- **WHEN** a selected binaural HRTF variant is incomplete or fails render validation
- **THEN** no processing job for that output is submitted

### Requirement: Preserve eligible degraded output paths
Eligible HRTF degradation SHALL preserve the existing output layout, hearing-profile directories, rendered WAV basename, and adjacent JSON sidecar contract. This change SHALL NOT introduce an array-to-stereo conversion or alter the standalone backend's two-channel requirement.

#### Scenario: Eligible HRTF processed
- **WHEN** a selected HRTF variant is successfully degraded
- **THEN** its WAV and sidecar remain under the existing degraded output root and HRTF/profile subdirectories with the rendered WAV basename unchanged
