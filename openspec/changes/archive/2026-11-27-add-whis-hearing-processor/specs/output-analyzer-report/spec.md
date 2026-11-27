# Spec Delta

## ADDED Requirements

### Requirement: Degraded records identified by processor
The analyzer SHALL identify each degraded record by variant, hearing profile and processor, taking the processor from the sidecar's `processor.name`. Records of different processors for the same variant and profile SHALL be kept as separate records, without one replacing the other or being reported as a duplicate.

#### Scenario: Run with two processors
- **WHEN** a run contains `msbg` and `whis` sidecars for the same variant and hearing profile
- **THEN** the exported degraded records contain both, each with its processor, and no duplicate issue is reported

### Requirement: Legacy degraded layout
The analyzer SHALL still read sidecars in the layout prior to per-processor directories (`degraded/<output_type>/<profile>/`). Sidecars without `processor.name` SHALL be treated as `msbg` and flagged as legacy in the exported records.

#### Scenario: Run produced before per-processor layout
- **WHEN** the analyzer reads a run whose sidecars lack `processor.name` and sit in the previous layout
- **THEN** its degraded records are loaded as `msbg` with a legacy flag, and the degraded charts are produced as before

### Requirement: Degraded charts and summaries per processor
When a run contains degraded records from more than one processor, degraded charts and summaries (hearing profiles, degradation status, ear asymmetry) SHALL be broken down by processor. Exports SHALL include each record's processor, effective scale K, presentation level and per-ear Leq when present.

#### Scenario: Status per processor
- **WHEN** all `msbg` jobs completed and one `whis` job failed
- **THEN** the degradation status summary shows the failure under `whis` and none under `msbg`

#### Scenario: Calibration fields exported
- **WHEN** a sidecar records K = 95 dB and per-ear Leq values
- **THEN** the degraded record export contains those values for that record
