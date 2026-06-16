# Clarity Backend

Python backend for the hearing loss degradation stage. It consumes the JSONL job
manifest produced by `apps/orchestrator`, resolves hearing profiles from YAML,
applies Clarity MSBG processing, and writes the degraded WAV plus sidecar
metadata at the explicit paths requested by each job.

The backend is intentionally kept as a sibling project. The orchestrator calls
it through files and subprocesses instead of importing backend internals.

## Requirements

- Python `>=3.12,<3.13`
- `uv`
- Dependencies declared in `pyproject.toml`, including `pyclarity`, `numpy`,
  `scipy`, `soundfile`, and `PyYAML`
- Input WAV files compatible with the current MSBG integration

Current integration note: the MSBG path used here expects stereo WAV input at
44.1 kHz.

## Install

From this directory:

```sh
uv sync
```

From the repository root:

```sh
uv sync --project apps/clarity-backend
```

Check the CLI:

```sh
uv run --project apps/clarity-backend clarity-backend --help
```

## Relationship With The Pipeline

- `apps/orchestrator` decides which Clarity jobs exist and writes
  `manifests/runtime/clarity/clarity_jobs.jsonl`.
- `apps/matlab` produces upstream rendered WAV files and render metadata.
- `apps/clarity-backend` reads the explicit paths in each job, writes degraded
  audio and metadata, and exits with status `1` if any job fails.

Preferred invocation from the repository root:

```sh
uv run --project apps/clarity-backend clarity-backend run-manifest path/to/clarity_jobs.jsonl
```

When called from `apps/orchestrator`, the sibling invocation is typically:

```sh
uv run --project ../clarity-backend clarity-backend run-manifest .\manifests\runtime\clarity\clarity_jobs.jsonl
```

## CLI

Run a manifest:

```sh
uv run --project . clarity-backend run-manifest path/to/clarity_jobs.jsonl
```

The command:

1. Reads the JSONL manifest line by line.
2. Validates the required input paths for each job.
3. Resolves `hearing_profile_id` in the YAML profile catalog.
4. Converts the hearing profile into Clarity audiograms.
5. Processes the WAV with MSBG.
6. Writes the degraded WAV and metadata sidecar to the expected paths.
7. Continues after failed jobs.
8. Exits with code `1` if at least one job failed.

## Manifest Contract

Each JSONL line is one job. The backend consumes the current contract generated
by the orchestrator tests, including:

```json
{
  "schema_version": "1.0",
  "run_id": "sim_test",
  "job_id": "clarity__scene_static_0001__binaural_hrtf__mild_loss",
  "variant_id": "scene_static_0001__binaural_hrtf",
  "scene_id": "scene_static_0001",
  "output_type": "binaural_hrtf",
  "hearing_profile_id": "mild_loss",
  "hearing_profiles_path": ".../hearing_profiles.yaml",
  "backend_invocation": {
    "mode": "uv_project",
    "entrypoint": "clarity-backend",
    "use_uv": true,
    "backend_project_path": null
  },
  "input_wav_path": ".../scene_static_0001__binaural_hrtf.wav",
  "input_render_metadata_path": ".../scene_static_0001__binaural_hrtf__render.json",
  "output_dir": ".../outputs/degraded/binaural_hrtf/mild_loss",
  "expected_output_wav_path": ".../scene_static_0001__binaural_hrtf.wav",
  "expected_output_metadata_path": ".../scene_static_0001__binaural_hrtf.json"
}
```

Contract notes:

- Current `schema_version`: `1.0`.
- `backend_invocation` is preserved as part of the handoff contract.
- `expected_output_wav_path` and `expected_output_metadata_path` are
  authoritative. The backend must not infer fixed output names.
- The backend assumes the orchestrator already selected valid jobs and explicit
  paths, but still validates required files before processing.

## Hearing Profile Catalog

The backend expects a YAML file with a root `profiles` list. Each profile must
have a unique `hearing_profile_id`.

Minimal valid example:

```yaml
profiles:
  - hearing_profile_id: mild_loss
    ears:
      left:
        loss_db_by_band:
          250: 10
      right:
        loss_db_by_band:
          250: 12
```

Validation rules:

- `profiles` must be a non-empty list.
- Each profile must have a unique non-empty `hearing_profile_id`.
- Both `ears.left` and `ears.right` must exist.
- Each ear must define a non-empty `loss_db_by_band` map.
- Band names are normalized to strings.
- Loss values must be finite numbers.

The MSBG adapter maps the profile into left and right audiograms before
processing.

## Outputs

For each job, the backend writes to the explicit paths in the job:

- Degraded WAV: `expected_output_wav_path`
- Sidecar metadata JSON: `expected_output_metadata_path`

Successful metadata includes:

- job identity fields
- resolved input and output paths
- `status: "completed"`
- processor metadata
- applied degradation by band and audiogram frequency

Failed metadata includes:

- job identity fields
- resolved input paths
- `status: "failed"`
- `output_wav_path: null`
- `error.type` and `error.message`

## Development

Run tests:

```sh
uv run --project apps/clarity-backend pytest
```

Useful upstream references:

- `apps/orchestrator/README.md`
- `apps/orchestrator/tests/test_clarity_handoff.py`
- `apps/orchestrator/tests/test_output_index.py`

Those files describe how jobs are prepared, how output paths are reserved, and
which handoff states the orchestrator tracks.

## Limitations

- The CLI currently exposes only `run-manifest`.
- The integration is centered on file-based JSONL jobs, not a long-running
  service API.
- Validation focuses on required paths and hearing profiles.
- Current MSBG processing is limited to the formats supported by the adapter.
