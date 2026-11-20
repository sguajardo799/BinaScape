# Spec Delta

## ADDED Requirements

### Requirement: Source audio catalogs listed once per inspection
During a static preflight inspection, each process that samples scenes SHALL list a given source `audio_dir` at most once and SHALL reuse that listing for every scene, scene attempt and receiver attempt it samples in that inspection. The reused listing SHALL be identical to a fresh listing taken at the start of the inspection.

#### Scenario: Many receiver attempts
- **WHEN** a sequential inspection samples 10 scenes that together need more than 10 receiver attempts with one speech `audio_dir`
- **THEN** that `audio_dir` is listed once during the inspection

#### Scenario: Parallel workers
- **WHEN** a parallel inspection runs with 3 worker processes
- **THEN** each process lists a given `audio_dir` at most once, and the plan equals the sequential plan

### Requirement: Source audio listing refreshed between inspections
A source audio listing reused within one inspection SHALL NOT be reused by a later inspection or by sampling performed outside an inspection. A WAV added to or removed from `audio_dir` between two inspections SHALL be reflected in the second inspection's catalog.

#### Scenario: WAV added between inspections
- **WHEN** an inspection completes, a new eligible WAV is added to `audio_dir`, and a second inspection runs with overwrite enabled
- **THEN** the second inspection's source catalog includes the new WAV

#### Scenario: Catalog change detected before publication
- **WHEN** a WAV is added to `audio_dir` after inspection and before publication
- **THEN** publication fails with a static preflight conflict reporting that the sampling catalog changed

### Requirement: Faster listing preserves candidate order
The source audio listing SHALL return the same candidates in the same order as defined by source discovery (ordered by relative path, with the platform's path comparison), so the same configuration and seed produce the same scenes as before this change.

#### Scenario: Mixed-case names and identity folders
- **WHEN** `audio_dir` contains `z.wav`, `A.WAV`, `ID02/clip.wav`, `ID01/clip.WaV` and `ID01/session/deep.wav`
- **THEN** the listing equals the paths sorted by the platform's path ordering, excluding `ID01/session/deep.wav`

#### Scenario: Same scenes for the same seed
- **WHEN** the same fixture batch is published before and after this change with the same seed
- **THEN** the scene manifests and the sampling failure journal are byte-identical, and the static plan differs only in the generation contract fingerprint of the changed listing module

#### Scenario: Batch published before this change
- **WHEN** a batch whose static plan was published before this change is inspected with `resume_if_possible: true`
- **THEN** inspection fails with a static preflight conflict stating that the generation contract changed and requires `resume_if_possible: false` and `overwrite_existing: true`
