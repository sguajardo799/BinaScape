# Tasks

Conventions for every task below:
- **Evidence.** Record results in `openspec/changes/add-pyroomacoustics-render-backend/verification.md`: commands, pass/fail counts, versions, measured values.
- **Linux.** Tests marked **[Linux]** must run on Linux without MATLAB.
- **Windows.** Tests marked **[Windows/RAVEN]** need MATLAB, the ITA Toolbox and a licensed RAVEN install, and are executed by the operator.
- **Pyroomacoustics package.** Tests marked **[pra]** need the `pyroomacoustics` extra installed.
- **Paths** are relative to `apps/orchestrator/` unless stated.

## 1. Preflight, environment and baseline (gating)

- [x] 1.1 Record the starting state in `verification.md`: branch, HEAD, `git status --short`, untracked and modified files (expected to be agent tooling only), and `openspec validate add-pyroomacoustics-render-backend --strict` output. Verify that no file outside the change directory differs from the start of the apply session.
- [x] 1.2 **Requires operator authorization to install dependencies.**
  - Create the repo-local environment with `uv sync --package acoustic-orchestrator --extra dev` (`.venv/` is git-ignored).
  - Run `uv run --package acoustic-orchestrator pytest apps/orchestrator/tests -p no:cacheprovider` and `uv run --package output-analyzer pytest apps/output-analyzer/tests -p no:cacheprovider`.
  - Record per-suite pass/fail/error counts and every failing test id as the **pre-existing baseline**.
  - Verify the counts are recorded; do not fix pre-existing failures in this change.
- [x] 1.3 **Pyroomacoustics compatibility spike (gating for groups 3–9).**
  - Run in a throwaway environment outside the repository, e.g. `uv run --isolated --python 3.12 --with "pyroomacoustics==0.10.1" --with "numpy==2.4.6" --with "scipy==1.17.1"`.
  - Record that the install succeeds and the installed versions.
  - Record that these APIs exist and behave as described in design.md: shoebox and extruded-polygon rooms; `Material` with explicit `center_freqs` and scattering; `compute_rir` and `room.rir[mic][src]`; `pyroomacoustics.random.seed`; the fractional-delay length constant and its `// 2` global delay; the RIR high-pass filter defaults; ray tracing disabled by default.
  - If any check fails, record it and choose the nearest compatible version.
  - Verify by updating the pin in design.md D13 **before** starting group 2.
- [x] 1.4 **[Linux] Capture RAVEN golden references before any code change.** For `configs/experiments/circular_array_only.yml` and `configs/experiments/static_example.yml`:
  - Store the normalized static config (`normalized_static_config`).
  - With the RAVEN path patched to a fake runner, store the generated scene and runtime manifests.
  - Store them as test fixtures under `tests/fixtures/raven_golden/`.
  - Verify the fixtures load and that a test comparing freshly generated values to them passes on the unmodified code.

## 2. Backend declaration and configuration

- [x] 2.1 Add `src/acoustic_orchestrator/pipeline/render_backends.py`, a pure-Python declarative table with no Pyroomacoustics import. Per backend it declares: name; supported sample rates ({44100} for both); expected T30 `method` and `estimated_from`; required `summary.render_backend` (non-RAVEN only); supported receiver output types; supported Pyroomacoustics version range. Verify with unit tests that the RAVEN entry reproduces today's literals (`rpf.getT30`, `room`, 44100).
- [x] 2.2 Extend `config/models.py`:
  - `RenderConfig.backend: Literal["raven","pyroomacoustics"] = "raven"`.
  - `AppConfig.raven: RavenConfig | None`.
  - A new strict `PyroomacousticsConfig`: `max_order` int ≥0, `max_order_non_shoebox` int ≥0 defaulting to `max_order`, `air_absorption` bool, optional `temperature_c` and `humidity_pct` given together, with humidity in [0,100].
  - A new optional `AppConfig.pyroomacoustics`.

  Verify with model unit tests for valid, missing, negative and partial-atmosphere cases.
- [x] 2.3 Add `config/backend_validation.py` and call it from `validate_config`:
  - Make the `raven.base_rpf_file` existence check conditional on the `raven` backend.
  - Under `raven`, reject a `pyroomacoustics` block.
  - Under `pyroomacoustics`, reject a `raven` block, explicitly set RAVEN-only `render` fields (via `model_fields_set`), enabled `binaural_hrtf`/`bte_rear_hartf`/`bte_front_hartf`, any source `directivity`, `hearing_degradation.enabled`, `scene_type: dynamic`, and sample rates outside the table.
  - Verify with one test per scenario of the requirements "Backend-specific configuration blocks" and "Explicit rejection of unsupported backend features" in `specs/render-backend-selection`. Each error must name the field.
- [x] 2.4 Implement the dependency check with `importlib.metadata.version("pyroomacoustics")` (no import) against the table's version range. Verify with tests that monkeypatch a missing package, a too-old version and a supported version.
- [x] 2.5 Make `pipeline/static_plan.py::normalized_static_config` omit `render.backend` when it is `raven` and omit `pyroomacoustics` when it is `None`. Verify that the 1.4 golden normalized configs match exactly, with and without an explicit `render.backend: raven`.
- [x] 2.6 **Requires operator authorization to modify dependencies and the lockfile.**
  - Add the optional extra `pyroomacoustics = ["pyroomacoustics>=0.10.1,<0.11", "numpy>=2.0", "scipy>=1.17", "soundfile>=0.13"]` (pin as confirmed by 1.3) to `apps/orchestrator/pyproject.toml`, and update `uv.lock`.
  - Verify that `uv sync --package acoustic-orchestrator --extra dev` without the extra does not install `pyroomacoustics`, and that the full orchestrator suite matches the 1.2 baseline.

## 3. Adapters: coordinates, geometry and materials

- [x] 3.1 Add `backends/pyroomacoustics/frames.py` with `public_to_backend` and `backend_to_public`, the rotation matrix constant and the per-room translation `t = (-min x, max z, 0)` (design D6, amended). Verify with property tests: round trip within 1e-9 m, determinant +1, `y = 1.5 → Z = 1.5`, public yaw +90° view direction → backend +Y, and a normalized shoebox footprint maps into `[0, length] x [0, width]`.
- [x] 3.2 Add a pure-data room description builder (no Pyroomacoustics import). It maps `room.geometry` (`footprint_vertices_m`, `height_m`, `wall_ids`) to backend polygon vertices with normalized winding, plus per-wall surface ids bound by physical edge, and floor/ceiling ids. Unknown geometry types raise an invalid-input error. Verify with tests for shoebox, trapezoid and L-shape: area preserved within 1e-9 m², and every wall's inverse-transformed edge midpoint equals the public `wall_id` edge midpoint within 1e-9 m.
- [x] 3.3 Add `backends/pyroomacoustics/materials.py`, which converts 31-band `effective_absorption` and `effective_scattering` to the ten octave values using `RAVEN_OCTAVE_CENTER_THIRD_INDEXES`. It rejects non-finite or out-of-[0,1] values without clamping. Verify with tests: index 17 = 0.12 → 1000 Hz = 0.12, ten output bands, and rejection cases.
- [x] 3.4 **[pra]** Build backend rooms from 3.2/3.3: a rectangular room for shoebox, and an extruded polygon for trapezoid and L-shape, with materials by surface and ISM-only settings (`max_order` or `max_order_non_shoebox`, `air_absorption`, temperature/humidity, ray tracing off, randomized ISM off). Verify with tests: the number of walls, each wall's material identity, and that the parameters are applied. Characterize how the 31.5 Hz and 63 Hz coefficients map onto the backend's bands, and record that in `verification.md`.
- [x] 3.5 **[pra]** Compute per-(source, microphone) RIRs for the `circular_array` descriptor, placing microphones from `receiver.circular_array.microphones[*].position_m` in `microphone_index` order (never recomputed from radius or yaw). Seed `pyroomacoustics.random.seed(effective_seed)`. Read and return the global delay, the high-pass settings and the speed of sound from the library at runtime. Verify with an anechoic (`max_order=0`) test that each RIR has a single dominant peak and that the reported timing fields are present.

## 4. Signal synthesis and T30 measurement (pure numpy/scipy)

- [x] 4.1 Add a MATLAB-compatible rounding helper (half away from zero). Verify with tests on 22050.5 → 22051, -0.5 → -1 and 2.5 → 3.
- [x] 4.2 Implement source preparation: load the WAV, polyphase resampling to the render rate when needed, arithmetic-mean downmix with the recorded policy, peak normalization to 0.99 (zero signal unchanged), then gain `10^(gain_db/20)`. Verify with unit tests covering mono, stereo, silent, 48 kHz input and the gain order.
- [x] 4.3 Implement per-source full convolution, the optional `trim_reverb_tail` (`len_rir - 1` samples), placement at the rounded offset, summation, the target-duration trim or pad (`max(1, round(T*fs))`) and the 0.999 peak guard. Verify with tests: output lengths, linearity of two sources within 1e-6 when no guard fires, 2.0 s → 88200 samples, and the guard scaling.
- [x] 4.4 Implement background noise:
  - layer seeds (explicit seed / `effective_seed + i - 1` / `i`);
  - colored noise by spectral shaping (white 0, pink -1, brown -2) with zero mean and unit RMS;
  - WAV layers (resample, loop or trim);
  - channel fitting (`mono_replicated`, `channel_to_channel`, mismatch error);
  - independent SNR scaling against the pooled pre-noise RMS, with `silent_signal`/`silent_noise` skips;
  - the second 0.999 guard and its metadata.

  Verify:
  - pink noise at 10 dB has a measured SNR of 10 ± 0.01 dB before the guard;
  - two runs are identical;
  - the metadata keys equal the MATLAB key set;
  - `render_validation.validate_noise` accepts the produced metadata.
- [x] 4.5 Implement the WAV writer (RIFF, 16-bit PCM, N channels, render rate, parent directories created). Verify that `render_validation.validate_wav` accepts the output with the expected channel count and full decode.
- [x] 4.6 Add `backends/pyroomacoustics/t30.py`: octave filtering at the ten centers with the 16 kHz upper edge clipped to 0.95·Nyquist; backward filtering; Schroeder integration; a fit from -5 to -35 dB extrapolated to 60 dB; per-pair invalidation when -35 dB is not reached; the band mean over pairs with null for any invalid pair; and the descriptor fields, with `method: schroeder_backward_integration_v1` and `estimated_from: rir`. Verify:
  - synthetic decays with T60 of 0.3, 0.6 and 1.0 s are within ±5% for 125 Hz to 16 kHz and ±10% for 31.5 and 63 Hz;
  - a truncated decay gives a null band and `status: unavailable`;
  - the descriptor passes the backend-aware `validate_t30` from 6.5.

## 5. Worker process, records and sidecar

- [x] 5.1 Implement the completion, failure and event writers: write to `<file>.tmp`, check that the target does not exist, then `os.replace`; SHA-256 over exact bytes; the event schema with sequence numbers and UTC timestamps. Verify against the existing readers: `RenderAttempt.inspect_completion` returns `valid`; the failure reader accepts an `invalid_input` report; `MatlabDiagnostics.collect_evidence` parses the events. Verify also that a pre-existing completion path is refused.
- [x] 5.2 Implement the sidecar builder:
  - every field required for array variants by `read_render_metadata`/`validate_render`;
  - the legacy z-reflected source pose representation;
  - `summary.render_backend: "Pyroomacoustics"`;
  - a `backend` block with name, version, the `simulation` parameters (`method: ism`, orders, `air_absorption`, temperature, humidity, speed of sound, `ray_tracing: false`, `randomized_image_sources: false`, `seed_applied`), the transform matrix and translation, the converted materials with `scattering_applied: false`, the RIR timing (global delay, high-pass settings, lengths) and `t30_pairs_s`.

  Verify that `validate_render(canonical=True)` reports a valid result on a builder-produced sidecar plus a WAV from 4.5 (after 6.5).
- [x] 5.3 Add the worker entrypoint `python -m acoustic_orchestrator.backends.pyroomacoustics.worker <snapshot> --attempt-id --variant-id --manifest-sha256 --completion-path --failure-path [--events-path]`.
  - Verify the snapshot hash; on load or validation errors publish an `invalid_input` failure report and exit non-zero.
  - Emit the stage, step and artifact events and `entrypoint_completed`.
  - Write the WAV, then the sidecar, then the completion record.
  - Exit 0 only after publication.

  **[pra]** Verify with subprocess tests on a minimal snapshot: success yields a valid completion; a snapshot missing the array descriptor yields a failure report and a non-zero exit; an injected exception yields a non-zero exit and no completion.
- [x] 5.4 **[pra]** Verify determinism: run the worker twice in separate processes on the same snapshot (two attempt directories). The WAV SHA-256 hashes must be equal and the sidecars must be JSON-equal.

## 6. Orchestrator integration

- [x] 6.1 Refactor `pipeline/matlab_runner.py` to extract the shared process core: attempt record, diagnostics, `subprocess.run(check=True)`, the status mapping and `MatlabProcessResult`. Keep `render_manifest_with_matlab`'s signature and exact command. Verify that the existing runner, diagnostics, retry, resilience and interruption tests pass unchanged, and that a new test asserts the MATLAB command list is byte-identical to the 1.4 capture.
- [x] 6.2 Add `render_manifest_with_pyroomacoustics` using the shared core. It builds the worker command from `sys.executable` and the attempt context, and sets `OMP_NUM_THREADS=1` and `PRA_NUM_THREADS=1` in the child environment. Verify with a fake `subprocess.run`: the attempt record stores the command, a non-zero exit yields `CalledProcessError` semantics, `process_status` is mapped as for MATLAB, and the console log is written.
- [x] 6.3 Dispatch in `render_pipeline._run_single_render_task` by the runtime manifest's `render.backend` (absent means `raven`), keeping both runners as module-level names. Verify with tests that monkeypatch each runner, that RAVEN manifests always reach `render_manifest_with_matlab`, and that existing tests patching `render_pipeline.render_manifest_with_matlab` still pass.
- [x] 6.4 Under `pyroomacoustics`, write `render.backend` and `render.backend_parameters` into scene and runtime manifests, set `base_rpf_file: null`, and make `static_plan._references` tolerate a null RPF. Verify that the RAVEN scene and runtime manifests equal the 1.4 golden fixtures, and that a Pyroomacoustics runtime manifest contains the backend and parameters.
- [x] 6.5 Make `pipeline/render_validation.py` and `pipeline/output_index.py` backend-aware through the 2.1 table:
  - the sample-rate set replaces the literal 44100 in `validate_wav`, the runtime check and the defaults, plus `output_index` ≈L735;
  - the T30 `method` and `estimated_from` come from the table;
  - for non-RAVEN variants, `summary.render_backend` and `backend.name` must match the runtime backend.

  Verify that the existing RAVEN validation tests and fixtures give identical outcomes, and add tests for a Pyroomacoustics descriptor accepted, a cross-backend method rejected, and a backend identity mismatch rejected.
- [x] 6.6 Accept the step `pyroomacoustics_rir` in `MatlabActivityReader` and terminal progress. Verify with a progress test fed worker events.
- [x] 6.7 Verify resume isolation with tests:
  - a RAVEN-rendered run resumed with `render.backend: pyroomacoustics` reuses no variant and raises the existing plan or overwrite conflict without modifying files (byte and mtime snapshot);
  - a Pyroomacoustics run resumed with a different `max_order` is not treated as equivalent input;
  - an unchanged Pyroomacoustics run reuses its valid variants without launching the worker.

## 7. Acoustic and physical validation [pra][Linux]

- [x] 7.1 Direct-path arrival in an anechoic shoebox: one source 2.0 m from one microphone at 44100 Hz; the peak index equals `round(2.0/c*fs) + global_delay` ±1, using the recorded `c` and delay.
- [x] 7.2 Distance attenuation in an anechoic room: the direct-path energy ratio between 1.0 m and 2.0 m is 4 ± 5%, with energy summed in a ±`global_delay` window around each peak.
- [x] 7.3 Circular array: rendered microphone positions equal the descriptor within 1e-9 m after the round-trip transform. For each k of a 3-microphone and an 8-microphone array, a source placed 1 m along microphone k's direction gives the earliest arrival on channel k+1.
- [x] 7.4 Inter-microphone delay: the cross-correlation lag between two channels equals the geometric delay difference in samples ±1, for three source positions.
- [x] 7.5 Reverberation behavior:
  - the same shoebox with uniform absorption 0.1 and then 0.4 gives lower T30 in every valid band for 0.4;
  - two sources with different `start_time_s` appear at their rounded offsets in an anechoic render.
- [x] 7.6 Characterize the legacy angular convention with a 4-microphone array at yaw +90°:
  - microphone 0 is at `[0,0,+r]` from the center;
  - a `fixed_position` source at azimuth 0° with receiver yaw +90° lies toward public -Z.

  Verify that the test documents the opposite rotation direction as the known risk, without changing any geometry.
- [x] 7.7 Calibration benchmark on the `circular_array_only` room ranges (shoebox) and an L-shape range. For several `max_order` / `max_order_non_shoebox` values, record the worker wall time per variant, the T30 valid-band rate and the canonical rejection rate. Choose the documented example values and record the rationale in `verification.md`.

## 8. Retry, resume, recovery and diagnostics integration [Linux]

- [x] 8.1 A worker crash (injected non-zero exit after an observed termination, no completion) consumes the `execution.matlab_process_retry` budget, counts toward the batch circuit breaker, and is retried with the same snapshot input and seed. Verify the chain and circuit records.
- [x] 8.2 An invalid snapshot produces an `invalid_input` failure report, is classified as a deterministic failure and is not retried. Verify the classification and the attempt count.
- [x] 8.3 `post_completion` recovery: the worker publishes a valid completion then exits non-zero (injected). The decision is `continue_with_warning` with `recovery_reason: post_completion`, and the warning is visible in the summary. Verify through `evaluate_render` and the CLI summary output.
- [x] 8.4 `reconcile-static` on an interrupted Pyroomacoustics attempt behaves as for MATLAB: inspection by default, the `--apply` guards, `exit_code=null` and no worker launched. Verify with an interruption-recovery test using a worker attempt.
- [x] 8.5 Diagnostics: `invocation.json`, `console.log` and `events.jsonl` exist for worker attempts, and diagnostic-only write failures do not change the decision. Verify with a diagnostics integration test.

## 9. Linux end-to-end and documentation

- [x] 9.1 Add the example config `configs/experiments/circular_array_pyroomacoustics.yml`: an array-only, 3-microphone, radius 0.05 m, shoebox room, 2 sources, colored noise, ISM orders from 7.7, no `raven` block. Add it to the `.gitignore` allowlist next to the existing examples. Verify that the configuration validates and that `git check-ignore` does not ignore it.
- [x] 9.2 **[pra][Linux]** End-to-end test: run `acoustic-orchestrator render-static` on a minimal temporary copy of 9.1 (1 scene, short sources from test fixtures) with a PATH that contains no `matlab`. Verify:
  - exit code 0;
  - the render index row is `completed` with valid validation;
  - `completion.json` is valid;
  - the WAV has 3 channels at 44100 Hz;
  - the sidecar backend block is present;
  - a second run reuses the variant without launching the worker.
- [x] 9.3 Document the manual Linux validation commands (`uv sync --package acoustic-orchestrator --extra dev --extra pyroomacoustics`, the orchestrator tests with `BINASCAPE_REQUIRE_PYROOMACOUSTICS=1`, and the output-analyzer tests, excluding Clarity and every MATLAB/RAVEN test). No CI workflow is added; CI/CD automation is deferred to a separate change. Verify with a local run of these commands and record the result.
- [x] 9.4 Characterize `output-analyzer analyze` on the 9.2 run directory without changing analyzer code. Record whether it completes, how `render_backend: Pyroomacoustics` scenes and the new T30 method are reported, and any errors, in `verification.md` as input for a follow-up change.
- [x] 9.5 Update the documentation: `apps/orchestrator/README.md` (backend selection, `pyroomacoustics` block, rejections, Linux usage), `docs/architecture/contracts.md` (manifest `render.backend`/`backend_parameters`, the sidecar `backend` block, the T30 method, the coordinate transform, the angular-convention risk), and `docs/development/setup.md` and `testing.md` (extra install, Linux commands, Windows-only suites). Verify that every documented field and command matches the code and the tests.

## 10. Regression and final acceptance evidence

- [x] 10.1 **[Linux]** Re-run the 1.2 suites plus all new tests. Verify that there are no failures beyond the recorded pre-existing baseline, and record the counts.
- [x] 10.2 **[Linux]** RAVEN compatibility regression: the normalized configs and manifests equal the 1.4 golden fixtures, the MATLAB command shape is unchanged (6.1), and the existing RAVEN-fixture validation outcomes are unchanged (6.5). Verify that the tests pass.
- [x] 10.3 **[Linux]** Clarity compatibility: `tests/test_clarity_handoff.py`, `tests/test_clarity_runner.py`, `tests/test_render_pipeline_clarity.py` and the Clarity eligibility rules pass unchanged. If the operator authorizes installing the Clarity dependencies, also run `apps/clarity-backend/tests`. Verify and record the results.
- [x] 10.4 **[Windows/RAVEN]** The operator runs MATLAB `runtests('apps/matlab/tests')`, `tests/verify_render_acceptance.py` and `tests/verify_circular_array_e2e.py` against the RAVEN backend on the implementation branch. Verify by recording the results. If the environment is unavailable, record **BLOCKED** with the reason; the operator must acknowledge this before archive.
- [x] 10.5 Map every scenario in `specs/**/spec.md` of this change to a test id or recorded evidence in `verification.md`, run `openspec validate add-pyroomacoustics-render-backend --strict`, and confirm that `git status` shows no changes outside the files listed in design.md and these tasks. Verify that the mapping has no unmapped scenarios.
