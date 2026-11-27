# Spec Delta

## Purpose

Define the planned static circular-array contract between experiment YAML, orchestrator scene/runtime manifests, MATLAB rendering, and indexed audio outputs. These requirements introduce behavior not yet implemented by the current HRTF/HARTF-only renderer.

## ADDED Requirements

### Requirement: Optional circular array configuration
The orchestrator SHALL accept an optional `receiver_outputs.circular_array` with `enabled`, `num_microphones`, `radius_m`, `output_subdir`, and `required`. An enabled array SHALL require an integer microphone count of at least two and a finite positive radius in meters, without an IR catalog or separately configurable channel count.

#### Scenario: Valid array configuration
- **WHEN** an experiment enables an eight-microphone array with `radius_m: 0.05` and a safe relative output subdirectory
- **THEN** configuration validation succeeds without requiring array HRTF/HARTF assets

#### Scenario: Invalid array geometry
- **WHEN** an enabled array has a fractional count, fewer than two microphones, or a zero, negative, or non-finite radius
- **THEN** validation rejects the configuration with an error identifying the invalid field

#### Scenario: Array omitted or disabled
- **WHEN** the array configuration is absent or disabled and another receiver output is enabled
- **THEN** the pipeline plans no array variant and preserves existing catalog-backed rendering behavior

### Requirement: Independent and combined receiver outputs
The pipeline SHALL support an enabled array as the sole receiver output or alongside enabled HRTF/HARTF outputs. At least one receiver output SHALL be enabled. Array-only rendering SHALL NOT require a binaural configuration or IR catalog.

#### Scenario: Array-only experiment
- **WHEN** only `circular_array` is configured and enabled, with hearing degradation disabled
- **THEN** scene generation and rendering plan one array variant without accessing an HRTF catalog

#### Scenario: Combined experiment
- **WHEN** an array, two sampled binaural HRTFs, and a HARTF output are enabled
- **THEN** the scene produces one array variant and the three independent catalog-backed variants with distinct output paths

#### Scenario: No receiver enabled
- **WHEN** no receiver output is enabled
- **THEN** configuration validation rejects the experiment

### Requirement: Horizontal yaw-only microphone geometry
The array SHALL consist of uniformly spaced omnidirectional point microphones without head or housing effects. Its center SHALL equal the sampled receiver position. For zero-based microphone index k, its public position SHALL be `center + radius_m * [cos(theta_k), 0, sin(theta_k)]`, where `theta_k = yaw + 360*k/N` in degrees. Pitch and roll SHALL NOT change the array geometry.

#### Scenario: Four microphones at zero yaw
- **WHEN** a four-microphone array has center `[cx, cy, cz]`, radius r, and yaw zero
- **THEN** microphone offsets in order are `[r,0,0]`, `[0,0,r]`, `[-r,0,0]`, and `[0,0,-r]`, within numerical tolerance

#### Scenario: Yaw rotation without tilt
- **WHEN** receiver yaw changes by 90 degrees while pitch or roll also changes
- **THEN** microphone 0 moves from the positive X offset to the positive Z offset and every microphone retains the center's Y coordinate

### Requirement: Validate the microphone footprint
The orchestrator SHALL validate every microphone against enabled receiver-inside-room checks and the configured minimum source-to-receiver distance, not only the center. Invalid sampled geometry SHALL be retried within existing attempt limits and reported through existing sampling-failure behavior if no valid scene is obtained.

#### Scenario: Center inside but microphone outside
- **WHEN** receiver-inside-room validation is enabled and an array center is inside an L-shaped footprint but a microphone lies outside it
- **THEN** the placement is rejected rather than generating an invalid render manifest

#### Scenario: Source too close to an outer microphone
- **WHEN** a source satisfies the center distance but violates the configured minimum distance to an array microphone
- **THEN** the scene placement is rejected or resampled within the existing attempt limits

### Requirement: Explicit array manifest identity
Scene and runtime manifests SHALL distinguish circular-array descriptors from catalog-backed `receiver.hrtfs`. They SHALL identify the array output as `circular_array`, specify its microphone count, radius, and ordered public microphone positions, and allow an empty HRTF list when an array is active. Runtime manifests SHALL select exactly the intended receiver variant.

#### Scenario: Array runtime manifest
- **WHEN** the orchestrator prepares the array variant of a combined scene
- **THEN** its runtime manifest selects the array, retains source timing/gains and prepared audio paths, and does not require an active HRTF

#### Scenario: Legacy binaural manifest
- **WHEN** MATLAB receives a previously supported HRTF-only manifest without an array descriptor
- **THEN** it retains the existing normalization, variant identities, and binaural rendering path

### Requirement: Raw synchronized multichannel rendering
MATLAB SHALL render each source through the room impulse response at each array microphone and mix sources into one N-channel WAV at the supported render sample rate. WAV channel k+1 SHALL correspond to microphone k. Rendering SHALL preserve relative propagation delays and interchannel levels without head filtering, beamforming, per-channel normalization, or per-microphone onset alignment.

#### Scenario: Eight-channel output
- **WHEN** an eight-microphone array scene is rendered
- **THEN** the WAV has exactly eight synchronized channels at 44100 Hz and includes the configured source gains, offsets, and duration handling

#### Scenario: Unequal source distances
- **WHEN** a source is closer to one microphone than another
- **THEN** the recording preserves the backend's relative arrival-time and level differences rather than independently aligning or normalizing channels

### Requirement: Array output provenance and validation
The array variant SHALL use existing output layout and naming patterns with `output_type: circular_array`. Its metadata SHALL record count, radius, center, yaw, ordered microphone/channel mapping, and public positions, distinguishing backend coordinates when included. Output indexing and resume validation SHALL check the expected N-channel shape and array provenance without requiring a receiver IR file.

#### Scenario: Output metadata
- **WHEN** an array render completes
- **THEN** its WAV and sidecar are indexed as one `circular_array` variant and the sidecar maps each WAV channel to its microphone position

#### Scenario: Invalid resumed output
- **WHEN** an existing array output has the wrong channel count or incompatible array geometry metadata
- **THEN** it is not considered a valid completed variant for resume

### Requirement: Downmix array source audio
Array rendering SHALL downmix multichannel source audio to mono using the arithmetic mean across channels before peak normalization and source gain. Mono sources SHALL remain mono. Metadata SHALL record original channel count and downmix policy. HRTF/HARTF source channel handling SHALL remain unchanged.

#### Scenario: Stereo source rendered through an array
- **WHEN** an array scene uses a stereo source WAV
- **THEN** the mean of its left and right signals feeds every microphone's room response, and metadata records two original channels and arithmetic-mean downmixing

#### Scenario: Mono source rendered through an array
- **WHEN** an array scene uses a mono source WAV
- **THEN** no downmix is performed and the same source signal feeds every microphone response

### Requirement: Preserve supported background noise semantics
Array rendering SHALL retain the existing configured background-noise behavior for channel-compatible noise: mono layers replicated across channels or layers matching N channels. Incompatible noise channel counts SHALL produce a clear error. Replicated noise SHALL NOT be represented as a simulated spatial diffuse field.

#### Scenario: Mono noise with an array
- **WHEN** an eight-channel array output uses a configured mono background-noise layer
- **THEN** the existing duration fitting and SNR handling apply with that layer replicated across all eight channels

#### Scenario: Stereo noise with eight microphones
- **WHEN** an eight-channel array output uses a two-channel noise asset
- **THEN** rendering rejects the channel mismatch rather than silently inventing a spatial conversion
