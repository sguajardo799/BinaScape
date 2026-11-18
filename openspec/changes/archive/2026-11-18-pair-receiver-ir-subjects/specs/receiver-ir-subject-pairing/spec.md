# Spec Delta

## Purpose

Define how the orchestrator selects HRTF/HARTF receiver IR files per scene, including an opt-in mode in which every receiver output of a scene uses the same measured subject. Implemented in the orchestrator; `independent` remains the default selection mode.

## ADDED Requirements

### Requirement: Subject pairing setting
The orchestrator SHALL accept an optional experiment setting `receiver_outputs.subject_pairing` with the values `independent` and `shared`. When the setting is omitted, the orchestrator SHALL behave as `independent`. Any other value SHALL be rejected during configuration loading with an error naming the field.

#### Scenario: Setting omitted
- **WHEN** an experiment config has no `receiver_outputs.subject_pairing`
- **THEN** configuration loading succeeds and receiver IRs are selected in `independent` mode

#### Scenario: Unknown value
- **WHEN** an experiment config sets `receiver_outputs.subject_pairing: paired`
- **THEN** configuration loading fails with an error identifying `receiver_outputs.subject_pairing`

### Requirement: Subject ID derived from catalog filename
The subject ID of a receiver IR catalog file SHALL be the part of its filename before the first underscore. A file whose name has no underscore, or an empty part before it, SHALL have no subject ID.

#### Scenario: Matching subjects across catalogs
- **WHEN** the catalogs contain `id22_HRTF.daff`, `id22_HARTF_front.daff`, and `id22_HARTF_rear.daff`
- **THEN** all three files have subject ID `id22`

### Requirement: Shared mode validation
In `shared` mode, configuration validation SHALL fail, naming the offending output, when an enabled HRTF/HARTF output sets `num_hrtfs` other than 1, a matching catalog file has no subject ID, or two matching files of one catalog share a subject ID. It SHALL also fail when the enabled catalogs have no subject ID in common.

#### Scenario: Multiple IRs per output
- **WHEN** `subject_pairing: shared` and `bte_front_hartf` sets `num_hrtfs: 2`
- **THEN** validation fails with an error identifying `receiver_outputs.bte_front_hartf.num_hrtfs`

#### Scenario: Duplicate subject in one catalog
- **WHEN** `subject_pairing: shared` and the `binaural_hrtf` catalog matches both `id05_HRTF.daff` and `id05_HRTF_v2.daff`
- **THEN** validation fails with an error identifying `receiver_outputs.binaural_hrtf` and subject `id05`

#### Scenario: Disjoint catalogs
- **WHEN** `subject_pairing: shared` and the enabled catalogs have no subject ID in common
- **THEN** validation fails with an error stating that the receiver IR catalogs share no subject

#### Scenario: Independent mode keeps existing validation
- **WHEN** `subject_pairing` is `independent` and a catalog contains files without an underscore
- **THEN** validation does not report subject ID errors

### Requirement: One subject per scene in shared mode
In `shared` mode, the orchestrator SHALL choose one subject ID per scene, uniformly from the subject IDs present in every enabled HRTF/HARTF catalog, and SHALL select for each enabled HRTF/HARTF output the catalog file with that subject ID. Subjects missing from any enabled catalog SHALL never be chosen. The choice SHALL be deterministic for a given seed and config.

#### Scenario: All outputs share the subject
- **WHEN** `subject_pairing: shared` with `binaural_hrtf`, `bte_front_hartf`, and `bte_rear_hartf` enabled and a run of 1000 scenes is planned
- **THEN** in every scene the three selected IR files have the same subject ID

#### Scenario: Subject missing from one catalog
- **WHEN** `subject_pairing: shared` and `id30` exists in the HRTF and front catalogs but not in the rear catalog
- **THEN** no scene is assigned subject `id30`

#### Scenario: Reproducible selection
- **WHEN** the same config and seed are planned twice in `shared` mode
- **THEN** both plans assign the same subject ID to every scene

### Requirement: Subject provenance in the scene manifest
In `shared` mode, each `receiver.hrtfs[]` entry of a generated scene manifest SHALL include a string field `subject_id` equal to the scene's chosen subject ID, alongside the existing `hrtf_id` and `hrtf_path`. In `independent` mode, entries SHALL keep their current fields only.

#### Scenario: Shared manifest entries
- **WHEN** a scene is generated in `shared` mode with subject `id22`
- **THEN** every `receiver.hrtfs[]` entry of its manifest has `subject_id: "id22"` and its `hrtf_path` points to that subject's file

#### Scenario: Independent manifest entries
- **WHEN** a scene is generated in `independent` mode
- **THEN** its `receiver.hrtfs[]` entries contain only `hrtf_id` and `hrtf_path`

### Requirement: Independent mode is unchanged
In `independent` mode, the orchestrator SHALL select receiver IR files exactly as before this change, so an existing config and seed SHALL produce the same scenes and selected IR files.

#### Scenario: Reproducing an earlier run
- **WHEN** an existing experiment config without `subject_pairing` is planned with its original seed
- **THEN** every scene selects the same receiver IR files as before this change

### Requirement: Circular array unaffected
The circular array output SHALL NOT take part in subject pairing; enabling it alongside `shared` mode SHALL neither require a subject ID nor change how its descriptor is derived from the sampled receiver pose.

#### Scenario: Shared mode with circular array
- **WHEN** `subject_pairing: shared` and `circular_array` is enabled together with HRTF/HARTF outputs
- **THEN** validation and pairing consider only the HRTF/HARTF outputs and the circular array descriptor matches the one derived from the scene's receiver position and orientation
