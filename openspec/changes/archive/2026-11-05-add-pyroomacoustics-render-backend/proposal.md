## Why

BinaScape's static render can only run through MATLAB + ITA Toolbox + RAVEN on Windows: the RAVEN project path is hardcoded (`apps/matlab/core/prepare_raven_project.m:12`), RAVEN exposes no seed (0/91 byte-identical WAVs in the S8 benchmark, `odd/tasks/parallel-canonical-render.md`), and no part of the render path can be exercised on Linux or in CI. The project now targets synthetic multichannel datasets for source localization, which needs a reproducible, Python-native acoustic engine. Pyroomacoustics provides image-source room impulse responses for arbitrary microphone positions, but introducing it must not disturb the hardened RAVEN pipeline, the binaural/HARTF outputs or Clarity.

## What Changes

- Add an explicit render backend selector, `render.backend: raven | pyroomacoustics`, defaulting to `raven` so every existing experiment configuration keeps its current behavior unchanged.
- Add a Pyroomacoustics render backend, executed as an isolated Python worker process that honors the same per-attempt contract as the MATLAB entrypoint (runtime manifest snapshot in; WAV, metadata sidecar, completion/failure record and diagnostic events out). Existing attempt tracking, retries, circuit breaker, reconciliation, resume, indexing and progress are reused rather than duplicated.
- Initial backend scope: the **existing** `circular_array` receiver only, rendered with the Image Source Method (ISM) on the existing shoebox, trapezoid and L-shape rooms at 44100 Hz. Hybrid ray tracing is not part of this change.
- Add an explicit, reversible coordinate adapter between the public frame (`x`, `y`-up, `z`) and the Pyroomacoustics frame (`z`-up), and an explicit conversion of the existing 31-band absorption coefficients to the backend's octave bands. Acoustic equivalence with RAVEN is explicitly **not** claimed.
- Add a Python signal-synthesis stage that reproduces the MATLAB array-variant semantics (downmix, peak normalization, gain, full convolution, offsets, reverb-tail trim, target duration, background noise and SNR, peak guards, WAV export) without depending on Pyroomacoustics' own mixing or noise.
- Add a backend-independent T30 measurement (Schroeder backward integration over the ten existing octave bands) so the existing canonical T30 acceptance gate stays active for the new backend instead of being disabled.
- Reject unsupported combinations at configuration validation time: binaural/HARTF outputs, `.daff` source directivity, hearing degradation, dynamic scenes and RAVEN-only tuning fields under `pyroomacoustics`. Nothing is silently degraded to an omnidirectional or simplified equivalent.
- Make the `raven` configuration block required only when `render.backend` is `raven`.
- Record backend identity, version and simulation parameters in render metadata, and make backend identity part of resume compatibility so outputs from one backend are never reused as outputs of the other.
- Add an optional `pyroomacoustics` dependency extra to the orchestrator and a Linux test path (including a MATLAB-free end-to-end run), run manually and kept separate from MATLAB/RAVEN integration tests. CI/CD automation is out of scope.
- Record, without changing, the existing circular-array angular convention (microphone ring winding vs. yaw/azimuth convention) as a known risk, guarded by a characterization test.

No existing behavior is removed. Under the default `raven` backend no output, metadata field, name or validation outcome changes.

## Capabilities

### New Capabilities
- `render-backend-selection`: backend selector, backend-specific configuration validation, supported receiver/feature matrix, backend identity in metadata, and backend-aware resume compatibility.
- `pyroomacoustics-render-backend`: the Pyroomacoustics worker process contract, coordinate and material adapters, ISM impulse-response generation for the circular array, Python signal synthesis, backend-independent T30 measurement, determinism, and the mapping of MATLAB-process obligations from existing specs onto the worker process.

### Modified Capabilities
- `circular-microphone-array`: "Raw synchronized multichannel rendering" no longer names MATLAB as the only renderer; the selected render backend renders the array with the same channel, delay and level guarantees.
- `static-render-acceptance`: the functional completion record and its atomic publication are produced by the selected render backend process (MATLAB or the Pyroomacoustics worker); the canonical T30 gate accepts the T30 measurement method declared for the selected backend while keeping the ten-band, finite-positive requirement and the existing rejection/resampling policy.

## Impact

- **Code (future implementation):**
  - `apps/orchestrator/src/acoustic_orchestrator/config/`: the backend selector, the `pyroomacoustics` block, conditional `raven`, and new rejection rules.
  - `pipeline/matlab_runner.py` and `pipeline/render_pipeline.py`: backend dispatch at the existing per-variant invocation.
  - `pipeline/render_validation.py`: backend-aware T30 method and sample-rate source of truth.
  - `pipeline/static_plan.py` and resume compatibility: backend identity.
  - A new backend package for the worker, adapters, synthesis and T30.
- **Unchanged:**
  - `apps/matlab`, the RAVEN render path, `apps/clarity-backend` and the Clarity eligibility rules.
  - The output layout and naming patterns.
  - The scene sampler and the static planner's sampling semantics.
- **Dependencies:**
  - A new optional extra on the orchestrator: `pyroomacoustics` with numpy, scipy and soundfile. The exact pin is verified in the first implementation task; the planning target is Pyroomacoustics `>=0.10.1,<0.11`.
  - The `uv.lock` update happens during implementation.
- **Systems:** enables MATLAB-free rendering and testing on Linux. CI/CD automation is deferred to a separate change. MATLAB/RAVEN integration tests remain Windows-only and opt-in.
- **Not affected / out of scope:**
  - New array geometries.
  - The receiver redesign and DOA metadata.
  - Binaural rendering, SOFA and directivity in Pyroomacoustics.
  - Hybrid ray tracing.
  - Clarity changes.
  - Output-analyzer changes. The analyzer's behavior on Pyroomacoustics outputs is only characterized.
  - Fixing the legacy array angular convention.
  - Sample rates other than 44100 Hz. Validation is structured so that adding them later is a localized change.
