## Purpose

Select the acoustic render backend for static experiments, validate backend-specific configuration before any scene is planned or rendered, and keep backend identity explicit in manifests, metadata and resume decisions so RAVEN and Pyroomacoustics outputs are never confused.

## ADDED Requirements

### Requirement: Explicit render backend selector with RAVEN default
The experiment configuration SHALL accept `render.backend` with the values `raven` and `pyroomacoustics`. When `render.backend` is absent, the effective backend SHALL be `raven`. Any other value SHALL be rejected by configuration validation with an error naming `render.backend` and the supported values.

#### Scenario: Existing configuration without a backend
- **WHEN** an experiment configuration that was valid before this change, and has no `render.backend`, is loaded
- **THEN** the effective backend is `raven` and configuration validation, planning and rendering behave as before this change

#### Scenario: Explicit Pyroomacoustics selection
- **WHEN** a configuration sets `render.backend: pyroomacoustics` together with a valid `pyroomacoustics` block and only supported features
- **THEN** configuration validation succeeds and every render variant of the run is dispatched to the Pyroomacoustics backend

#### Scenario: Unknown backend
- **WHEN** a configuration sets `render.backend: odeon`
- **THEN** validation fails before planning with an error naming `render.backend` and listing `raven` and `pyroomacoustics`

### Requirement: Backend-specific configuration blocks
The `raven` block and existence of its `base_rpf_file` SHALL be required only for the `raven` backend. For `pyroomacoustics`, a top-level `pyroomacoustics` block SHALL be required and a `raven` block SHALL be rejected. For `raven`, a `pyroomacoustics` block SHALL be rejected.

#### Scenario: Linux configuration without RAVEN
- **WHEN** `render.backend` is `pyroomacoustics` and the configuration has no `raven` block
- **THEN** validation does not require or check any RAVEN project file

#### Scenario: Conflicting backend blocks
- **WHEN** `render.backend` is `pyroomacoustics` and a `raven.base_rpf_file` is also configured
- **THEN** validation fails, naming `raven` as not applicable to the selected backend

### Requirement: RAVEN-only tuning rejection
Under `pyroomacoustics`, explicitly configured RAVEN-only `render` fields SHALL be rejected: `generate_rir`, `generate_brir`, `simulation_type_rt`, `simulation_type_is`, `num_particles` and `is_order_ps`.

#### Scenario: RAVEN tuning under Pyroomacoustics
- **WHEN** `render.backend` is `pyroomacoustics` and `render.num_particles: 60000` is explicitly configured
- **THEN** validation fails, naming `render.num_particles` as a RAVEN-only field, instead of ignoring it

### Requirement: Required ISM parameters and non-shoebox order
The `pyroomacoustics` block SHALL require an integer `max_order` ≥ 0 and boolean `air_absorption`. It SHALL accept an optional integer `max_order_non_shoebox` ≥ 0, defaulting to `max_order`.

#### Scenario: Missing ISM order
- **WHEN** `render.backend` is `pyroomacoustics` and `pyroomacoustics.max_order` is absent, negative or not an integer
- **THEN** validation fails, naming `pyroomacoustics.max_order`

### Requirement: Paired atmospheric conditions
Optional `pyroomacoustics.temperature_c` and `humidity_pct` SHALL be finite and provided together, with humidity in [0, 100].

#### Scenario: Partial atmospheric conditions
- **WHEN** `pyroomacoustics.temperature_c` is set without `pyroomacoustics.humidity_pct`
- **THEN** validation fails instead of combining a configured value with a hidden default

### Requirement: Explicit rejection of unsupported backend features
Configuration validation SHALL reject every unsupported requested capability before planning, naming its configuration field and selected backend. The backend SHALL NOT substitute omnidirectional sources, omnidirectional receivers or other simplified models for unsupported requests.

#### Scenario: No simplified replacement
- **WHEN** a configuration requests an unsupported capability
- **THEN** validation fails before planning instead of rendering a simplified replacement, with an error naming the field and backend

### Requirement: Unsupported receiver outputs
For `pyroomacoustics`, enabled `binaural_hrtf`, `bte_rear_hartf` or `bte_front_hartf` receiver outputs SHALL be rejected.

#### Scenario: Binaural output requested with Pyroomacoustics
- **WHEN** `render.backend` is `pyroomacoustics` and `receiver_outputs.binaural_hrtf.enabled` is true
- **THEN** validation fails, naming `receiver_outputs.binaural_hrtf` as unsupported by `pyroomacoustics`

### Requirement: Unsupported source directivity
For `pyroomacoustics`, any source type with `directivity` SHALL be rejected.

#### Scenario: Directional source requested with Pyroomacoustics
- **WHEN** a source type configures a `.daff` `directivity` and the backend is `pyroomacoustics`
- **THEN** validation fails, naming that source type's `directivity` field, and no scene is rendered with an omnidirectional replacement

### Requirement: Unsupported hearing degradation and dynamic scenes
For `pyroomacoustics`, `hearing_degradation.enabled: true` and `scene_type: dynamic` SHALL be rejected before planning. Existing Clarity eligibility rules SHALL remain unchanged.

#### Scenario: Hearing degradation requested with Pyroomacoustics
- **WHEN** `hearing_degradation.enabled` is true and the backend is `pyroomacoustics`
- **THEN** validation fails before planning, and the existing Clarity eligibility rules remain unchanged

#### Scenario: Dynamic scene requested with Pyroomacoustics
- **WHEN** the backend is `pyroomacoustics` and `scene_type` is `dynamic`
- **THEN** validation fails before planning, naming the scene-type field and backend

### Requirement: Unsupported Pyroomacoustics sample rates
For `pyroomacoustics`, `render.sample_rate_hz` other than 44100 SHALL be rejected before planning, naming the field and supported rates.

#### Scenario: Unsupported sample rate
- **WHEN** the backend is `pyroomacoustics` and `render.sample_rate_hz` is 48000
- **THEN** validation fails, naming `render.sample_rate_hz` and the rates supported by `pyroomacoustics`

### Requirement: Backend dependency availability
For `pyroomacoustics`, a missing package or a version outside the supported range SHALL be rejected before planning or worker launch, naming the required dependency and version range.

#### Scenario: Backend package unavailable
- **WHEN** the backend is `pyroomacoustics` and the package cannot be imported, or its version is outside the supported range
- **THEN** validation fails with an error that names the required dependency and version range, before any worker process is launched

### Requirement: Supported sample rates declared per backend
Each backend SHALL declare its set of supported render sample rates. Configuration validation for non-RAVEN backends and render validation for every backend SHALL check the runtime `render.sample_rate_hz` against the set declared for the variant's backend. In this change, both `raven` and `pyroomacoustics` SHALL declare exactly {44100}. Configuration-time behavior for `raven` SHALL remain unchanged.

#### Scenario: RAVEN render at 44100 Hz
- **WHEN** a RAVEN variant is validated with `sample_rate_hz` 44100
- **THEN** the sample-rate checks produce the same outcome as before this change

#### Scenario: Runtime manifest with an undeclared rate
- **WHEN** a runtime manifest for either backend declares a sample rate outside that backend's declared set
- **THEN** render validation records `runtime_sample_rate_invalid` and the render is not accepted

### Requirement: Stable identity of existing RAVEN configurations and manifests
For the effective `raven` backend, the normalized configuration used for static plan identity SHALL be identical to the one produced before this change.

#### Scenario: Normalized configuration of a RAVEN experiment
- **WHEN** the normalized static configuration is computed for `configs/experiments/circular_array_only.yml` with and without an explicit `render.backend: raven`
- **THEN** both results are equal to the normalized configuration produced before this change for the same file

### Requirement: Unchanged RAVEN manifests and metadata requirements
For `raven`, scene and runtime manifests SHALL be byte-identical in content to those produced before this change, without `render.backend` or Pyroomacoustics parameters. RAVEN metadata SHALL NOT require any new field.

#### Scenario: RAVEN runtime manifest content
- **WHEN** a RAVEN runtime manifest is generated for a scene
- **THEN** it contains no `render.backend` key and no Pyroomacoustics parameters

### Requirement: Explicit conflicts after generator-contract changes
Changes to the static generator contract caused by modifying hashed source files SHALL retain existing explicit plan-conflict behavior, without silent reuse or overwrite.

#### Scenario: Run created before the upgrade
- **WHEN** a RAVEN run directory planned before this change is resumed after a generator-contract change
- **THEN** the existing plan-conflict handling reports the conflict without rewriting or deleting existing artifacts

### Requirement: Backend identity in manifests and render metadata
For a non-RAVEN backend, scene and runtime manifests SHALL record `render.backend` and the effective backend parameters needed by the worker, so the runtime snapshot alone determines the simulation. Render metadata of non-RAVEN variants SHALL identify the backend name, the backend package version and the effective simulation parameters. Render validation SHALL reject a non-RAVEN variant whose metadata backend identity does not match its runtime manifest.

#### Scenario: Pyroomacoustics runtime manifest
- **WHEN** a runtime manifest is generated for a Pyroomacoustics array variant
- **THEN** it contains `render.backend: pyroomacoustics` and the effective ISM order, air-absorption flag and atmospheric parameters

#### Scenario: Metadata from a different backend
- **WHEN** a runtime manifest selects `pyroomacoustics` but the sidecar identifies another backend or omits backend identity
- **THEN** render validation rejects the variant, citing a backend identity mismatch

### Requirement: No cross-backend reuse on resume
Resume compatibility SHALL treat the effective backend and its effective parameters as part of the render inputs. An output produced by one backend, or with different backend parameters, SHALL NOT be reused as a completed variant of a run that expects another backend or other parameters. Existing protection against overwriting non-reusable outputs SHALL continue to apply.

#### Scenario: Switching an existing run to Pyroomacoustics
- **WHEN** a run directory containing RAVEN outputs is resumed with a configuration whose backend is `pyroomacoustics`
- **THEN** no RAVEN variant is reused, and the existing plan or overwrite protection reports the incompatibility instead of rendering over protected files

#### Scenario: Changed ISM order
- **WHEN** a Pyroomacoustics run is resumed with a different `pyroomacoustics.max_order`
- **THEN** previously rendered variants are not considered equivalent render inputs
