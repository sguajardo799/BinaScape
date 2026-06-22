# Acoustic Orchestrator

Python CLI for coordinating the static binaural acoustic pipeline. It loads an
experiment YAML file, validates and samples static scenes, writes scene
manifests, invokes the MATLAB/RAVEN backend, and prepares optional Clarity
hearing-loss degradation jobs.

The primary supported workflow is root-first execution from the monorepo root.

## Requirements

- Python `>=3.12,<3.13`
- `uv`
- MATLAB on `PATH` as `matlab` for `render-static`
- A local MATLAB/RAVEN backend at `apps/matlab`
- ITA Toolbox and RAVEN configured in MATLAB
- Local assets referenced by the selected config
- The sibling Clarity backend at `apps/clarity-backend` when submitting hearing
  degradation jobs

Install app dependencies from this directory:

```sh
uv sync --extra dev
```

Or from the repository root:

```sh
uv sync --project apps/orchestrator --extra dev
```

## Commands

Show CLI help:

```sh
uv run --project apps/orchestrator acoustic-orchestrator --help
```

Generate manifests only:

```sh
uv run --project apps/orchestrator acoustic-orchestrator generate-manifests configs/experiments/static_example.yml
```

Run the full static render:

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

Prepare Clarity handoff artifacts without submission:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml
```

Prepare and submit Clarity handoff artifacts to the sibling backend:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml --submit
```

From inside `apps/orchestrator`, the equivalent app-local example command is:

```sh
uv run acoustic-orchestrator generate-manifests .\examples\example_config.yml
```

The local `main.py` entrypoint can also be used for development:

```sh
uv run python .\main.py generate-manifests .\examples\example_config.yml
```

## Config Examples

Root-level examples:

- `configs/experiments/static_example.yml`: canonical root-first static
  pipeline example.
- `configs/experiments/sim_config.yml`: additional experiment configuration.
- `configs/hearing/hearing_profiles.yaml`: hearing profile catalog for Clarity
  degradation.

App-local examples:

- `apps/orchestrator/examples/example_config.yml`
- `apps/orchestrator/examples/example_config_w_loss.yml`
- `apps/orchestrator/examples/example_config_clapping_background_noise.yml`
- `apps/orchestrator/examples/example_scene_static.json`
- `apps/orchestrator/examples/hearing_profiles.yaml`

The app-local examples are useful for tests and development. The root-level
config is the preferred starting point for end-to-end runs from the repository
root.

## Generate Manifests

```sh
uv run --project apps/orchestrator acoustic-orchestrator generate-manifests configs/experiments/static_example.yml
```

This command:

1. Reads and validates the YAML config.
2. Resolves relative paths from the config file location.
3. Samples static scenes according to `execution.num_simulations`.
4. Writes one scene manifest per generated scene under
   `{outputs.artifact_root}/{run_name}/manifests/scene/`.

Current material contract in generated manifests:

- `room.materials` is preserved as a surface-to-`material_id` map.
- `room.material_files` is added as a surface-to-file map.
- Each material file path is absolute.
- Material entries do not emit `surface_id`.

Coordinate note for RAVEN: receiver and source positions are serialized as
`[x, y, -z]` because the MATLAB/RAVEN side expects that coordinate convention.

## Background Noise Planning

The orchestrator plans background noise metadata in the scene manifest. MATLAB
performs the downstream synthesis and mixing.

The optional `background_noise` config supports:

- `enabled`
- `snr_db`
- `allow_multiple_layers`
- `colored` strategies with `white`, `pink`, or `brown`
- `audio_folder` strategies with `noise_type`, `audio_dir`, and `file_pattern`

Every emitted manifest includes `background_noise`. When disabled, it is exactly:

```json
{ "enabled": false, "layers": [] }
```

When enabled, the orchestrator chooses concrete layers per scene in a
reproducible and balanced way, including absolute paths for selected audio
files. It does not perform DSP or audio mixing.

## Static Render

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

This command:

1. Generates static scene manifests.
2. Creates MATLAB input manifests, one per enabled receiver-output variant.
3. Invokes `matlab -batch` through the fixed backend entrypoint
   `apps/matlab/run_raven_static_render.m`.
4. Writes rendered WAV files and render metadata to configured paths.
5. Updates output indexes for planned, completed, resumed, failed, partial,
   blocked, skipped, or inconsistent variants.
6. If `hearing_degradation.enabled=true`, prepares Clarity handoff artifacts.
7. Submits Clarity jobs only when
   `hearing_degradation.runner.auto_submit=true`.

Parallel MATLAB/RAVEN execution is controlled by `execution.num_workers`.
`num_workers: 1` keeps execution sequential. Higher values may launch multiple
MATLAB subprocesses after runtime inputs are prepared. Do not configure more
workers than available MATLAB/RAVEN licenses, and return to `1` if the local
RAVEN setup shows hidden global state.

The canonical single-output example enables `binaural_hrtf`. The runtime also
supports additional receiver IR outputs when enabled under `receiver_outputs`.

## Clarity Handoff

Prepare artifacts only:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml
```

Prepare and submit:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml --submit
```

Expected artifacts:

- `manifests/runtime/clarity/clarity_jobs.jsonl`: one job per eligible rendered
  variant, including `run_id` and `backend_invocation`.
- `indexes/clarity_index.jsonl`: status tracking for planned, submitted,
  completed, partial, failed, blocked, or skipped jobs.
- `outputs/degraded/{output_type}/{hearing_profile_id}/{render_wav_name}.wav`:
  degraded output from the backend.
- `outputs/degraded/{output_type}/{hearing_profile_id}/{render_wav_stem}.json`:
  metadata sidecar next to the degraded WAV.

Current degradation output contract:

- The default root is `{artifact_root}/{run_name}/outputs/degraded/`.
- Subdirectories are `{output_type}/{hearing_profile_id}/`.
- `expected_output_wav_path` and `expected_output_metadata_path` in each JSONL
  job are authoritative for the backend.
- Resume checks validate the explicit paths reserved in the handoff manifest.
- Legacy layouts under `outputs/clarity/...` are not enough to satisfy new runs
  by themselves.

The orchestrator does not add the Clarity backend as a direct Python dependency.
The current design keeps a file-and-subprocess boundary between the two apps.

## Project Structure

```text
apps/orchestrator/
|-- main.py
|-- pyproject.toml
|-- ARCHITECTURE.md
|-- examples/
|-- src/
|   `-- acoustic_orchestrator/
|       |-- cli.py
|       |-- config/
|       |-- experiment/
|       `-- pipeline/
`-- tests/
```

Key modules:

- `config/loader.py`: reads config files and resolves paths.
- `config/models.py`: Pydantic models for the experiment config.
- `config/validator.py`: config validation rules.
- `experiment/sampler.py`: scene sampling.
- `experiment/scene_builder.py`: scene manifest construction.
- `experiment/manifest_writer.py`: manifest output.
- `pipeline/render_pipeline.py`: high-level workflow orchestration.
- `pipeline/matlab_runner.py`: MATLAB subprocess integration.
- `pipeline/clarity_handoff.py`: Clarity JSONL job preparation.
- `pipeline/clarity_runner.py`: Clarity subprocess integration.
- `pipeline/output_index.py`: output status tracking.
- `pipeline/output_paths.py`: output path reservation.
- `pipeline/runtime_audio.py`: runtime audio preparation.

## Development

Run tests:

```sh
uv run --project apps/orchestrator pytest
```

Run static checks when the tools are installed:

```sh
uv run --project apps/orchestrator ruff check .
uv run --project apps/orchestrator mypy src
```

The example configs use `execution.overwrite_existing: true`, so repeated runs
can replace generated manifests and outputs for the same run.

## Practical Notes

- `render-static` assumes the monorepo layout where `apps/orchestrator`,
  `apps/matlab`, and `apps/clarity-backend` are sibling directories.
- If `apps/matlab` is missing, `render-static` fails before invoking MATLAB.
- The orchestrator writes `backend_invocation` metadata into Clarity jobs, but
  actual execution still happens via `uv run --project <sibling-backend>` or the
  configured entrypoint.
- The supported pipeline is static. Dynamic rendering remains outside the
  supported orchestrator workflow.
