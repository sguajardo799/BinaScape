## Purpose

Guarantee that concurrent RAVEN render processes cannot read or overwrite each other's room materials or room model, so that each render simulates exactly the room in its own manifest.

## ADDED Requirements

### Requirement: Private material database per render run
Every RAVEN render run SHALL write all room materials to a material database directory used by no other render run, derived from its unique RAVEN project name. RAVEN SHALL read the materials for that run from that directory. No room material SHALL be written to the shared RAVEN material database.

#### Scenario: Two scenes with the same slot names
- **WHEN** two runs for different scenes both set slot `wall_001` with different absorption, concurrently
- **THEN** each run's `wall_001.mat` is in its own directory and contains its own absorption

#### Scenario: Shared database untouched
- **WHEN** a polygon or legacy shoebox room is rendered
- **THEN** no file under the shared `RavenDatabase/MaterialDatabase` is created or modified

### Requirement: Private room model per render run
A polygon room model generated for a render run SHALL be written inside that run's private workspace, and RAVEN SHALL load the model from there.

#### Scenario: Same face count in two scenes
- **WHEN** a shoebox and a trapezoid scene (both 6 faces) are rendered concurrently
- **THEN** each run's model file is in its own workspace and describes its own geometry

### Requirement: Isolation preserves render results
Isolation SHALL NOT change slot names, material values, geometry, receivers, sources or outputs. A run rendered alone SHALL produce the same T30 and audio as before this change.

#### Scenario: Sequential render unchanged
- **WHEN** `directivity_0001` of `outputs/directivity_experiment102` is rendered alone, before and after this change
- **THEN** the T30 values are equal within 1e-6 s

### Requirement: Concurrent renders are consistent
Receiver variants of the same scene SHALL report equal room T30 within the analyzer's tolerance (1e-6 s absolute, 1e-6 relative) when the experiment is rendered with several workers.

#### Scenario: Parallel experiment without conflicts
- **WHEN** `configs/experiments/speech_directivity_experiment.yml` is run with `num_workers: 4` and analyzed
- **THEN** the analyzer reports no `rt30_conflict` incident

### Requirement: Workspace cleanup and traceability
After a run finishes, successfully or not, its private workspace SHALL be removed best-effort; a cleanup failure SHALL emit a warning and SHALL NOT change the render outcome. Render metadata SHALL record the material database directory and model file path used.

#### Scenario: Cleanup after success
- **WHEN** a render run completes
- **THEN** its workspace directory no longer exists and its render metadata names the paths that were used

#### Scenario: Cleanup failure does not fail the render
- **WHEN** the workspace cannot be deleted
- **THEN** a warning is emitted and the render still reports success
