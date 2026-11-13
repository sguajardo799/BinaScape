# Spec Delta

## ADDED Requirements

### Requirement: Private MATLAB preferences directory per render process
Every MATLAB render process launched by the orchestrator SHALL start with `MATLAB_PREFDIR` set to a new, empty directory that no other process uses, located outside the batch outputs. The value SHALL override any `MATLAB_PREFDIR` inherited from the caller. Render processes SHALL NOT read or write the user's MATLAB preferences directory.

#### Scenario: Concurrent renders use distinct preference directories
- **WHEN** two render attempts run at the same time
- **THEN** each MATLAB process receives a different `MATLAB_PREFDIR`, and each directory exists and is empty when its process starts

#### Scenario: User preferences untouched during a parallel batch
- **WHEN** a batch is rendered with `num_workers` greater than one
- **THEN** the user's `matlabprefs.mat` keeps its bytes and modification time

#### Scenario: Inherited preference directory is overridden
- **WHEN** the orchestrator runs with `MATLAB_PREFDIR` already set in its environment
- **THEN** the render process receives its private directory, not the inherited value

### Requirement: Preference isolation preserves render results
Preference isolation SHALL NOT change render inputs, outputs or acceptance. A variant rendered with isolated preferences SHALL produce the same T30 as before this change.

#### Scenario: Sequential render unchanged
- **WHEN** one variant of `outputs/directivity_experiment102` is rendered alone, before and after this change
- **THEN** the T30 values are equal within 1e-6 s

### Requirement: Preference directory cleanup and traceability
After a MATLAB render process ends, successfully or not, its preferences directory SHALL be removed best-effort. A cleanup failure SHALL emit a warning and SHALL NOT change the process status, exit code, render acceptance or retry decision. The attempt record SHALL name the directory used and whether it was removed.

#### Scenario: Cleanup after success and after failure
- **WHEN** a render process exits with code zero or with a non-zero code
- **THEN** its preferences directory no longer exists and the attempt record names it as removed

#### Scenario: Cleanup failure does not fail the render
- **WHEN** the preferences directory cannot be deleted
- **THEN** a warning is emitted, the attempt record marks the cleanup as failed, and the render outcome is the same as with a successful cleanup

#### Scenario: Launch failure
- **WHEN** MATLAB cannot be launched
- **THEN** the preferences directory created for that attempt is removed and the attempt keeps its existing launch-failure classification
