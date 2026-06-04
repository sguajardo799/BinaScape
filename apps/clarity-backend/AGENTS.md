# AGENTS.md

## Purpose
This repository implements the Clarity-stage backend for the workspace dataset pipeline.

The Clarity backend is responsible for:
- reading orchestrator-generated degradation jobs
- validating required inputs for each job
- applying hearing-loss processing
- writing degraded WAV output(s)
- writing backend metadata for traceability

It is **not** the place to re-implement orchestration, scene sampling, or MATLAB rendering logic.

Keep the system simple, explicit, and debuggable.

---

## Start here before changing code

Before inventing new structure, inspect the upstream contracts that already exist in the workspace.

Use these as the current source of truth:
- `apps/orchestrator/ARCHITECTURE.md`
- `apps/orchestrator/README.md`
- `apps/orchestrator/tests/test_clarity_handoff.py`
- `apps/orchestrator/tests/test_output_index.py`

These files define the current Mode A handoff shape more reliably than guesses do.

Important current reality:
- this backend is still minimal
- the orchestrator generates the Clarity job manifest
- the backend consumes explicit job records rather than importing orchestrator internals

If the backend and upstream contract diverge, reconcile them explicitly instead of silently creating a second contract.

---

## Core philosophy

Prefer:
- direct data flow
- explicit file-based boundaries
- small numbers of modules
- straightforward CLI entrypoints
- obvious validation and error messages
- boring code that is easy to inspect and debug

Avoid:
- speculative abstractions
- plugin systems for DSP backends
- factory classes for job execution
- deep model layering for the same manifest data
- dependency injection frameworks
- repository patterns before persistence actually needs them
- framework-style architecture

The backend should stay simple for the current handoff slice, while remaining extensible when real needs appear.

---

## Non-goals

Do **not** introduce these unless the codebase clearly needs them:
- alternate job contract formats invented only for this repo
- importer-style coupling to orchestrator Python modules
- abstract processor hierarchies when there is only one concrete path
- generic task runners or workflow engines
- database-first state architecture
- packaging or deployment ceremony beyond what the current `uv` workflow needs

If a small function or one clear module solves the problem, use that.

---

## Repository mental model

The workspace has three logical stages:
- `apps/orchestrator`: prepares jobs and tracks pipeline state
- `apps/matlab`: renders acoustic outputs
- `apps/clarity-backend`: consumes Clarity jobs and produces degraded outputs

The Clarity backend should communicate through:
- explicit job manifests written by the orchestrator
- rendered WAV and render metadata produced upstream
- degraded WAV outputs and backend metadata written here

Do not tightly couple this repo to orchestrator internals.
Do not move orchestration or MATLAB responsibilities into this backend just to make the architecture look unified.

---

## Design rules

### 1. Keep job manifests literal
The orchestrator-generated Clarity manifest is the handoff contract.

Reflect that contract closely in code.
Do not invent extra parallel schemas unless they solve a real problem.

### 2. Keep processing flow explicit
The happy path should be easy to follow:
- read manifest
- validate job inputs
- process one job
- write degraded output(s)
- write metadata
- report failures clearly

### 3. Keep boundaries file-based
This backend consumes files and writes files.

Prefer explicit paths such as:
- input WAV path
- upstream render metadata path
- hearing profile path
- output directory
- expected degraded WAV path
- expected result metadata path

Do not replace this with hidden in-memory coupling across repos.

### 4. Keep the CLI straightforward
The primary entrypoint should remain a small CLI that can be invoked from the sibling orchestrator, typically through `uv run --project ../clarity-backend ...`.

Do not turn the CLI into a framework.

### 5. Keep metadata practical
Result metadata should help answer:
- which job ran
- which inputs were used
- which hearing profile was applied
- where outputs were written
- whether the job completed, partially completed, or failed

Write metadata that is useful for traceability first.

### 6. Keep state simple
Short term:
- file outputs
- metadata JSON
- lightweight status derived from observed outputs

Medium term:
- richer indexing or persistent state only when resume/query needs justify it

Do not introduce DB-heavy architecture before the end-to-end handoff is stable.

---

## What to preserve in the current Mode A architecture

Preserve these current choices:
- the orchestrator owns job creation and submission decisions
- this backend consumes orchestrator-generated Clarity manifests
- the backend is invoked as a sibling project, normally via `uv`
- the first slice preserves an explicit subprocess + file boundary
- outputs remain easy to inspect on disk
- the orchestrator-provided expected output WAV path and metadata path are the authoritative output artifacts
- traceability matters more than abstraction elegance

The current priority is a robust, inspectable first slice, not a generalized DSP platform.

---

## What not to do when extending the backend

Do not:
- create a plugin framework for future degradation engines
- add `Factory` classes because they might be useful later
- split one manifest into many near-duplicate model layers
- hide critical filesystem behavior behind too many indirections
- import orchestrator code directly instead of consuming its written contract
- assume future multi-profile or multi-output complexity should reshape today’s simple path
- optimize concurrency before correctness, traceability, and resumability are clear

---

## Preferred implementation style

### Models
Use simple typed structures that map closely to the current job manifest and result metadata.

### Loader / validation
One clear path for:
- reading the manifest
- validating required fields
- checking that referenced files exist where appropriate

### Processing
Keep per-job execution explicit and localized.
Prefer a small processing function/module over a hierarchy of processors.

### CLI
Keep one obvious CLI path for the common operation of running a manifest.

### State
Prefer simple file-based status and metadata first.

---

## Near-term priority

The near-term priority is a robust first Clarity slice:
- read orchestrator-generated jobs
- validate inputs
- run degradation
- write degraded outputs and metadata
- keep traceability and resume behavior simple

Everything else is secondary until this path is stable.

---

## Practical coding guidance for future agents

When adding code:
1. check the upstream orchestrator contract first
2. prefer editing an existing clear module over adding a new abstraction layer
3. keep naming aligned with manifest vocabulary like `job`, `variant`, `hearing_profile`, and `output`
4. keep the happy path easy to follow from CLI to manifest to degraded outputs
5. make failures obvious and file paths visible in error messages
6. preserve explicit sibling-repo boundaries
7. mention `uv` where invocation behavior matters, but do not turn repository guidance into packaging documentation

When unsure, choose:
- simpler code
- fewer files
- fewer models
- fewer indirections

The project should feel like a clear processing stage, not a framework.
