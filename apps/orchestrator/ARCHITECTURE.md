# ARCHITECTURE.md

## Overview

This project is a multi-stage dataset generation workspace for acoustic virtual reality and hearing-loss simulation.

The pipeline has three logical stages:
1. orchestration and scene generation in Python
2. static/dynamic acoustic rendering in MATLAB/RAVEN
3. hearing-loss degradation in Python with Clarity

The current implementation priority is the **static rendering pipeline**.

---

## Local reference examples

The repository should include a local `examples/` directory so the agent can inspect the expected contracts without leaving the workspace.

These files are the primary concrete references:
- `examples/example_config.yml`
- `examples/example_scene_static.json`

Interpret them as:
- `example_config.yml`: the current experiment-config contract consumed by the orchestrator
- `example_scene_static.json`: the current static-scene manifest contract consumed by MATLAB

The code should stay aligned with these examples unless there is an explicit, deliberate contract change.
If a contract changes, update the example files too.

---

## Workspace structure

Current workspace structure:

```text
workspace/
├── apps/
│   ├── orchestrator/
│   ├── matlab/
│   └── clarity-processor-py/
├── configs/
├── examples/
├── assets/
├── data/
└── logs/
```

### `apps/orchestrator`
Main application responsible for:
- reading experiment config
- validating config
- sampling concrete scenes
- writing MATLAB manifests
- invoking MATLAB renderer
- later invoking Clarity jobs
- tracking pipeline progress

### `apps/matlab`
MATLAB application responsible for:
- loading one scene manifest
- building room / receiver / sources in RAVEN
- rendering one or more receiver IR outputs
- exporting WAV(s) and render metadata

### `apps/clarity-processor-py`
Future Python application responsible for:
- reading degradation jobs
- applying hearing-loss processing
- exporting degraded WAV(s) and metadata

---

## Main contracts

### 1. Experiment config
The orchestrator consumes a YAML config describing:
- experiment metadata
- execution settings
- render settings
- receiver output settings
- room sampling space
- source sampling space
- optional background-noise planning (`background_noise`)
- output paths
- later hearing degradation settings

This config defines the sampling space, not one concrete scene.
The current expected shape should be inspectable in `examples/example_config.yml`.

### 2. MATLAB scene manifest
For each sampled scene, the orchestrator writes one concrete manifest JSON for MATLAB.
That manifest should contain fully instantiated scene data:
- base RPF file
- scene type
- room dimensions/materials
- receiver pose
- receiver IR targets
- concrete source list
- explicit `background_noise` metadata
- render options
- output paths or naming patterns

This manifest is the rendering contract.
The current expected shape should be inspectable in `examples/example_scene_static.json`.
Treat `examples/example_config.yml` and `examples/example_scene_static.json` as the only normative example pair for the static pipeline.

Receiver and source positions use canonical `[x, y, z]` coordinates, where `y` is height and `z` is horizontal depth. Runtime manifests consumed by RAVEN serialize those positions as `[x, y, -z]` because RAVEN uses the upper-left corner as origin.

For the current static pipeline, room materials are represented in two parallel manifest fields:
- `room.materials`: surface-keyed semantic material IDs
- `room.material_files`: surface-keyed objects with only `material_id` and absolute `material_path`

`room.material_files` is additive beside `room.materials`; entries do not include `surface_id`.

Every static manifest includes `background_noise`. Disabled configs serialize the explicit contract
`{"enabled": false, "layers": []}`. Enabled configs serialize scene-level layers chosen by the
orchestrator (`colored` white/pink/brown or concrete `audio_folder` paths), but synthesis and mixing
remain backend responsibilities outside the orchestrator.

### 3. Render outputs
MATLAB writes:
- one or more WAV outputs per scene
- one render metadata JSON per output variant in the current static pipeline

A single scene may generate multiple acoustic outputs when multiple receiver IR targets are requested.
The canonical teaching example is still single-output `binaural_hrtf`; multi-output remains a supported runtime capability, not the primary example.
Example categories:
- binaural HRTF
- BTE rear HARTF
- BTE front HARTF

### 4. Hearing-degradation outputs
When the optional Clarity stage is enabled, the public degraded-output contract is:
- root: `{artifact_root}/{run_name}/outputs/degraded/`
- nested directories: `{output_type}/{hearing_profile_id}/`
- degraded WAV filename: exactly `Path(input_wav_path).name`
- degraded metadata: sidecar in the same directory, named `{Path(input_wav_path).stem}.json`

The orchestrator keeps `clarity_jobs.jsonl` and `clarity_index.jsonl` as the internal handoff/index artifacts, but the backend must treat the explicit expected output paths in each manifest row as authoritative instead of assuming fixed filenames.

---

## Orchestrator internal structure

The orchestrator should remain compact.
Recommended structure:

```text
src/acoustic_orchestrator/
├── cli.py
├── config/
│   ├── models.py
│   ├── loader.py
│   └── validator.py
├── experiment/
│   ├── sampler.py
│   ├── scene_builder.py
│   └── manifest_writer.py
└── pipeline/
    ├── render_pipeline.py
    └── matlab_runner.py
```

This is enough for the current scope.
Do not introduce more layers unless there is a concrete need.

---

## Responsibilities by module

### `config/models.py`
Contains the Pydantic models that map closely to the experiment YAML.

Should include the minimum necessary structure for:
- experiment
- execution
- raven
- render
- receiver sampling
- receiver outputs
- room sampling
- source sampling
- scene validation
- outputs
- hearing degradation

Do not split these into many files too early.
One models file is acceptable at the beginning.
Match the examples and config vocabulary closely.

### `config/loader.py`
Responsible for:
- reading YAML
- constructing the root config model
- resolving relative paths

Should not contain business logic for scene sampling.
It should load the shape represented in `examples/example_config.yml`.

### `config/validator.py`
Responsible for semantic checks such as:
- ranges are valid
- required outputs are enabled
- source counts are consistent
- probability constraints are valid
- weighted policies sum correctly
- referenced paths exist when appropriate

### `experiment/sampler.py`
Responsible for turning sampling-space config into concrete scene parameters.
Examples:
- sample room dimensions
- sample room materials
- sample receiver pose
- choose source types and counts
- place sources according to spatial policy

### `experiment/scene_builder.py`
Responsible for building the in-memory concrete scene representation used before manifest export.
Keep this representation close to the MATLAB manifest structure.
Do not create many parallel scene representations.

### `experiment/manifest_writer.py`
Responsible for converting one concrete scene into one MATLAB manifest JSON.
This file writer is an explicit stage in the pipeline.
It should write the contract represented by `examples/example_scene_static.json`.

### `pipeline/matlab_runner.py`
Responsible for the current MATLAB-specific execution details.

Today it knows:
- where the sibling MATLAB app lives
- the static MATLAB entrypoint
- how to invoke MATLAB via `subprocess`
- how to write one runtime manifest per requested receiver IR target

It should not know scene sampling details.

In the current repo layout, that sibling app is `apps/matlab` resolved from `apps/orchestrator` as `../matlab`.

This module is runner-specific, which is acceptable for the current scope.
If another runner is added later, prefer another explicit runner module with the same kind of narrow responsibility instead of introducing a plugin framework.

### `pipeline/render_pipeline.py`
Responsible for the happy-path orchestration:
- load config
- validate config
- sample scenes
- write manifests
- expand scene manifests into concrete render jobs when needed
- call the selected runner implementation
- collect outputs

Keep this flow easy to read.

In the current implementation, `render_pipeline.py` prepares the static scene manifests first and then delegates execution to `matlab_runner.py`.
For MATLAB, one scene manifest may fan out into multiple runtime manifests, one per requested HRTF/HARTF target.
That fan-out is part of execution, not a separate framework layer.

---

## Scene generation model

The experiment config defines a **sampling space**.
The orchestrator generates **concrete scenes**.

A concrete static scene should contain:
- scene identifiers
- room geometry and materials
- receiver position/orientation
- receiver IR targets
- concrete list of sources with audio paths, positions, gains, offsets
- render settings
- output paths or path patterns

This scene is then serialized as the MATLAB manifest.
Use the example static manifest as the local contract reference.

---

## Receiver outputs model

Receiver outputs are modeled as multiple IR targets for the same receiver pose.
This is important.

Do not model HRTF and HARTF as completely separate pipeline concepts.
At the orchestrator level they are all receiver IR outputs.

One scene may request outputs such as:
- `binaural_hrtf`
- `bte_rear_hartf`
- `bte_front_hartf`

The MATLAB renderer may implement these as repeated rendering passes with different receiver IR files.
That internal detail should not leak heavily into the orchestrator.

---

## Output model

A single scene may produce:
- multiple WAV files
- multiple render metadata JSON files (one per output variant in the current static pipeline)

Therefore the orchestrator must not assume one scene equals one WAV.

`receiver_outputs.*.output_subdir` is the effective per-output subdirectory under both `outputs.rendered_wav_dir`
and `outputs.render_metadata_dir`.

The public filenames come from `outputs.naming.wav_pattern` and `outputs.naming.metadata_pattern`.
Only `{scene_id}` and `{output_type}` are supported placeholders.

The internal runtime identity remains stable as `{scene_id}__{output_type}` for:
- runtime manifest filenames
- resume checks
- output indexing

The base static scene manifest exposes resolved render paths for the first enabled output listed in the scene.
Each runtime manifest rewrites those same fields for its own output variant.

---

## Persistence and state

Short term:
- keep state simple
- file-based outputs and metadata are acceptable
- lightweight tracking is enough

Medium term:
- add SQLite for indexing and resume logic

Do not introduce DB-heavy architecture before the static pipeline works cleanly end-to-end.

---

## Extension points that are justified

These are justified future extension points:
- static vs dynamic rendering
- multiple receiver output types
- additional runner modules when there is a concrete second backend
- optional hearing degradation stage
- persistent pipeline state

These are **not** currently justified:
- renderer plugin framework
- generic sampler framework
- strategy/factory explosion for every policy
- many parallel model layers for the same scene data

---

## Development order

The intended implementation order is:
1. stable experiment config parsing and validation
2. static scene sampling
3. MATLAB manifest generation
4. MATLAB render invocation
5. output collection and metadata indexing
6. later Clarity integration
7. later dynamic scenes

Do not reorder this unless there is a strong reason.

---

## Architectural constraints

1. Keep the orchestrator simple.
2. Make data flow explicit.
3. Keep module count low.
4. Avoid speculative abstractions.
5. Prefer direct, testable code.
6. Scale by adding well-justified modules, not by adding patterns.
7. Keep the local example files updated when contracts change.

The project should feel like a clear pipeline, not a framework.
