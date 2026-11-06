## Purpose

Render static circular-array variants without MATLAB, ITA Toolbox or RAVEN by computing image-source room impulse responses with Pyroomacoustics and synthesizing the multichannel recording in Python, while producing the same artifacts and attempt evidence that the existing static render pipeline validates, indexes and resumes.

## ADDED Requirements

### Requirement: Isolated worker process with the render entrypoint contract
Each Pyroomacoustics render attempt SHALL run as a separate operating-system process launched by the orchestrator through the existing per-variant render dispatch, never inside the orchestrator's process. It SHALL write the WAV and sidecar at `render.output_wav_path` and `render.output_metadata_path`.

#### Scenario: Successful worker attempt
- **WHEN** the orchestrator dispatches a Pyroomacoustics array variant with a fresh attempt
- **THEN** a child process reads the attempt snapshot, writes the N-channel WAV, the sidecar and the completion record, and exits with code 0; the attempt record shows `process_status: succeeded`

### Requirement: Worker exit status and publication boundary
The worker SHALL exit with code 0 only after every inventoried output and, when a completion path is provided, the completion record have been published. Any unhandled error SHALL produce a non-zero exit code, preserving MATLAB's process semantics for attempt tracking, acceptance and retry.

#### Scenario: Worker crash
- **WHEN** the worker raises an unexpected exception during impulse-response computation
- **THEN** the process exits non-zero, no completion record is published, and the attempt is classified by the existing retry policy as for a MATLAB process failure

### Requirement: Immutable worker inputs
The worker SHALL read only the attempt's immutable runtime-manifest snapshot and its context: attempt identifier, variant identifier, manifest SHA-256, completion path, failure path and optional diagnostic events path.

#### Scenario: Snapshot is the only input
- **WHEN** the runtime manifest file is modified after the attempt snapshot was taken
- **THEN** the worker renders from the snapshot and its completion record carries the snapshot's SHA-256

### Requirement: Existing MATLAB-process obligations apply to the worker
The worker SHALL fulfil the MATLAB-process and entrypoint obligations of `matlab-process-retries`, `matlab-render-diagnostics` and `pipeline-progress` using the same record formats. Configuration keys, summary keys and directory names containing `matlab` SHALL keep their current names.

#### Scenario: Existing process configuration and evidence contracts
- **WHEN** a Pyroomacoustics attempt is tracked, diagnosed or reported
- **THEN** the existing process record formats and `matlab`-named configuration, summary keys and directories are retained

### Requirement: Deterministic worker input failures
When an input manifest fails to load or validate, the worker SHALL publish the versioned deterministic-failure report with category `invalid_input` and exit non-zero. The existing policy SHALL classify it as deterministic and not retry it.

#### Scenario: Invalid snapshot
- **WHEN** the worker's snapshot lacks the `circular_array` descriptor for an array variant
- **THEN** the worker publishes a `failure.json` with category `invalid_input` linked to the attempt and manifest hash, exits non-zero, and the attempt is classified as a deterministic failure that is not retried

### Requirement: Shared worker retry and recovery policy
`execution.matlab_process_retry`, the batch circuit breaker, persistent retry chains, remnant isolation and `reconcile-static` SHALL govern worker invocations unchanged.

#### Scenario: Retry budget shared with MATLAB semantics
- **WHEN** a worker invocation fails with a non-zero exit, observed termination and no accepted render
- **THEN** it consumes one invocation of the chain configured by `execution.matlab_process_retry` and counts toward the consecutive-failure circuit breaker

### Requirement: Worker diagnostic events
The worker SHALL emit the structured diagnostic events for `initialization`, `simulation` and `artifact_writing`, the `artifact_written` events and `entrypoint_completed`, in the existing event format. Terminal progress SHALL report worker activity from these events.

#### Scenario: Diagnostic event stream
- **WHEN** a worker attempt runs with diagnostics enabled
- **THEN** its events file contains, in sequence order, `stage_started`/`stage_completed` for `initialization`, `simulation` (with steps `pyroomacoustics_rir` and `audio_postprocessing`) and `artifact_writing`, `artifact_written` events for the WAV and the metadata, and finally `entrypoint_completed`

### Requirement: Worker invocation diagnostics
The orchestrator SHALL record per-invocation identity, command, timing, exit code and console log for worker invocations.

#### Scenario: Worker invocation evidence
- **WHEN** the orchestrator invokes a worker with diagnostics enabled
- **THEN** `invocation.json` records identity, command, timing and exit code, and `console.log` captures the process console output

### Requirement: Explicit reversible coordinate transform
The backend SHALL map public meter coordinates `(x, y, z)` (Y-up) to `(X, Y, Z) = (x - min x, max z - z, y)`, using room-footprint extrema. The inverse SHALL be `(x, y, z) = (X + min x, Z, max z - Y)`. The transform SHALL be rigid: rotation `(x, -z, y)` with determinant +1, then translation `(-min x, max z, 0)`, preserving distances and propagation times with no vertical translation.

#### Scenario: Round trip
- **WHEN** any finite public point is transformed to the backend frame of a room and back
- **THEN** the result equals the original within 1e-9 m

#### Scenario: Room anchored at the backend origin
- **WHEN** a shoebox room with a normalized public footprint is transformed
- **THEN** every transformed footprint vertex lies in the backend box `[0, length] x [0, width]`, and every in-room public point maps inside the backend room

#### Scenario: Vertical axis
- **WHEN** a microphone is at public height `y = 1.5`
- **THEN** its backend `Z` coordinate is 1.5 and the floor is at backend `Z = 0`

### Requirement: Coordinate provenance and existing public frames
Render metadata SHALL record the transform's rotation matrix and translation. Existing manifest coordinates, array descriptors and sidecar fields SHALL retain their frames, including legacy `summary.sources` poses. Backend-frame coordinates SHALL appear only in clearly labeled backend metadata.

#### Scenario: Public metadata unchanged
- **WHEN** a Pyroomacoustics array variant is rendered
- **THEN** `receiver.coordinate_frame` is `public_xyz_y_up`, and the microphone positions in `receiver.circular_array` equal those of the runtime manifest

### Requirement: Room geometry construction and surface mapping
The backend SHALL construct the room from the manifest's `room.geometry`: the footprint vertices `[x, z]`, `height_m` and `wall_ids`. Shoebox rooms SHALL be built as rectangular boxes. Trapezoid and L-shape rooms SHALL be built by extruding the transformed footprint from the floor to `height_m`.

Any geometry type not supported before this change SHALL be rejected as invalid input.

#### Scenario: Unknown geometry type
- **WHEN** a snapshot contains a geometry type other than shoebox, trapezoid or L-shape
- **THEN** the worker publishes an `invalid_input` failure report

### Requirement: Transformed footprint winding
Footprint winding SHALL be normalized to the orientation the backend requires after transformation, without changing the footprint area.

#### Scenario: Winding reversal
- **WHEN** a counter-clockwise public footprint is transformed with `Y = -z`
- **THEN** the backend polygon is reordered to the required orientation, and the backend footprint area equals the public footprint area within 1e-9 m²

### Requirement: Physical surface identity
Every backend wall SHALL receive the acoustic surface of the wall connecting the same two physical vertices in the public footprint. The floor and ceiling SHALL receive the `floor` and `ceiling` surfaces.

#### Scenario: Wall material identity
- **WHEN** an L-shaped room assigns a distinct material to each of its six walls
- **THEN** for every backend wall, the inverse-transformed midpoint of its footprint edge equals the midpoint of the public edge of the same `wall_id`, within 1e-9 m

### Requirement: Explicit material conversion without equivalence claims
For each surface, the backend SHALL derive octave-band energy-absorption coefficients from the manifest's 31-band `effective_absorption`. It SHALL use the existing center-frequency mapping, which takes the third-octave band at each octave center, at the ten octave centers 31.5 Hz to 16 kHz. This is the mapping used by the Sabine estimator. Scattering coefficients SHALL be derived the same way from `effective_scattering`.

#### Scenario: Octave mapping
- **WHEN** a surface's 31-band absorption vector has index 17 (1000 Hz) equal to 0.12
- **THEN** the backend's 1000 Hz absorption coefficient for that surface is 0.12

#### Scenario: Out-of-range coefficient
- **WHEN** a converted absorption coefficient is outside [0, 1] or non-finite
- **THEN** the worker publishes an `invalid_input` failure report instead of clamping it

### Requirement: Converted material provenance
Render metadata SHALL record each surface's converted octave values and whether scattering influenced the simulation. Under ISM-only simulation, scattering SHALL be reported as not applied. Metadata and documentation SHALL NOT claim acoustically equivalent rooms between Pyroomacoustics and RAVEN.

#### Scenario: Scattering under ISM
- **WHEN** an ISM-only Pyroomacoustics variant is rendered
- **THEN** the metadata lists each surface's converted scattering values, with `scattering_applied: false`

### Requirement: Image-source-only simulation with explicit parameters
The backend SHALL compute room impulse responses with the Image Source Method only. It SHALL use `pyroomacoustics.max_order` for shoebox rooms and `max_order_non_shoebox` for extruded rooms, the configured air-absorption flag, and the configured temperature and humidity when present (otherwise the backend's default propagation physics). Ray tracing and randomized image sources SHALL be disabled.

#### Scenario: Anechoic configuration
- **WHEN** `max_order` is 0 and the room is a shoebox
- **THEN** each microphone's impulse response contains only the direct-path contribution of each source

#### Scenario: Ray tracing never enabled
- **WHEN** any Pyroomacoustics variant is rendered
- **THEN** the metadata records `ray_tracing: false` and `randomized_image_sources: false`

### Requirement: Simulation parameter provenance
Render metadata SHALL record the effective ISM orders, air-absorption flag, temperature, humidity, effective speed of sound, RIR high-pass filter settings and fractional-delay length.

#### Scenario: Effective simulation parameters recorded
- **WHEN** a Pyroomacoustics variant completes
- **THEN** its backend metadata contains the effective orders, atmospheric parameters, propagation speed and RIR filter and fractional-delay settings

### Requirement: Circular-array receiver rendering
For the `circular_array` variant, the backend SHALL place one omnidirectional microphone at each `receiver.circular_array.microphones[k].position_m` of the snapshot, after coordinate transformation, in increasing `microphone_index` order. It SHALL compute one impulse response per (source, microphone) pair. WAV channel `channel_index` SHALL carry microphone `channel_index - 1`.

#### Scenario: Three-microphone array
- **WHEN** a snapshot describes a three-microphone array with radius 0.05 m
- **THEN** the backend renders three microphones at the three descriptor positions, and the WAV has three channels in descriptor order

### Requirement: Unmodified array geometry and channel relationships
The backend SHALL NOT recompute microphone positions from radius or yaw. It SHALL NOT align, normalize or beamform channels individually, preserving relative arrival times and levels.

#### Scenario: Channel ordering by arrival
- **WHEN** an anechoic scene places a single source 1 m from the array center along the public direction of microphone k
- **THEN** channel k+1 has the earliest direct-path arrival of all channels

### Requirement: Python signal synthesis reproducing the static array semantics
The backend SHALL synthesize at the render sample rate in this order: source loading/resampling, downmix, source peak normalization, gain, full per-microphone convolution, optional reverb-tail trim, offset placement, source summation, target-duration trim/pad, first peak guard, then background noise and its second guard. The following requirements define each stage's unchanged semantics.

#### Scenario: Synthesis stage ordering
- **WHEN** a variant uses gain, offsets, target duration and noise
- **THEN** preparation precedes convolution, trimming precedes offset placement, and duration handling and the first peak guard precede noise

### Requirement: Source preparation and downmix provenance
The backend SHALL load the prepared source WAV, polyphase-resample to the render rate if needed, downmix multichannel audio by arithmetic mean, peak-normalize to 0.99 (zero signal unchanged), then apply `10^(gain_db/20)`, in that order. Sidecar source summaries SHALL record the original channel count and the `arithmetic_mean` or `none` downmix policy.

#### Scenario: Stereo source
- **WHEN** a stereo source is rendered through the array
- **THEN** the mean of its two channels feeds every microphone response, and the sidecar records `original_num_channels: 2` and `downmix_policy: arithmetic_mean`

### Requirement: Full convolution and optional tail trim
Each prepared source SHALL be fully linearly convolved with every microphone response, yielding `len_source + len_rir - 1` samples. When `render.trim_reverb_tail` is true, the backend SHALL drop `len_rir - 1` trailing samples, where `len_rir` is that source's longest microphone response.

#### Scenario: Reverb-tail trimming
- **WHEN** tail trimming is enabled for a source
- **THEN** the source's full convolution is shortened by its longest microphone response length minus one, before offset placement

### Requirement: Source offsets and linear mixing
The backend SHALL place each convolved and optionally trimmed source at `max(0, round_half_away_from_zero(start_time_s * fs))` samples, then sum all sources into a buffer whose length is the maximum end sample.

#### Scenario: Half-sample rounding
- **WHEN** `start_time_s * fs` equals 22050.5
- **THEN** the source starts at sample index 22051, not at the banker's-rounded 22050

#### Scenario: Linear mixing
- **WHEN** two sources are rendered individually and together with identical parameters, and no peak guard or noise is triggered
- **THEN** the combined output equals the sum of the individual outputs within 1e-6 absolute

### Requirement: Target duration and first peak guard
After summation, when `target_duration_s` is present, the backend SHALL truncate or zero-pad to `max(1, round_half_away_from_zero(target_duration_s * fs))` samples. It SHALL then scale the whole mix to peak 0.999 if its peak exceeds 0.999, before adding noise.

#### Scenario: Target duration
- **WHEN** `target_duration_s` is 2.0 at 44100 Hz
- **THEN** the WAV has exactly 88200 samples per channel

### Requirement: Background noise with existing semantics
When background noise is enabled, the backend SHALL apply every configured layer after the first peak guard, independently scaled to its `snr_db` against the pooled RMS of the pre-noise mix. A silent mix or layer SHALL skip that layer with `silent_signal` or `silent_noise`. The backend's built-in noise and SNR facilities SHALL NOT be used. Bit-identical noise with MATLAB SHALL NOT be required.

#### Scenario: Pink noise at 10 dB SNR
- **WHEN** a mono pink layer with `snr_db: 10` is applied to an eight-channel mix
- **THEN** the measured ratio between the pooled mix RMS and the pooled added-noise RMS is 10 dB ± 0.01 dB before the second guard, and the layer metadata records `channel_mode: mono_replicated`

### Requirement: Seeded colored-noise generation
Each layer's seed SHALL be its explicit `seed`, otherwise `effective_seed + layer_index - 1`, otherwise `layer_index` (1-based). Colored noise SHALL use spectral shaping of seeded Gaussian white noise with exponent 0 (white), -1 (pink) or -2 (brown), then zero mean and unit RMS.

#### Scenario: Reproducible noise
- **WHEN** the same snapshot is rendered twice
- **THEN** the noise samples are identical in both outputs

### Requirement: Noise duration and channel fitting
Audio-file noise SHALL be looped or trimmed to the output length. Mono noise SHALL be replicated across channels, N-channel files SHALL be applied channel to channel, and any other channel count SHALL be rejected as invalid input.

#### Scenario: Incompatible noise channels
- **WHEN** a two-channel noise file is configured for an eight-channel array
- **THEN** the worker fails with an error naming the channel mismatch, and no WAV is accepted

### Requirement: Noise peak guard and metadata compatibility
After all noise layers are summed into the mix, a second whole-mix peak guard at 0.999 SHALL be applied and recorded. Noise metadata SHALL contain the same top-level and per-layer keys as the existing sidecar.

#### Scenario: Noise raises the output peak
- **WHEN** the post-noise mix peak exceeds 0.999
- **THEN** the entire mix is scaled to peak 0.999 and the second guard is recorded in the existing noise metadata format

### Requirement: WAV export
The backend SHALL write the output as a RIFF WAV file with N channels for N microphones, at the render sample rate, encoded as 16-bit PCM (the format produced by the existing MATLAB writer). It SHALL create missing parent directories, and SHALL write all samples before the metadata and completion record are published.

#### Scenario: Output format
- **WHEN** an eight-microphone variant completes
- **THEN** the WAV decodes fully as 8-channel, 44100 Hz, 16-bit PCM, with the sample count recorded in `summary.output_num_samples`

### Requirement: Sidecar contract compatible with existing validation
The sidecar SHALL contain every field required by existing array render validation, with unchanged semantics: identity and output fields, seed provenance, receiver descriptor and frame, and summary fields for sources (legacy poses), output dimensions, source count, noise, room reverberation and batch. `schema_version` SHALL be copied from the manifest.

#### Scenario: Existing validator acceptance
- **WHEN** a Pyroomacoustics array variant is validated with the existing metadata, source, array, seed and noise checks
- **THEN** those checks pass without any exception specific to Pyroomacoustics, other than the backend-declared T30 descriptor and sample-rate set

#### Scenario: Existing sidecar field inventory
- **WHEN** an array sidecar is published
- **THEN** it contains `schema_version`, `scene_id`, `job_id`, `scene_type`, `sample_rate_hz`, `output_type`, `project_name`, output paths, `effective_seed`, `seed_source`, `receiver.circular_array`, `receiver.coordinate_frame`, and summary source entries, `output_num_samples`, `output_num_channels`, `n_sources`, `background_noise`, `room.reverberation` and `batch`

### Requirement: Backend sidecar identity and provenance
`summary.render_backend` SHALL be `Pyroomacoustics`. A top-level `backend` object SHALL record backend name, package version, simulation parameters, coordinate transform, converted materials, reported RIR timing and per-pair T30 values.

#### Scenario: Backend block
- **WHEN** a Pyroomacoustics sidecar is read
- **THEN** `backend.name` is `pyroomacoustics`, `backend.version` equals the installed package version, and `backend.simulation.method` is `ism`

### Requirement: Backend-independent T30 measurement
The backend SHALL measure T30 from its own impulse responses at the ten octave centers 31.5 Hz to 16 kHz. It SHALL use octave-band filtering, Schroeder backward integration and a least-squares fit of the decay between -5 dB and -35 dB, extrapolated to 60 dB.

#### Scenario: Synthetic exponential decay
- **WHEN** the measurement is applied to a synthetic impulse response whose envelope decays with a known T60 of 0.6 s in every band
- **THEN** the measured T30 is 0.6 s ± 5% for bands from 125 Hz to 16 kHz, and ± 10% for 31.5 Hz and 63 Hz

#### Scenario: More absorption, shorter decay
- **WHEN** the same shoebox is rendered with uniform absorption 0.1 and then 0.4
- **THEN** every valid band's T30 is lower for absorption 0.4

### Requirement: T30 pair validity and aggregation
A (source, microphone) band whose decay does not reach -35 dB SHALL be invalid. A band's reported value SHALL be the arithmetic mean over all pairs, and SHALL be invalid if any pair is invalid. Invalid bands SHALL be reported as null.

#### Scenario: Truncated decay
- **WHEN** an impulse response's band decay reaches only -20 dB before it ends
- **THEN** that band is null, `status` is `unavailable`, and the existing canonical T30 gate rejects the scene and applies its existing resampling policy

### Requirement: T30 descriptor compatibility
The reverberation block SHALL use `metric: T30`, `estimated_from: rir`, `method: schroeder_backward_integration_v1`, `band_resolution: octave`, `frequency_mapping: center_frequency`, and unchanged `band_frequencies_hz`, `valid_band_count`, `status` and `mean_t30_s` semantics.

#### Scenario: Backend T30 descriptor
- **WHEN** a Pyroomacoustics reverberation block is validated
- **THEN** its descriptor matches the declared RIR-based method and retains the existing frequency, valid-band count, status and mean conventions

### Requirement: Physically consistent propagation and reported timing
Render metadata SHALL report the global delay that the backend's fractional-delay filtering introduces into every impulse response, in samples. Under ISM, the direct-path peak of a source at distance `d` from a microphone SHALL occur at `d / c * fs` plus the reported global delay, within ±1 sample, where `c` is the recorded speed of sound.

#### Scenario: Direct-path arrival
- **WHEN** an anechoic shoebox renders one source 2.0 m from one microphone at 44100 Hz
- **THEN** the impulse-response peak index equals `round(2.0 / c * 44100) + global_delay` ± 1

### Requirement: Relative propagation delays and levels
For two microphones, the arrival-time difference SHALL match the geometric difference within ±1 sample. In an anechoic room, the direct-path energy ratio between distances `d1` and `d2` SHALL be `(d2/d1)^2` within ±5%.

#### Scenario: Inter-microphone delay
- **WHEN** an anechoic scene renders one source at different distances from two array microphones
- **THEN** the cross-correlation lag between the two channels equals the geometric delay difference in samples ± 1

#### Scenario: Direct-path distance attenuation
- **WHEN** an anechoic room renders a source at 1.0 m and 2.0 m from a microphone
- **THEN** the direct-path energy ratio is 4 within ±5%

### Requirement: Deterministic rendering
Two renders of identical snapshots, with the same backend package version and the same platform, SHALL produce byte-identical WAV files and sidecars that are equal as JSON values. The backend SHALL seed its package-level random state from the effective seed before simulation, and SHALL record that it did.

#### Scenario: Repeat render
- **WHEN** the same snapshot is rendered twice in separate worker processes
- **THEN** the two WAV files have equal SHA-256 hashes, and the two sidecars are equal JSON documents

### Requirement: Supported dependency range and version provenance
The backend SHALL support Pyroomacoustics only within the version range declared by the orchestrator's optional `pyroomacoustics` dependency extra. Installing the orchestrator without that extra SHALL keep the RAVEN backend fully usable. The installed backend version SHALL be recorded in every Pyroomacoustics sidecar.

#### Scenario: RAVEN-only installation
- **WHEN** the orchestrator is installed without the `pyroomacoustics` extra and runs a RAVEN experiment
- **THEN** no Pyroomacoustics import is attempted and the experiment behaves as before this change

### Requirement: Legacy circular-array angular convention preserved
The backend SHALL preserve the existing circular-array microphone positions: microphone 0 points toward public +Z at receiver yaw +90°. The ring's angular direction is opposite to receiver view-vector yaw and `fixed_position` azimuth. This known risk SHALL be recorded and characterized by tests, not corrected here. A correction SHALL require a separate, explicitly authorized specification change.

#### Scenario: Characterized yaw behavior
- **WHEN** a four-microphone array with yaw +90° is rendered
- **THEN** microphone 0 is at public offset `[0, 0, +r]` from the center, while a `fixed_position` source at azimuth 0° with receiver yaw +90° lies toward public -Z
