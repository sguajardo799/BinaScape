# MATLAB Backend

This app contains the MATLAB/RAVEN backend for the static binaural rendering
stage. It consumes scene JSON manifests produced by the orchestrator or written
manually for backend development.

The supported flow is static rendering through `run_raven_static_render.m`.
Dynamic rendering is not supported yet: `run_raven_dynamic_render.m` exists as
an entrypoint, but `dynamic/render_dynamic_scene.m` is still a placeholder.

## Requirements

- MATLAB
- ITA Toolbox
- RAVEN
- Local RAVEN project files, HRTF/DAFF files, source audio, directivity files,
  and material files referenced by the input scene manifest

For root-first end-to-end runs, `matlab` must also be available on `PATH` so the
orchestrator can invoke it by subprocess.

## Directory Layout

```text
apps/matlab/
|-- assets/                     # Local example audio and HRTF assets, when present
|-- core/                       # Loading, validation, audio, metadata, and helpers
|-- dynamic/                    # Preliminary dynamic-rendering work
|-- example/                    # Scene JSON examples
|-- static/                     # Static rendering implementation
|-- tests/                      # MATLAB tests
|-- run_raven_static_render.m   # Supported static rendering entrypoint
`-- run_raven_dynamic_render.m  # Present, but not supported yet
```

## Recommended Use Through The Orchestrator

From the repository root, prefer the orchestrator for normal runs. It generates
the scene manifests, resolves output paths, and invokes this backend:

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

The root-level config must point to assets and a RAVEN `.rpf` file that exist in
the local environment.

## Direct MATLAB Use

For backend development, call the static entrypoint with a scene JSON path:

```matlab
run_raven_static_render('example/scene_static_0001.json')
```

Before running directly, review the scene manifest paths for source WAV files,
HRTFs, material files, the base `.rpf`, output WAV paths, and metadata paths.

## Important Files

- `run_raven_static_render.m`: entrypoint for supported static rendering.
- `static/render_static_scene.m`: main static rendering implementation.
- `core/load_scene_config.m`: loads scene JSON.
- `core/validate_static_scene_config.m`: validates the static scene contract.
- `core/prepare_raven_project.m`: prepares the RAVEN project for a local setup.
- `core/apply_background_noise.m`: applies post-render additive background
  noise.
- `core/export_render_metadata.m`: writes render metadata.
- `run_raven_dynamic_render.m`: dynamic entrypoint placeholder.
- `dynamic/render_dynamic_scene.m`: dynamic implementation placeholder.

## Static Scene Contract

The current static manifest contract includes these fields and behaviors:

- `receiver.hrtfs` is the canonical receiver format. It is a non-empty list of
  HRTF variants with `hrtf_id` and `hrtf_path`.
- The legacy singular `receiver.hrtf` field is accepted and normalized as a
  one-variant batch.
- `render.seed` is the preferred seed location. A legacy top-level `seed` is
  accepted when `render.seed` is absent.
- Each source may optionally define `directivity_path`.
- `room.materials` and `room.material_files` are required for the static flow.
- `room.material_files` must explicitly define `north_wall`, `south_wall`,
  `east_wall`, `west_wall`, `floor`, and `ceiling`.
- Each material file entry must include `material_id` and an absolute
  `material_path`.
- The validator checks consistency between `room.materials.<surface>` and
  `room.material_files.<surface>.material_id`.
- Material absorption and scattering coefficients are loaded from explicit
  material files and applied to RAVEN using the wall order `north`, `south`,
  `east`, `west`.

## Outputs Per HRTF

- `render.output_wav_path` and `render.output_metadata_path` can be explicit
  files or output directories.
- If an output path is a directory, filenames are derived from `scene_id`.
- A single HRTF run keeps the configured `project_name` without extra suffixes.
- Multiple HRTFs are rendered sequentially, one variant at a time.
- Multi-HRTF output filenames receive a safe `__<hrtf_id>` suffix before the
  file extension.
- Each HRTF variant writes its own WAV file and metadata JSON.
- `render.trim_reverb_tail` is optional and defaults to `false`. When `true`,
  each rendered source is trimmed before mixing.

Example output-directory config:

```json
"render": {
  "sample_rate_hz": 44100,
  "seed": 12345,
  "trim_reverb_tail": false,
  "output_wav_path": "../../data/raven_rendered/",
  "output_metadata_path": "../../data/raven_rendered/metadata/"
}
```

For `scene_id = "scene_static_0001"`, the backend writes:

- Single HRTF: `scene_static_0001.wav` and `metadata/scene_static_0001.json`.
- Multiple HRTFs: names such as `scene_static_0001__subject-001.wav` and
  `metadata/scene_static_0001__subject-001.json`.

## Background Noise

The static manifest may include `background_noise` as additive post-processing
on the final mixed audio. This happens outside RAVEN, after source mixing and
before the output WAV is written. It does not modify BRIRs and does not enable
the dynamic pipeline.

- If `background_noise` is absent or `background_noise.enabled` is `false`, no
  noise is added.
- With `enabled=true`, `layers` must be a non-empty list.
- Each layer is scaled by `snr_db` using the RMS power of the final pre-noise
  mix.
- `strategy="colored"` supports `white`, `pink`, `brown`, `blue`, and `violet`.
- Colored noise is generated deterministically from `render.seed` and the layer
  index, then replicated to all channels.
- `strategy="audio_file"` loads a WAV from `path`.
- Mono noise is replicated to all channels. Noise with the same channel count is
  used channel-by-channel. Other multichannel layouts are rejected.
- The legacy alias `strategy="audio_folder"` is accepted only when `path` points
  to a concrete WAV file, and is normalized as `audio_file`.
- If a noise WAV has a different sample rate, the backend attempts to resample
  it. If the required MATLAB function is unavailable, rendering fails with an
  explicit error.
- Short noise is looped; long noise is trimmed.
- After summing all layers, a peak guard limits audio to `0.999`. If it acts,
  metadata records the gain and warns that the effective SNR may change.

Minimal example:

```json
"background_noise": {
  "enabled": true,
  "layers": [
    { "strategy": "colored", "color": "pink", "snr_db": 20.0 }
  ]
}
```

The exported metadata records applied layers, original and normalized strategy,
noise color or file path, target SNR, effective seed, resampling, loop/trim,
channel adaptation, and clipping-guard information.

## Reproducibility Notes

- The effective seed is normalized and written to metadata as `effective_seed`
  and `seed_source`.
- In this repository, the seed is verified for MATLAB `rng` usage.
- The code does not currently verify that the RAVEN API exposes a compatible
  seed mechanism for complete BRIR reproducibility. Metadata documents that
  limitation explicitly.

## Tests

Run MATLAB tests from this directory in an environment with the required
toolboxes:

```matlab
runtests('tests')
```

Some tests use local mocks; full rendering still depends on the local
MATLAB/RAVEN setup and assets.

## Dynamic Rendering

Dynamic rendering is not implemented or supported yet.
