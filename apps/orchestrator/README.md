# Acoustic Orchestrator

Python CLI for coordinating the static binaural acoustic pipeline. It loads an
experiment YAML file, validates and samples static scenes, writes scene
manifests, invokes the MATLAB/RAVEN backend, and prepares optional Clarity
hearing-loss degradation jobs.

The primary supported workflow is root-first execution from the monorepo root.

See the [documentation index](../../docs/README.md), [local architecture](ARCHITECTURE.md),
[shared contracts](../../docs/architecture/contracts.md), [setup](../../docs/development/setup.md),
and [testing guide](../../docs/development/testing.md). The sibling
[output analyzer](../output-analyzer/README.md) can inspect run metadata after
execution; it is an optional analysis application.

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

Run the official opt-in MATLAB/RAVEN smoke directly through the orchestrator:

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

This command samples all configured room-shape candidates and requires the
referenced local assets, MATLAB, ITA Toolbox, and RAVEN.

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

For development inspection of sampled room footprints, install the optional
`dev` extra (which includes matplotlib) and call
`acoustic_orchestrator.experiment.geometry_visualization.plot_room_geometry`.
The helper is intentionally not imported by the production pipeline.

## Config Examples

Root-level examples:

- `configs/experiments/static_example.yml`: root-first static
  pipeline example.
- `configs/experiments/speech_fixed_position_e2e.yml`: fixed-position experiment example.
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
   `{outputs.artifact_root}/{run_name}/metadata/manifests/scene/`.

Run artifacts are grouped under `metadata/manifests/`, `metadata/indexes/`,
and `output_audio/`. The optional `cropped_audio/` directory is created only when
source audio must be trimmed. Output directories are created when a stage writes
files, so a manifest-only run does not create empty render or Clarity directories.

Generated scene manifests use schema **3.0**. MATLAB accepts schemas 1.0, 2.0,
and 3.0; the examples illustrate contracts implemented by production validators.
The loader rejects legacy independent output-root keys and
`room_sampling.max_rt30_s`; use `outputs.artifact_root`, `outputs.run_name`,
and `room_sampling.reverberation`.

Current material contract in generated manifests:

- `room.materials` is preserved as a surface-to-`material_id` map.
- `room.material_files` is added as a surface-to-file map.
- Each material file path is absolute.
- Material entries do not emit `surface_id`.
- Schema 3.0 adds `room.acoustic_surfaces` with base, treatment and authoritative
  effective absorption/scattering vectors for every surface.

The required `room_sampling.reverberation` block defines a global uniform
distribution over equal-width bins. The sampler preassigns balanced quotas by
scene index, estimates all ten RAVEN octave bands with Sabine and classifies on
the seven-band mean from 125 Hz through 8 kHz. Walls and ceiling receive
independent synthetic treatments. A uniformly retained in-range candidate is
used as a soft fallback when the requested bin is not reached. Manifests contain
`reverberation_sampling`; their embedded `reverberation_batch` reports quotas,
observations, deviations, fallbacks, failures and statistical resolution.

The bin divisibility check uses an absolute tolerance of `1e-9 s` (plus a
relative tolerance of `1e-12`) on the reconstructed time span. Bins follow
`[lower, upper)` semantics, except that the last bin includes its upper bound.
For a single-bin experiment, set its width to the complete range:

```yaml
room_sampling:
  reverberation:
    metric: estimated_rt30_s
    estimator: sabine
    estimator_version: sabine_polygon_octaves_v4
    aggregation: arithmetic_mean
    mean_bands_hz: [125, 250, 500, 1000, 2000, 4000, 8000]
    distribution:
      type: uniform
      scope: global
      range_s: {min: 0.4, max: 0.6}
      bin_width_s: 0.2
      quota_tolerance_fraction: 0.10
    treatment:
      catalog_version: 1
      mix_model: area_weighted_linear
      mix_model_version: 1
      eligible_surface_types: [wall, ceiling]
      max_treatments_per_surface: 1
      preserve_base_scattering: true
      allow_none: true
      wall_coverage: {min: 0.0, max: 1.0}
      ceiling_coverage: {min: 0.0, max: 1.0}
```

The v1 synthetic treatment catalog requires 31 finite absorption coefficients
in `[0, 1]` and limits the absolute step between adjacent frequency bands to
`0.08`. This is an acoustic-plausibility rule, not a claim of construction or
commercial-product realism. `quota_tolerance_fraction` is diagnostic: bins
above it are reported and a successful batch becomes
`complete_with_deviation`, but the tolerance never changes classification or
invalidates the batch. Small quotas also report
`insufficient_statistical_resolution` when their discrete resolution is coarser
than the requested tolerance.

Coordinate note for RAVEN: public manifests use `[x, y, z]` with the footprint
in `[x, z]`. For schemas 2.0 and 3.0, the MATLAB adapter applies the same explicit
`[x, y, z] -> [x, y, -z]` reflection to positions and orientation vectors.
Legacy schema 1.0 keeps its existing shoebox contract. Scene and runtime JSON
preserve public coordinates: the reflection takes place inside MATLAB. Render
metadata source positions and orientation vectors therefore use the adapted
RAVEN coordinates for schemas 2.0/3.0.

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

The canonical single-output example enables `binaural_hrtf`. MATLAB requires
`render.sample_rate_hz: 44100`. Before completing the remaining variants,
the pipeline checks that the canonical RAVEN T30 has exactly ten finite,
positive bands and can retry within the scene budget. This measured T30 is
distinct from the seven-band Sabine estimate used by sampling. The runtime also
supports additional receiver IR outputs when enabled under `receiver_outputs`.

## Circular Array Configuration

Configuration loading/validation accepts an optional `receiver_outputs.circular_array`.
The static pipeline supports array-only and mixed receiver scene generation,
runtime selection, rendering, indexing, and resume provenance checks.

```yaml
receiver_outputs:
  circular_array:
    enabled: true
    num_microphones: 8
    radius_m: 0.05
    output_subdir: circular_array
    required: true
hearing_degradation:
  enabled: false
  input_targets: []
  hearing_profiles_path: path/to/hearing_profiles.yml
```

This is a section-level excerpt: retain the other required experiment sections.
Array-only config does not require a `binaural_hrtf` block or receiver IR catalog.
`num_microphones` is a strict integer >= 2; `radius_m` is a finite positive
distance in meters. Channels are derived from the microphone count, so do not
configure `num_channels`, `num_hrtfs`, `file_pattern`, or `ir_catalog_path` for an
array. The defaults are `enabled: false`, `required: false`, and
`output_subdir: circular_array`; a disabled array can omit its geometry.
Output subdirectories must be relative, nonempty, and contain no traversal
segments or Windows drive/root/UNC prefixes. At least one output must be enabled.

The array geometry is a horizontal ring in the public XZ plane, centered on
the sampled receiver and rotated by yaw only. HRTF/HARTF outputs remain
independently configurable alongside the array.

Array WAVs and sidecars use the existing artifact layout:

```text
{artifact_root}/{run_name}/output_audio/render/{output_subdir}/
  {scene_id}__circular_array.wav
  {scene_id}__circular_array__render.json
```

These filenames follow the default naming templates; custom templates still
use `{scene_id}` and `{output_type}`. One array has one variant identity
`{scene_id}__circular_array`, regardless of microphone count. Its index entry
uses `receiver_ir_path: null`, not a fake HRTF path. Metadata records the
public center/yaw, radius, ordered microphone positions and WAV channel mapping.
Microphone 0 corresponds to WAV channel 1; yaw zero starts at +X, with ordering
toward +Z. Array source WAVs are downmixed by channel mean before normalization
and gain, while HRTF/HARTF source handling is unchanged.

Resume checks compare planned array geometry with the sidecar/runtime provenance
and verify actual WAV channel count/sample rate. Wrong radius/yaw/order or channel
count makes the output invalid for resume. If sidecars are disabled, saved runtime
geometry is required; newly written manifests must not validate old stale WAVs.
Canonical receiver rendering still needs a T30 sidecar for room acceptance.
For rerunning an existing experiment, use `resume_if_possible: true` together
with `overwrite_existing: true` to allow scene-manifest regeneration: this is
the pipeline's existing behavior, not permission to blindly reuse audio.

Full experiment examples: `configs/experiments/circular_array_only.yml` and
`configs/experiments/circular_array_mixed.yml`. Receiver-section examples are
in `apps/orchestrator/examples/circular_array_scenes.md`.

Opt-in acceptance checks require installed MATLAB/RAVEN, compatible subject-01
DAFF files in the HRTF/front-HARTF/rear-HARTF catalogs, and the Clarity backend.
Run from the repository root using a fresh writable artifact directory:

```sh
uv run --project apps/orchestrator python apps/orchestrator/tests/verify_circular_array_e2e.py render --output-root PATH
uv run --project apps/orchestrator python apps/orchestrator/tests/verify_circular_array_e2e.py clarity --output-root PATH
```

The render phase verifies array-only and four-output mixed runs, actual IR paths
stored in RAVEN projects, channel/coordinate provenance, T30, and resume. The
Clarity phase verifies HRTF-only degraded artifacts and rejection of HARTF/array
targets. Reports are `render_verification.json` and `clarity_verification.json`
under `PATH`. These real-backend checks are separate from mocked unit tests.

## Clarity Handoff

Only `binaural_hrtf` is accepted in `hearing_degradation.input_targets`.
This base target covers numbered sampled HRTFs such as `binaural_hrtf__1`;
do not list numbered variants in the experiment config.
HARTF rear/front and `circular_array` targets are rejected explicitly, even
when degradation is disabled and even for two microphones. To migrate a
previous HARTF-degradation config, remove HARTF from `input_targets`; keep
its receiver output enabled to continue rendering it. Array-only or
HARTF-only runs must disable degradation and use `input_targets: []`.
Enabled degradation requires an enabled binaural HRTF output and a nonempty
eligible target list. Handoff creation and submission independently enforce
the same HRTF-only policy, including stale or externally supplied job records.

Prepare artifacts only:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml
```

Prepare and submit:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml --submit
```

Expected artifacts:

- `metadata/manifests/runtime/clarity/clarity_jobs.jsonl`: one job per eligible rendered
  variant, including `run_id` and `backend_invocation`.
- `metadata/indexes/clarity_index.jsonl`: status tracking for planned, submitted,
  completed, partial, failed, blocked, or skipped jobs.
- `output_audio/degraded/{output_type}/{hearing_profile_id}/{render_wav_name}.wav`:
  degraded output from the backend.
- `output_audio/degraded/{output_type}/{hearing_profile_id}/{render_wav_stem}.json`:
  metadata sidecar next to the degraded WAV.

Current degradation output contract:

- The default root is `{artifact_root}/{run_name}/output_audio/degraded/`; an explicit `hearing_degradation.output_dir` overrides it.
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

## Source audio directories

`source_sampling.source_types[].audio_dir` accepts WAVs directly in the configured
directory and directly in its immediate child folders. Each child folder name is
treated as an opaque database identity, not necessarily a speaker:

```text
speech/                  <-- audio_dir
+-- flat.wav
+-- ID01/
|   +-- recording.wav
+-- ID02/
    +-- recording.wav
```

Both layouts can coexist; no new YAML keys are needed. Extensions are
case-insensitive (`.wav`, `.WAV`). Sidecars, empty folders, and deeper recordings
such as `ID01/session/clip.wav` are ignored. At least one eligible WAV is required.
Point `audio_dir` at the parent of the ID folders, not an individual ID folder.
Selection remains file-based: IDs do not impose uniqueness or balancing, and two
recordings of the same identity can occur in one scene.

New scene manifests include two provenance strings per source:

| Original recording relative to `audio_dir` | `identity_id` | `audio_relpath` |
| --- | --- | --- |
| `ID02/recording.wav` | `"ID02"` | `"ID02/recording.wav"` |
| `clip_ID13.wav` | `""` | `"clip_ID13.wav"` |

The scene-local `source_id` (for example `src_0001`) identifies an instance, not
the database identity. Flat recordings have an empty identity; IDs are not parsed
from filenames or the configured root's own name. IDs are scoped to that source
database, not globally unique person identifiers. `audio_relpath` uses forward
slashes and always describes the original recording. Runtime trimming can change
`audio_path` but leaves both provenance strings unchanged.

When render metadata is enabled, inspect `summary.sources` in each rendered JSON
sidecar for these fields. Existing MATLAB manifests missing either field remain
accepted and export an empty string for the missing value. Provenance is not
duplicated into the compact output-index snapshot or Clarity degraded sidecars;
the latter retain their link to the input render metadata. Existing schema
versions, output names, and acoustic processing are unchanged.

## Fixed source positions

`fixed_position` places a required source type at receiver-relative points
defined by the Cartesian product of three non-empty lists:

```yaml
source_sampling:
  min_sources: 1
  max_sources: 1
  source_types:
    - event_type: speech
      role: base
      min_count: 1
      max_count: 1
      probability: 1.0
      audio_dir: ../../../assets/events/speech/
      spatial_policy:
        type: fixed_position
        azimuths_deg: [-90, 0, 90]
        elevations_deg: [0, 15]
        distances_m: [1.0]
```

The example defines six cases. `execution.num_simulations` must be at least
the largest case count of any configured `fixed_position` policy. For each
policy, every block of six scenes uses a seed-reproducible shuffle and covers
each case exactly once. A final partial block uses the prefix of a fresh
shuffle. The scheduled case is preferred. If scheduled cases from simultaneous
`fixed_position` policies conflict, the sampler tries deterministic local
alternatives; this exceptional substitution can reduce that block's coverage.
WAV selection remains independent and may reuse the same file.

Angles follow MATLAB/RAVEN: at receiver yaw/pitch zero, azimuth/elevation zero
points along `+X`; positive azimuth rotates toward RAVEN `+Z`; positive
elevation rotates toward `+Y`. Azimuth is added to receiver yaw, elevation is
added to receiver pitch, and receiver roll is ignored. Python preserves the
public coordinates in the manifest; the MATLAB schema 2.0/3.0 adapter reflects Z
consistently for room geometry, poses, and orientation vectors.

List values must be finite and unique. Azimuth is limited to `[-180, 180]`,
elevation to `[-90, 90]`, and distance must be greater than zero. A fixed
source type must have `min_count >= 1`. Multiple fixed sources in one scene use
distinct points; the sampler searches alternate assignments when necessary to
meet source-to-source separation. It aborts if there are too few unique points
or no valid assignment.

Assigned fixed points always enforce room bounds, the 0.5 m wall clearance,
the configured receiver clearance, and source separation, including when
`require_sources_inside_room` is false. An assigned point that violates a room,
wall, or receiver constraint aborts the experiment instead of being resampled.
Monophonic scenes are supported with `source_sampling.min_sources: 1`.

## Development

Run tests:

```sh
uv run --project apps/orchestrator pytest apps/orchestrator/tests
```

Run static checks when the tools are installed:

```sh
uv run --project apps/orchestrator ruff check apps/orchestrator
uv run --project apps/orchestrator mypy apps/orchestrator/src
```

The canonical `configs/experiments/static_example.yml` smoke config uses
`execution.overwrite_existing: false`. Give a repeated smoke run a fresh
`outputs.run_name`, then execute the `render-static` command above directly.
Other examples may choose overwrite behavior independently.

## Practical Notes

- `generate-manifests` and `render-static` return exit code 2 for partial
  sampling and 1 for failed sampling; render errors also return 1. Failed scene
  indices and diagnostics remain in `metadata/indexes/scene_sampling_failures.jsonl`.
- A Clarity job requires a completed, validated render variant targeted by
  `input_targets`, with both input WAV and render metadata present.
- `render-static` assumes the monorepo layout where `apps/orchestrator`,
  `apps/matlab`, and `apps/clarity-backend` are sibling directories.
- If `apps/matlab` is missing, `render-static` fails before invoking MATLAB.
- The orchestrator writes `backend_invocation` metadata into Clarity jobs, but
  actual execution still happens via `uv run --project <sibling-backend>` or the
  configured entrypoint.
- The supported pipeline is static. Dynamic rendering remains outside the
  supported orchestrator workflow.
