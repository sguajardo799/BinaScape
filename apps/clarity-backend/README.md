# Clarity Backend

Python backend for the hearing loss degradation stage. It consumes the JSONL job
manifest produced by `apps/orchestrator`, resolves hearing profiles from YAML,
applies the job's processor (Clarity MSBG in Python, or WHIS v302 in MATLAB), and
writes the degraded WAV plus sidecar metadata at the explicit paths requested by
each job.

The backend is intentionally kept as a sibling project. The orchestrator calls
it through files and subprocesses instead of importing backend internals.

## Requirements

- Python `>=3.12,<3.13`
- `uv`
- Dependencies declared in `pyproject.toml`, including `pyclarity`, `numpy`,
  `scipy`, `soundfile`, `PyYAML`, and `rich`
- Input WAV files compatible with the processor: stereo at 44.1 kHz for MSBG;
  stereo at 44.1 or 48 kHz for WHIS
- For WHIS only: MATLAB and a WHIS installation (see [WHIS installation](#whis-installation))

The orchestrator permits only binaural HRTF outputs as Clarity targets.
HARTF and circular-array recordings are render-only, including arrays with two
microphones. That eligibility policy is enforced upstream during configuration,
handoff, and submission; it does not change the standalone stereo MSBG algorithm
or convert microphone-array channels into ears.

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
  `metadata/manifests/runtime/clarity/clarity_jobs.jsonl`.
- `apps/matlab` produces upstream rendered WAV files and render metadata.
- `apps/clarity-backend` reads the explicit paths in each job, writes degraded
  audio and metadata, and exits with status `1` if any job fails.

Preferred invocation from the repository root:

```sh
uv run --project apps/clarity-backend clarity-backend run-manifest path/to/clarity_jobs.jsonl
```

### Progress and ETA

The CLI owns a local progress display on stderr. It counts each nonempty manifest
line as one variant/profile job and advances only after its result metadata has
been written. Completed and failed counts are separate; an interrupted traversal
leaves unresolved jobs pending. A completed traversal with failures still exits
with status `1`. Progress never changes the requested WAV or JSON sidecar paths,
metadata contents, or the DSP algorithm.

Elapsed time remains visible during long jobs in an interactive terminal. The
approximate ETA needs three newly completed jobs in the current invocation;
failed jobs do not provide fast timing samples. There is no global pipeline ETA.
With redirected stderr, only brief transitions are printed without animation or
ANSI. Disable both forms without hiding functional errors:

```sh
uv run --project apps/clarity-backend clarity-backend run-manifest path/to/clarity_jobs.jsonl --no-progress
```

Direct Python calls to `run_manifest(path)` are silent by default. An optional
`progress=events.append` callback receives transient `JobEvent` observations, not
functional evidence. Presentation failures do not replace processing errors.
When the orchestrator launches this backend, its own display is suspended so
the two applications do not compete for the terminal.

When called from `apps/orchestrator`, the sibling invocation is typically:

```sh
uv run --project ../clarity-backend clarity-backend run-manifest .\metadata\manifests\runtime\clarity\clarity_jobs.jsonl
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
4. Processes `msbg` jobs one by one in Python, in manifest order.
5. Processes all `whis` jobs afterwards in a single `matlab -batch` session.
6. Writes the degraded WAV and metadata sidecar to the expected paths.
7. Continues after failed jobs.
8. Exits with code `1` if at least one captured processing failure occurred.

Recovery applies to errors caught while processing a structurally complete job.
Malformed JSONL, missing required fields, output-directory creation errors, or
metadata-write failures can abort the manifest instead of producing a per-job result.

## Manifest Contract

Each JSONL line is one job. The backend consumes the current contract generated
by the orchestrator tests, including:

```json
{
  "schema_version": "1.0",
  "run_id": "sim_test",
  "job_id": "clarity__scene_static_0001__binaural_hrtf__mild_loss__msbg",
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
  "output_dir": ".../output_audio/degraded/msbg/binaural_hrtf/mild_loss",
  "expected_output_wav_path": ".../scene_static_0001__binaural_hrtf.wav",
  "expected_output_metadata_path": ".../scene_static_0001__binaural_hrtf.json",
  "processor": "msbg",
  "calibration": {
    "variant_id": "scene_static_0001__binaural_hrtf",
    "mode": "fixed_scale",
    "configured": {"digital_rms1_spl_db": 90.0},
    "is_default": true,
    "random_seed": null,
    "presentation_level_db_spl": null,
    "effective_scale_db_spl": 90.0,
    "channels": {
      "left": {"mean_square": 0.005, "leq_db_spl": 66.99, "silent": false},
      "right": {"mean_square": 0.005, "leq_db_spl": 66.99, "silent": false}
    },
    "silent_channels": [],
    "level_measure": "rms_unweighted_full_file",
    "status": "ok"
  },
  "outer_middle_ear": "free_field",
  "processor_config": {"level_handling": "true_level"},
  "processing_signature": {
    "processor": "msbg",
    "calibration": {"mode": "fixed_scale", "configured": {"digital_rms1_spl_db": 90.0}, "is_default": true},
    "random_seed": null,
    "outer_middle_ear": "free_field",
    "msbg": {"level_handling": "true_level"}
  }
}
```

A `whis` job has `"processor": "whis"`, its outputs under `degraded/whis/...` and
`"processor_config": {"install_root": "...", "matlab_executable": "matlab", "synth_method": "dtvf"}`;
its signature carries `"whis": {"synth_method": "dtvf"}` instead of `"msbg"`.

Contract notes:

- Current producer handoff and backend result metadata use `schema_version: "1.0"`.
  The backend does not validate the incoming job schema version.
- `backend_invocation` belongs to the orchestrator handoff contract. The backend
  does not consume it or copy it into result metadata; `run_id` is also not copied.
- Relative paths in jobs resolve against the process working directory, not the
  manifest directory. The render sidecar is checked for existence; its content
  is not parsed by this backend.
- `expected_output_wav_path` and `expected_output_metadata_path` are
  authoritative. The backend must not infer fixed output names.
- The backend assumes the orchestrator already selected valid jobs and explicit
  paths, but still validates required files before processing.
- `calibration.effective_scale_db_spl` is K, the dB SPL of digital RMS 1.0 for this
  render. The orchestrator computes it once per render; the backend uses it as is and
  never recomputes it. A job without a finite K fails. Each ear is simulated at
  `K + 20*log10(rms_ear)` and the output stays in the input digital domain.

## Processors

### MSBG (`processor: msbg`)

- `processor_config.level_handling`:
  - `true_level` (default): pyclarity's `Ear.process` rescales each channel so that the
    RMS of its active frames equals the full-file RMS, and does not undo it. The backend
    measures that rescale (`active_rescale_db`, same `measure_rms(signal, fs, -12)`),
    raises `equiv_0db_spl` by the same amount and undoes it on the output, so the active
    frames are simulated at their real level and the output is not attenuated by pauses.
  - `legacy_active_rescale`: `equiv_0db_spl = K - 20` without compensation, the behaviour
    before this change. With `free_field` and K = 90 dB the WAV is bit-identical to the
    previous integration (`tests/fixtures/msbg_legacy_*.wav`).
- `outer_middle_ear`: `free_field` -> `ff`, `diffuse_field` -> `df`, `itu` -> `ITU`;
  `eardrum` sets `ear.src_correction = 0`, leaving only the middle-ear transfer.
- Latency: measured once per configuration with an impulse and a 0 dB HL audiogram
  (1078 samples at 44.1 kHz with pyclarity 0.8.0) and compensated so the output is
  sample-aligned with the input. `legacy_active_rescale` does not compensate
  (`latency_compensated: false`).
- `pyclarity` is pinned to `0.8.0`; `tests/test_msbg_levels.py` fails if the internals
  that `true_level` and `eardrum` rely on change.

### WHIS (`processor: whis`)

- All WHIS jobs of a manifest run in one `matlab -batch` session executing
  `matlab/binascape_whis_run.m`, which calls `WHISv302` directly (not `WHISv30_Batch`,
  which would normalise each channel to a fixed level and destroy the ILD).
- Per job and non-silent channel, the backend writes into `<manifest dir>/whis_work/<sha1(job_id)[:12]>/`
  (short names keep Windows paths under 260 characters)
  a 48 kHz float input (`resample_poly` 160/147 from 44.1 kHz) and a task with
  `srcsnd_spl_db = K + 20*log10(rms)`, `calib_tone_spl_db = K - 26`, the 7 thresholds
  (125 Hz copied from 250 Hz if missing), the 7 `compression_health` values,
  `synth_method` (`DTVF`/`FBAnaSyn`) and `out_mid_crct`
  (`FreeField`/`DiffuseField`/`ITU`/`EarDrum`).
- Jobs are finalised while MATLAB runs: back to 44.1 kHz (147/160), exact length,
  latency compensation (impulse through a 0 dB HL, compression-health-1 ear per
  configuration), clipping check, sidecar. A failing task fails only its job; if MATLAB
  dies, every job without a result gets a `WhisSessionError` sidecar.

## WHIS Installation

WHIS and the gammachirp filterbank are not vendored. Clone both next to each other in
an `install_root` of your choice:

```sh
mkdir -p /c/Tesis/whis && cd /c/Tesis/whis
git clone https://github.com/AMLAB-Wakayama/WHIS.git
git clone https://github.com/AMLAB-Wakayama/gammachirp-filterbank.git
```

Resulting layout (`StartupWHIS.m` expects `GCFBv234` two levels above `WHISv302`):

```text
<install_root>/
  WHIS/WHISv302/WHISv302.m
  gammachirp-filterbank/GCFBv234/GCFBv234.m
```

MATLAB requirements (from `matlab.codetools.requiredFilesAndProducts` on WHISv302, its DTVF and
FBAnaSyn syntheses and `binascape_whis_run.m`): MATLAB and the **Signal Processing Toolbox**.
Verified with MATLAB R2026b, WHIS commit `33f5c29` and gammachirp-filterbank commit `e936405`.

Then set `hearing_degradation.whis.install_root` (and `matlab_executable` if `matlab` is
not on `PATH`).

Measured speed (16-thread Windows machine, MATLAB R2026b, pyclarity 0.8.0, 3 s scenes):

| Processor | Compute time per second of stereo audio | Fixed cost |
|---|---|---|
| MSBG (`true_level`) | about 1.1 s | latency measurement once per process (about 1.5 s) |
| WHIS (DTVF, 48 kHz) | about 2.6 s (1.26-1.35x real time per channel) | one MATLAB session per manifest (about 10-15 s) plus a 1 s latency probe |

Integration checks with real MATLAB live in `tests/test_integration_matlab.py` and run only when
`BINASCAPE_WHIS_INSTALL_ROOT` points at an install root and `matlab` is on `PATH`:

```sh
BINASCAPE_WHIS_INSTALL_ROOT=C:/Tesis/whis uv run --project apps/orchestrator --extra dev pytest apps/clarity-backend/tests/test_integration_matlab.py
``` The orchestrator blocks WHIS jobs before submission if either directory
or MATLAB is missing. Commits of both checkouts (or `null` without `.git`) and the MATLAB
release are recorded in each sidecar.

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
          500: 12
          1000: 15
          2000: 18
          4000: 21
          8000: 24
      right:
        loss_db_by_band:
          250: 12
          500: 14
          1000: 17
          2000: 20
          4000: 23
          8000: 26
```

Validation rules:

- `profiles` must be a non-empty list.
- Each profile must have a unique non-empty `hearing_profile_id`.
- Both `ears.left` and `ears.right` must exist.
- Each ear must define a non-empty `loss_db_by_band` map.
- Band names are normalized to strings.
- Loss values must be finite numbers.
- `compression_health` is optional: a number in [0, 1] or a map with the same bands as
  `loss_db_by_band`.

The MSBG adapter additionally requires the six bands `250`, `500`, `1000`, `2000`,
`4000`, `8000` Hz in each ear. It ignores band `125` and `compression_health` (same
WAV with or without them) and lists them per ear in `ignored_profile_fields`; any other
extra band fails. WHIS requires `compression_health`, bands `250`-`8000` (`125`
optional) and no negative thresholds.

## Outputs

For each job, the backend writes to the explicit paths in the job:

- Degraded WAV: `expected_output_wav_path`
- Sidecar metadata JSON: `expected_output_metadata_path`

Successful metadata includes:

- job identity fields
- resolved input and output paths
- `status: "completed"`
- `processor`: `name`, `sample_rate_hz`, `latency_compensated`,
  `latency_compensation_samples`, `output_peak`, per-ear `channel_levels`;
  MSBG adds `package_version`, `level_handling`, `outer_middle_ear`, `src_pos`;
  WHIS adds `whis_version`, `gcfb_version`, `whis_commit`, `gcfb_commit`,
  `matlab_release`, `install_root`, `synth_method`, `out_mid_crct`,
  `processing_sample_rate_hz`, `resampled`, `latency_sample_rate_hz`
- `degradation_applied` per ear: bands and audiogram frequencies; WHIS adds
  `extrapolated_bands`, `compression_health_requested`, `compression_health_applied`,
  `pin_loss_db_act` and `pin_loss_db_pas`
- `calibration`: the render record of the job (plus `level_handling` for MSBG), so the
  sidecar alone gives the SPL at which each ear was simulated
- `outer_middle_ear`, `processing_signature`, `ignored_profile_fields`

Failed metadata includes:

- job identity fields
- resolved input paths
- `status: "failed"`
- `output_wav_path: null`
- `processor.name` and `processing_signature`
- `error.type` and `error.message`

Outputs keep the input sample rate and length and are written as 32-bit float, so filtering overshoot above full scale is kept, never clipped (`processor.output_peak`, `output_subtype`). Only MSBG with `legacy_active_rescale` keeps the input subtype (bit-identical to the previous integration) and fails with `Recorte` if an integer output would exceed 1.0.

## Development

Run tests:

```sh
uv run --project apps/orchestrator --extra dev pytest apps/clarity-backend/tests
```

Run this command from the repository root; pytest comes from the orchestrator
development extra in the shared workspace, not a backend-specific extra.

Useful upstream references:

- [Orchestrator guide](../orchestrator/README.md)
- [Clarity handoff tests](../orchestrator/tests/test_clarity_handoff.py)
- [Output index tests](../orchestrator/tests/test_output_index.py)

Those files describe how jobs are prepared, how output paths are reserved, and
which handoff states the orchestrator tracks.

## Limitations

- The CLI currently exposes only `run-manifest`.
- The integration is centered on file-based JSONL jobs, not a long-running
  service API.
- Validation focuses on required paths and hearing profiles.
- Current MSBG processing is limited to the formats supported by the adapter.
- WHIS is slower than real time (measured factors in the
  [simulator validation guide](../../docs/guides/calibration-simulator-validation.md)).
- WHIS DTVF applies minimum-phase gain filters: with hearing loss the output has a
  frequency-dependent group delay (about 20 samples at 44.1 kHz for a sloping loss) that
  is part of the simulated filter, not a constant latency, so it is not compensated.
- MSBG has no equivalent of `compression_health`; compare processors with several WHIS
  profiles (see the calibration guides).
- MSBG is not waveform-identical even with 0 dB HL: its gammatone resynthesis advances low
  frequencies relative to high ones (100-300 Hz by about 280 samples, 300-1000 Hz by about 70,
  above 3 kHz aligned at 44.1 kHz). The impulse-based latency compensation aligns the impulse
  peak, but speech cross-correlation peaks about 290 samples early. WHIS with `nh_reference`
  is near identity (SNR 23.5 dB, lag 0). Take this into account for waveform-level comparisons.

## Documentation

- [Documentation index](../../docs/README.md)
- [Calibration guide: simulator validation (NH vs IH listeners)](../../docs/guides/calibration-simulator-validation.md)
- [Calibration guide: neural-network datasets](../../docs/guides/calibration-nn-dataset.md)
- [Shared artifact contracts](../../docs/architecture/contracts.md)
- [Development and testing](../../docs/development/testing.md)
- [MATLAB renderer](../matlab/README.md)
- [Optional metadata analysis](../output-analyzer/README.md)
