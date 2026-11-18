# Spec Delta

## Purpose

Define how the orchestrator evaluates the scenes of a static batch during read-only preflight inspection, including parallel evaluation in worker processes, dispatch, dependency fingerprinting, deterministic in-order merge, and interruption. Implemented in the orchestrator; completion-driven dispatch and overlapped fingerprinting are introduced by this change.

## ADDED Requirements

### Requirement: Planning worker count
Preflight inspection SHALL evaluate scenes in parallel worker processes only when the effective worker count, the minimum of `execution.num_workers`, the CPU count and `execution.num_simulations`, is greater than one and the scene sampler can be transferred to worker processes. Otherwise it SHALL evaluate scenes sequentially in the calling process.

#### Scenario: Workers capped by CPUs and scene count
- **WHEN** `execution.num_workers` is 8, the machine reports 2 CPUs and the batch has 3 scenes
- **THEN** inspection uses 2 worker processes

#### Scenario: Single scene
- **WHEN** `execution.num_workers` is 8 and the batch has 1 scene
- **THEN** inspection evaluates the scene sequentially without worker processes

#### Scenario: Sampler not transferable
- **WHEN** `execution.num_workers` is 4 and the sampler cannot be transferred to worker processes
- **THEN** inspection evaluates every scene sequentially in scene order

### Requirement: Parallel results identical to sequential
Parallel inspection SHALL produce the same plan as sequential inspection of the same batch: the same planned manifests in scene order, sampling failures, conflicts, preconditions and plan metadata. Publishing either plan SHALL write byte-identical artifacts.

#### Scenario: Batch with a sampling failure
- **WHEN** a 4-scene batch whose second scene fails sampling is published once with `num_workers: 1` and once with `num_workers: 3`
- **THEN** both runs write byte-identical files under the run root, and the parallel plan lists manifests for scenes 0, 2 and 3 and one failure

#### Scenario: Resume after parallel publication
- **WHEN** a batch published with parallel inspection is inspected again with the same configuration
- **THEN** no manifest, plan or failure journal needs to be written

### Requirement: Scene results merged in scene order
Inspection SHALL merge scene results and report each scene as resolved strictly in scene order, regardless of the order in which worker processes finish them.

#### Scenario: Later scene finishes first
- **WHEN** scene 0 takes longer to evaluate than scenes 1 to 3
- **THEN** progress reports scenes resolved in the order 0, 1, 2, 3 and the plan lists manifests in scene order

### Requirement: Dispatch independent of the slowest scene
During parallel inspection, a worker process that finishes a scene SHALL receive another pending scene without waiting for earlier, still-running scenes to finish. The number of scenes submitted to worker processes but not yet finished SHALL stay bounded by a fixed multiple of the worker count.

#### Scenario: One slow scene
- **WHEN** a batch of 8 scenes runs with 2 workers and scene 0 stays running while the other scenes finish quickly
- **THEN** every remaining scene is evaluated before scene 0 finishes, and scenes 0 to 7 are still resolved in scene order

#### Scenario: Bounded submission
- **WHEN** parallel inspection runs with `w` workers
- **THEN** at no point are more than `2 * w` scenes submitted to worker processes without having finished

### Requirement: Dependency fingerprinting overlapped with sampling
During parallel inspection, the fingerprint of each selected dependency SHALL be computed once per inspection, starting when the first scene that references it finishes evaluation, concurrently with the evaluation of other scenes. A fingerprint error SHALL be reported as a conflict of every scene that references the dependency, in scene order, as in sequential inspection.

#### Scenario: Shared dependency
- **WHEN** several scenes of a batch reference the same HRTF file
- **THEN** that file is fingerprinted exactly once during the inspection

#### Scenario: Unreadable dependency
- **WHEN** a dependency referenced only by scene 2 cannot be read while it is fingerprinted
- **THEN** inspection raises a static preflight conflict naming scene 2's manifest path and writes nothing

### Requirement: Interruption leaves only the merged prefix resolved
When parallel inspection is interrupted, it SHALL report as resolved only the scenes merged before the interruption, cancel pending scenes and fingerprints, finish without reporting the planning stage as complete, and write no files.

#### Scenario: Interrupt on the third scene
- **WHEN** a 4-scene batch runs with 2 workers and evaluating scene 2 raises an interruption
- **THEN** only scenes 0 and 1 are reported as resolved, no conflict is counted, the planning stage is not reported as finished and the run root does not exist
