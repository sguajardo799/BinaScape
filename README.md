# Binaural Hearing Loss Pipeline

Monorepo for a static binaural acoustic simulation pipeline. The supported
root-first workflow is coordinated by `apps/orchestrator`, which generates scene
manifests, calls the MATLAB/RAVEN renderer, and can prepare or submit hearing
loss degradation jobs to the sibling Clarity backend.

The project is still evolving. The documented supported path is the static
rendering workflow; dynamic rendering entrypoints exist in the MATLAB app but
are not implemented as a supported pipeline yet.

## Repository Layout

```text
.
|-- configs/
|   |-- experiments/          # Root-level experiment YAML examples
|   `-- hearing/              # Hearing profile catalogs for Clarity jobs
|-- apps/
|   |-- orchestrator/         # Python CLI that validates configs and runs the pipeline
|   |-- matlab/               # MATLAB/RAVEN static rendering backend
|   `-- clarity-backend/      # Python backend for Clarity/MSBG degradation
|-- pyproject.toml            # uv workspace declaration
`-- uv.lock                   # Root workspace lockfile
```

## What Each App Does

- `apps/orchestrator`: Python CLI package named `acoustic-orchestrator`. It
  loads experiment YAML, samples static scenes, writes JSON manifests, invokes
  MATLAB by subprocess, and manages Clarity handoff manifests and indexes.
- `apps/matlab`: MATLAB backend for static RAVEN rendering. It consumes scene
  JSON manifests and writes rendered WAV files plus metadata.
- `apps/clarity-backend`: Python CLI package named `clarity-backend`. It
  consumes JSONL jobs produced by the orchestrator and applies hearing loss
  degradation with Clarity MSBG.

## Requirements

- Python `>=3.12,<3.13`
- `uv`
- MATLAB for full rendering
- ITA Toolbox and RAVEN configured in MATLAB for full rendering
- `matlab` available on `PATH` when using `render-static`
- Local audio, HRTF, directivity, material, and RAVEN `.rpf` assets referenced
  by the selected experiment config

The Python projects are declared as a conservative `uv` workspace:

```text
apps/orchestrator
apps/clarity-backend
```

## Quick Start From The Repository Root

Install Python dependencies for the two Python apps:

```sh
uv sync --project apps/orchestrator --extra dev
uv sync --project apps/clarity-backend
```

Check that both CLIs resolve:

```sh
uv run --project apps/orchestrator acoustic-orchestrator --help
uv run --project apps/clarity-backend clarity-backend --help
```

Generate static scene manifests without invoking MATLAB:

```sh
uv run --project apps/orchestrator acoustic-orchestrator generate-manifests configs/experiments/static_example.yml
```

Run the full static render when MATLAB, ITA Toolbox, RAVEN, and the referenced
assets are available:

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

Prepare Clarity degradation jobs without submitting them:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml
```

Submit the prepared Clarity handoff to the sibling backend:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml --submit
```

## Main Configuration Files

- `configs/experiments/static_example.yml`: root-first example for the supported
  static pipeline.
- `configs/experiments/sim_config.yml`: additional root-level experiment config.
- `configs/hearing/hearing_profiles.yaml`: hearing profile catalog used by the
  Clarity backend when degradation is enabled.
- `apps/orchestrator/examples/`: app-local configs and scene examples used by
  the orchestrator tests and developer workflows.
- `apps/matlab/example/`: MATLAB scene JSON examples for direct backend work.

Experiment configs control scene sampling, receiver outputs, source assets,
room material choices, background noise planning, output naming, and optional
hearing degradation.

## Output Flow

For the supported static workflow, the pipeline is:

1. The orchestrator reads an experiment YAML file.
2. It samples static scenes and writes scene manifests under the configured
   artifact root and run name.
3. `render-static` prepares MATLAB input manifests and invokes
   `apps/matlab/run_raven_static_render.m`.
4. The MATLAB backend renders WAV files and render metadata.
5. If hearing degradation is enabled, the orchestrator writes
   `manifests/runtime/clarity/clarity_jobs.jsonl` and an index file.
6. When submitted, `apps/clarity-backend` writes degraded WAV files and sidecar
   metadata to the explicit paths reserved by the orchestrator.

The orchestrator treats generated output paths as part of the contract. The
Clarity backend consumes explicit paths from the JSONL manifest rather than
guessing filenames.

## Development

Run orchestrator tests:

```sh
uv run --project apps/orchestrator pytest
```

Run Clarity backend tests:

```sh
uv run --project apps/clarity-backend pytest
```

Run MATLAB tests from `apps/matlab` in a MATLAB environment with the required
toolboxes available:

```matlab
runtests('tests')
```

Useful developer references:

- `apps/orchestrator/README.md`
- `apps/orchestrator/ARCHITECTURE.md`
- `apps/matlab/README.md`
- `apps/clarity-backend/README.md`

## Current Limitations

- The supported end-to-end path is static rendering. Dynamic rendering is
  documented as not implemented.
- Full rendering requires local MATLAB/RAVEN/ITA setup and assets that are not
  guaranteed to exist on every checkout.
- The Clarity backend currently exposes a small CLI focused on JSONL manifest
  execution.
- Reproducibility depends on the configured seeds and downstream tool behavior;
  the MATLAB metadata records the effective seed and known limits.
