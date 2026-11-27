# Spec Delta

## MODIFIED Requirements

### Requirement: Preserve eligible degraded output paths
Eligible HRTF degradation SHALL write each processor's output under `output_audio/degraded/<processor>/<output_type>/<hearing_profile_id>/`, keeping the rendered WAV basename and the adjacent JSON sidecar contract. Outputs of different processors for the same variant and profile SHALL NOT share a path. This change SHALL NOT introduce an array-to-stereo conversion or alter the standalone backend's two-channel requirement.

#### Scenario: Eligible HRTF processed
- **WHEN** a selected HRTF variant is successfully degraded by a processor
- **THEN** its WAV and sidecar are under the degraded output root, in that processor's directory and the HRTF/profile subdirectories, with the rendered WAV basename unchanged

#### Scenario: Two processors for the same variant
- **WHEN** a selected HRTF variant is degraded by `msbg` and `whis` for the same hearing profile
- **THEN** both outputs exist side by side under `degraded/msbg/...` and `degraded/whis/...` and neither overwrites the other
