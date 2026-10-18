# Spec Delta

## Purpose

Allow source databases organized by identity to be sampled without flattening their folders, and retain recording provenance through the supported static rendering pipeline. These added requirements describe planned behavior, not a claim that identity traceability is already implemented.

## ADDED Requirements

### Requirement: Bounded source WAV discovery

For each `source_types.audio_dir`, the orchestrator SHALL discover WAV files directly in that directory and directly in its immediate child directories, accepting the extension case-insensitively. It SHALL ignore non-WAV files and files below that depth, and SHALL order candidates deterministically by their relative paths.

#### Scenario: Database with twenty identities
- **WHEN** `audio_dir` contains twenty immediate ID folders, each containing WAV files directly
- **THEN** WAV files from all twenty folders are eligible source candidates without separate source-type entries

#### Scenario: Flat and grouped recordings coexist
- **WHEN** `audio_dir` contains `flat.wav`, `ID01/clip.WAV`, a JSON sidecar, and `ID01/session/deep.wav`
- **THEN** only `flat.wav` and `ID01/clip.WAV` are eligible
- **AND** repeated discovery of the unchanged directory returns the same candidate order

### Requirement: Validation matches supported source layout

The orchestrator SHALL accept an existing source audio directory when bounded discovery finds at least one WAV candidate. It SHALL reject missing directories and directories with no eligible WAVs with an error identifying the configured source directory.

#### Scenario: Root containing only ID folders
- **WHEN** `audio_dir` contains no root-level files but contains `ID01/clip.wav`
- **THEN** directory validation succeeds

#### Scenario: No eligible recording
- **WHEN** `audio_dir` contains only sidecars, empty folders, or WAVs below the supported depth
- **THEN** validation fails with an error identifying `audio_dir` and the absence of eligible WAVs

### Requirement: Explicit source provenance

Each newly generated source record in the scene manifest SHALL include string fields `identity_id` and `audio_relpath`. For a child-folder recording, `identity_id` SHALL equal that immediate folder's exact name. `audio_relpath` SHALL identify the original recording relative to `audio_dir`, using forward slashes. `source_id` SHALL remain the independent scene-local instance identifier.

#### Scenario: Identical basenames under distinct IDs
- **WHEN** selected recordings are `ID01/clip.wav` and `ID02/clip.wav`
- **THEN** their records contain identities `ID01` and `ID02` and distinct relative paths
- **AND** each record retains its own scene-local `source_id`

#### Scenario: Flat recording with an ID-looking filename
- **WHEN** the selected recording is directly under `audio_dir` and named `clip_ID13.wav`
- **THEN** `identity_id` is the empty string and `audio_relpath` is `clip_ID13.wav`
- **AND** no identity is inferred from the filename or the name of `audio_dir`

### Requirement: Identity is descriptive only

The orchestrator SHALL retain file-based source selection without identity quotas, identity balancing, or distinct-identity constraints. It SHALL avoid reusing a file within the scene's source-type pool until that pool is exhausted, then allow reuse. Deriving provenance SHALL consume no additional random draws; flat-directory sampling SHALL remain seed-compatible.

#### Scenario: Two recordings from one identity
- **WHEN** a source type has two eligible recordings under the same identity and a scene requests two sources of that type
- **THEN** both recordings can be selected in that scene with the same `identity_id` and different `source_id` values

#### Scenario: Pool exhaustion
- **WHEN** a scene requests more sources of a type than there are eligible files
- **THEN** selection can reuse files after exhaustion without treating the repeated identity as an error

#### Scenario: Existing flat experiment
- **WHEN** an existing flat-directory experiment is generated with the same effective seed and unchanged inputs
- **THEN** source file choices and other sampled scene properties match their pre-change behavior, apart from the additive provenance fields

### Requirement: Provenance survives runtime audio preparation

Runtime receiver manifests SHALL preserve each source's `identity_id` and `audio_relpath` from the scene manifest, including when trimming replaces `audio_path` with a prepared WAV path. Provenance SHALL continue describing the original database recording rather than the prepared file.

#### Scenario: Trimmed recording across receiver variants
- **WHEN** `ID02/long.wav` is prepared as a trimmed WAV and receiver-specific manifests are generated
- **THEN** every runtime source record retains `identity_id` equal to `ID02` and `audio_relpath` equal to `ID02/long.wav`
- **AND** only its runtime `audio_path` points to the prepared WAV

### Requirement: Rendered metadata exposes source provenance

When render metadata is enabled, MATLAB SHALL preserve the manifest's source provenance fields in each corresponding `summary.sources` entry for every supported static receiver output. Metadata SHALL keep the existing audio-path and scene-local source-ID fields, without changing acoustic behavior or output naming based on identity.

#### Scenario: Export source identity after preparation
- **WHEN** MATLAB renders a runtime manifest with `identity_id` equal to `ID02` and `audio_relpath` equal to `ID02/long.wav`
- **THEN** the exported `summary.sources` entry contains those exact strings alongside its `source_id` and runtime `audio_path`

#### Scenario: Mixed flat and identity sources
- **WHEN** a scene contains both flat-directory sources and ID-folder sources
- **THEN** each exported source entry contains its own provenance fields, with the flat source's identity represented by an empty string

### Requirement: Legacy source manifests remain accepted

MATLAB SHALL accept otherwise valid existing source manifests that omit either provenance field. Missing provenance SHALL be represented by empty strings in exported source summaries without attempting to infer it from runtime paths. Existing experiment YAML SHALL require no new keys to use flat or ID-folder audio directories.

#### Scenario: Legacy manifest without provenance
- **WHEN** MATLAB receives an existing source record containing `source_id` and `audio_path` but neither provenance field
- **THEN** source construction and metadata export succeed with empty `identity_id` and `audio_relpath` values

#### Scenario: Existing YAML targets a grouped database
- **WHEN** an otherwise valid experiment points its existing `audio_dir` key at the parent of the ID folders
- **THEN** the orchestrator supports the layout without additional identity configuration
