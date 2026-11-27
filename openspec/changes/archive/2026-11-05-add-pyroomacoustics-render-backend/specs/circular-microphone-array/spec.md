## MODIFIED Requirements

### Requirement: Raw synchronized multichannel rendering
The selected render backend SHALL render each source through the room impulse response at each array microphone and mix sources into one N-channel WAV at a render sample rate supported by that backend. That backend is MATLAB/RAVEN for `render.backend: raven`, or the Pyroomacoustics worker for `render.backend: pyroomacoustics`. WAV channel k+1 SHALL correspond to microphone k. Rendering SHALL preserve relative propagation delays and interchannel levels without head filtering, beamforming, per-channel normalization, or per-microphone onset alignment. Backends SHALL NOT be required to produce numerically identical recordings for the same scene.

#### Scenario: Eight-channel output
- **WHEN** an eight-microphone array scene is rendered
- **THEN** the WAV has exactly eight synchronized channels at 44100 Hz and includes the configured source gains, offsets, and duration handling

#### Scenario: Unequal source distances
- **WHEN** a source is closer to one microphone than another
- **THEN** the recording preserves the backend's relative arrival-time and level differences rather than independently aligning or normalizing channels

#### Scenario: Same contract with either backend
- **WHEN** the same array scene configuration is rendered once with `render.backend: raven` and once with `render.backend: pyroomacoustics`
- **THEN** both outputs satisfy the same channel count, channel-to-microphone mapping, sample rate and duration contract, while their sample values are not required to match
