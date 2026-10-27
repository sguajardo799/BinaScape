# rt30-scene-distributions Specification

## Purpose

Define the planned Output Analyzer behavior for visualizing mean RT30 per unique scene from existing metadata, distinguishing Sabine sampling targets from RAVEN results without changing existing per-frequency analysis contracts.

## Requirements

### Requirement: Complete selected-band scene means

Output Analyzer SHALL derive one arithmetic mean per scene and method over the scene's explicit selected frequency set. Each mean SHALL require a finite positive value at every selected frequency. RAVEN values SHALL use existing consolidated scene/frequency values without additional variant weighting or equivalence decisions. Invalid or missing selection metadata SHALL make the mean unavailable.

#### Scenario: Extra bands and HRTF variants do not inflate the population
- **WHEN** a scene selects seven bands and has ten reported bands and several equivalent render variants
- **THEN** each method contributes at most one mean over the seven selected frequencies
- **AND** extra bands and render variant counts do not weight that mean

#### Scenario: Incomplete RAVEN does not suppress independent Sabine data
- **WHEN** a selected RAVEN band is missing or excluded but every selected Sabine band is valid
- **THEN** the Sabine mean remains available and the RAVEN complete mean is unavailable with the affected frequencies and reason
- **AND** no partial-band mean substitutes for the unavailable complete mean

#### Scenario: Missing or malformed selected frequencies
- **WHEN** the selected frequency list is missing, empty, duplicated or contains an invalid frequency
- **THEN** both complete means are unavailable with a selection reason rather than assuming seven bands or all reported bands

### Requirement: Comparable scene distribution panels

The report SHALL prominently display separate Sabine and RAVEN scene-mean histogram panels with shared interval boundaries and count scale within each identical selected-band group. It SHALL identify methods, seconds, selected frequencies, valid and excluded scene counts, and explain that these are metadata values rather than WAV measurements. It SHALL NOT silently pool different selected frequency sets.

#### Scenario: Independent method populations and heterogeneous selections
- **WHEN** scenes have different selected frequency sets or one method has fewer complete means
- **THEN** distinct frequency sets are displayed in separately labeled groups
- **AND** each panel reports its own valid and excluded scene counts within its group

#### Scenario: No usable scene means
- **WHEN** no complete mean exists for a method or for the run
- **THEN** the corresponding panel reports unavailable data and exclusion counts without fabricating zero observations

### Requirement: Sampling intervals and target context

For an unambiguous sampling batch covering the displayed population, the report SHALL use its valid bin edges and display authoritative per-bin Sabine target counts. It SHALL distinguish planned targets from observed scene counts and state that the targets do not constrain RAVEN. When batch scope, edges or targets are unavailable or ambiguous, it SHALL state the limitation and omit unsupported references.

#### Scenario: Directivity experiment sampling target
- **WHEN** the population is covered by one authoritative batch with edges 0.2 through 1.0 s in 0.1 s increments and target counts of 125
- **THEN** both panels use those eight in-range intervals and the report identifies 125 scenes per interval as the planned Sabine target
- **AND** observed counts are computed from the displayed scene means rather than copied from batch summaries

#### Scenario: Missing or conflicting batch context
- **WHEN** no valid batch edges exist or multiple batches cannot be assigned unambiguously to the displayed scenes
- **THEN** both panels use the same deterministic data-derived intervals and explain that configured intervals and target counts are unavailable
- **AND** the report does not infer a uniform target from a filename, the current experiment YAML or observed counts

#### Scenario: Valid edges without target counts
- **WHEN** batch scope and edges are valid but target counts are missing or invalid
- **THEN** the panels retain those interval boundaries but mark the target reference unavailable

### Requirement: Out-of-range values and boundary conservation

Every valid scene mean SHALL be represented in its method's histogram. With configured boundaries, values below and above the range SHALL be shown as explicit underflow and overflow counts. Intervals SHALL be left-closed and right-open except the final interval, which includes its upper bound. The report SHALL NOT label out-of-range RAVEN values as sampling failures solely because of that range.

#### Scenario: Values outside the Sabine sampling range
- **WHEN** RAVEN complete means include values below 0.2 s and above 1.0 s
- **THEN** both tails are visibly counted and remain available in exports
- **AND** in-range counts plus both tail counts equal the valid RAVEN scene count

#### Scenario: Exact interval boundaries
- **WHEN** means equal the configured minimum, an internal boundary or the maximum
- **THEN** they are counted exactly once in the first, following or final interval respectively

### Requirement: Paired scene-mean method comparison

The report SHALL include a Sabine-versus-RAVEN scatter plot with Sabine on X, RAVEN on Y, seconds on both axes, a visible equality line and equal numeric axis ranges. A point SHALL represent one scene with both complete means over the same selected set. The plot SHALL identify its band group and paired valid and excluded counts, including an unavailable state.

#### Scenario: Partial scenes and extreme RAVEN means
- **WHEN** some scenes lack a complete method mean and another scene has a RAVEN mean outside the sampling range
- **THEN** incomplete scenes are excluded with reasons and the complete out-of-range scene remains visible within the plot axes
- **AND** adding equivalent HRTF variants does not add scatter points

### Requirement: Auditable additive outputs and compatibility

The analyzer SHALL export scene-mean records in JSON, JSONL and CSV and include reconstructible chart observations, boundaries, counts, selection groups, reference provenance and exclusion reasons in its metrics. New SVGs SHALL be embedded in the self-contained HTML and exported separately with visible legends. Existing frequency exports, chart keys, consolidation rules and matched-pair summary semantics SHALL remain unchanged.

#### Scenario: Reconstructing the report from metadata outputs
- **WHEN** a consumer reads scene-mean and metrics exports without HTML or audio assets
- **THEN** the consumer can reproduce panel counts and scatter coordinates using scene identities, method means, selected bands and bin definitions
- **AND** original metadata paths and exclusion causes remain traceable

#### Scenario: Existing consumers and metadata-only operation
- **WHEN** analysis runs against a metadata-only copy of an existing schema 3.0 run
- **THEN** existing per-frequency chart filenames and exports remain available with explicit scene/frequency labels
- **AND** existing matched-band means retain their previous meaning
- **AND** the new analysis requires no WAV reads, renderer invocation or current configuration YAML
