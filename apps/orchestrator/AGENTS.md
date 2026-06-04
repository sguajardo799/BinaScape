# AGENTS.md

## Purpose
This repository implements an orchestration layer for a room-acoustics dataset pipeline.

The orchestrator is responsible for:
- loading and validating experiment configs
- sampling scenes from the experiment space
- generating MATLAB manifests
- invoking the MATLAB renderer
- later invoking the Clarity degradation stage
- tracking outputs and pipeline state

It is **not** the place to put all rendering logic, all hearing-loss logic, or speculative abstractions.

Keep the system simple, explicit, and debuggable.

---

## Start here before changing code

Before inventing or guessing structure, inspect the local examples inside the workspace.
The agent should not need to leave the working folder to understand the main contracts.

Use these files as the source of truth for current structure expectations:
- `examples/example_config.yml`
- `examples/example_scene_static.json`

These examples define:
- the experiment config shape consumed by the orchestrator
- the concrete scene manifest shape consumed by MATLAB

If implementation and examples diverge, reconcile them explicitly instead of silently creating new structures.

---

## Core philosophy

Prefer:
- direct data flow
- explicit modules
- small numbers of models
- obvious naming
- boring code that is easy to debug

Avoid:
- premature abstraction
- generic factories
- deep inheritance trees
- plugin systems that are not yet needed
- model layers that duplicate the YAML structure without adding value
- “enterprise” patterns with no current use

The orchestrator must remain **simple but scalable**.
That means:
- simple for the current static pipeline
- scalable enough to later support dynamic scenes, Clarity, and persistent state
- no unnecessary architectural ceremony

---

## Non-goals

Do **not** introduce these unless there is a concrete need already present in the codebase:
- Factory classes for config loading, renderer selection, or sampler creation
- Abstract base classes for components that only have one implementation
- Separate DTO / schema / domain / entity layers for the same object
- Service registries
- Dependency injection frameworks
- Repository patterns unless persistence actually requires it
- A “plugin” architecture for render backends

If a single function or a simple class solves the current problem, use that.

---

## Repository mental model

The workspace contains multiple sibling apps:
- `orchestrator`: main pipeline coordinator
- `matlab`: MATLAB/RAVEN rendering backend
- `clarity-processor-py`: later hearing-loss processing backend

The orchestrator should communicate with other stages through:
- validated config objects
- generated manifest files
- subprocess calls
- output files and metadata

Do not tightly couple the orchestrator to MATLAB internals.
Do not move MATLAB logic into Python just to make the architecture look unified.

---

## Design rules

### 1. Keep config handling literal
The experiment YAML is already the main source of truth.
Reflect it closely in Python models.

Do not invent extra intermediate models unless they solve a real problem.

Good:
- one root config model
- nested models matching YAML sections

Bad:
- extra “factory input models”
- extra “parsed models” and “runtime models” when they hold the same data

### 2. Separate these concerns clearly
Keep these concerns in separate modules:
- config loading/validation
- scene sampling
- manifest writing
- MATLAB execution
- pipeline orchestration

But keep the boundaries light.
A few modules are enough.

### 3. Prefer functions over elaborate class hierarchies
Use simple classes where stateful behavior is genuinely useful.
Otherwise prefer functions and small helpers.

### 4. Use explicit file-based contracts
The pipeline depends on two explicit contracts:
- experiment config consumed by the orchestrator
- scene manifest consumed by MATLAB

Do not blur them together.
Do not pass giant ad hoc dictionaries between stages.

When in doubt, re-check:
- `examples/example_config.yml`
- `examples/example_scene_static.json`

### 5. Add extensibility only where it is already justified
Known future needs:
- dynamic scenes
- Clarity stage
- persistent pipeline state
- multiple receiver output types

Unknown future needs should not drive today’s design.

---

## What to preserve

The current intended architecture is:
- experiment YAML describes the search/sampling space and execution settings
- Python instantiates concrete scenes from that config
- Python writes one MATLAB manifest per scene
- MATLAB renders outputs for one or more IR targets
- Python later indexes outputs and may hand them to Clarity

Preserve these choices:
- MATLAB executables live outside the Python app in the sibling app directory `apps/matlab`
- multiple receiver IRs are represented as multiple outputs per scene
- HARTF may be passed through the same receiver-IR list mechanism as HRTF, with proper IDs
- static pipeline is the first priority
- local `examples/` files are the first reference for config and contract structure

---

## What not to do when extending the project

Do not:
- add a `Factory` suffix class because “it might be useful later”
- create separate config model trees for YAML, manifests, DB rows, and runtime if they are nearly identical
- hide critical filesystem behavior behind too many layers
- create a scheduler framework before a basic sequential pipeline works cleanly
- optimize parallelism before correctness and traceability are stable
- create new config or manifest variants without checking the examples folder first

---

## Preferred implementation style

### Config
Use `pydantic` models that map closely to YAML.

### Loader
One loader that:
- reads YAML
- validates into config models
- resolves relative paths

### Validator
One validator module for semantic consistency checks.
Not a giant framework.

### Sampling
Keep scene sampling logic explicit and localized.
Policies may be represented as config-driven conditionals, not as a tree of strategy classes unless that becomes necessary.

### MATLAB runner
Use `subprocess`.
Do not integrate MATLAB Engine unless there is a hard requirement.

### State
At first, keep pipeline state simple.
JSON/Parquet/basic tracking is acceptable.
Later SQLite can be added when global indexing and resume logic are needed.

---

## Practical coding guidance for future agents

When adding code:
1. ask whether the new type/module is actually needed
2. prefer editing an existing clear module over creating a new abstraction layer
3. keep naming aligned with the experiment config vocabulary
4. keep the happy path easy to follow from CLI to manifest to MATLAB to outputs
5. make debug paths obvious
6. verify structure against the local examples before changing contracts

When unsure, choose:
- simpler code
- fewer files
- fewer models
- fewer indirections

---

## Near-term priority

The near-term priority is a robust static pipeline:
- validate experiment config
- generate concrete static scenes
- write MATLAB manifests
- run MATLAB renderer
- collect outputs

Everything else is secondary until this path is stable.

---

## Hearing degradation output contract

When `hearing_degradation.enabled=true`, keep the public degraded-output contract explicit in docs, examples, and code:
- default root: `{artifact_root}/{run_name}/outputs/degraded/`
- per-profile directories: `{output_type}/{hearing_profile_id}/`
- degraded WAV filename: preserve the rendered WAV basename exactly
- degraded metadata: write a sidecar `{render_wav_stem}.json` in the same directory

The orchestrator may still keep internal `clarity_jobs.jsonl` and `clarity_index.jsonl` artifacts, but the backend contract is driven by the explicit expected output paths, not by fixed filenames.
