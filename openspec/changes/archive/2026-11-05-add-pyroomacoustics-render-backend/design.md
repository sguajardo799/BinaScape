## Context

See `proposal.md` (Why) for motivation. This section records only the current-state facts that constrain the design. All facts were verified at `develop` @ `eb7cb920cc06368baa7985f4ee5f7fcb16245656`. Paths are relative to `apps/orchestrator/src/acoustic_orchestrator/` unless they start with `apps/`.

### Render seam
- **Single call site.** There is exactly one render call: `pipeline/render_pipeline.py::_run_single_render_task` (≈L1156-1183). It runs on `ThreadPoolExecutor` worker threads, calls `render_manifest_with_matlab(...)`, and requires a `MatlabProcessResult` (`isinstance` check ≈L1176).
- **Acceptance and recovery.** `pipeline/render_acceptance.py::evaluate_render` treats a non-zero exit as recoverable (`post_completion`) only if the error is a `subprocess.CalledProcessError` with a matching return code. A missing completion record is acceptable when the exit code is 0.
- **MATLAB invocation.** `pipeline/matlab_runner.py::render_manifest_with_matlab` (≈L45-124) reserves the attempt and opens diagnostics (`console.log`, `events.jsonl`, `invocation.json`). It launches `matlab -batch run_raven_static_render('<snapshot>', <diag ctx>, <completion ctx>)` with `subprocess.run(check=True)` and **no timeout**, then records `process_status`, `exit_code` and `termination_observed`.
- **Completion and failure records.**
  - `pipeline/render_attempt.py` defines and verifies `completion.json`: schema 1, attempt identity, `manifest_sha256` and an exact `{kind, path, size_bytes, sha256}` inventory.
  - `pipeline/render_failure.py` defines `failure.json` with category `invalid_input`.
  - MATLAB writes both through `<file>.tmp` plus an atomic move (`apps/matlab/core/write_render_completion.m`).

### Render validation (`pipeline/render_validation.py`)
- **Sample rate.** 44100 Hz is pinned at L113 (`validate_wav`) and L599 (runtime manifest), with defaults at L160, L253 and L634. It is also pinned in `pipeline/output_index.py` ≈L735.
- **Channels.** The non-array channel count is `2` (L632).
- **T30 gate.** `validate_t30` (L519-553) requires ten bands `RAVEN_OCTAVE_FREQUENCIES_HZ` (31.5 Hz to 16 kHz) and exact descriptor fields `method: "rpf.getT30"` and `estimated_from: "room"`.
- **T30 rejections.** A missing block, a wrong band count, or non-finite or non-positive bands trigger the existing reject-and-resample loop in `_CanonicalStage._finish_round`.
- **Source poses.** Sidecar source poses are validated in the legacy representation (z reflected for schema 2.0/3.0, up vector `[0,1,0]`, L279-297).

### Plan identity and resume
- **Normalized config.** `pipeline/static_plan.py::normalized_static_config` (L134-145) includes the whole `model_dump` except a few execution fields. Adding any field with a dumped default changes plan identity.
- **Generator contract.** `_generation_contract` (L168-178) hashes the source of `experiment/{sampler, scene_builder, geometry, acoustic_geometry, room_acoustics, reverberation_sampling, treatment_catalog, receiver_geometry}.py`, `source_audio.py` **and `config/validator.py`**.
- **Resume equivalence.** `should_resume_variant` compares the whole runtime manifest through `equivalent_render_inputs` (`render_validation.py` L653-690).

### Config
- **RAVEN block.** `AppConfig.raven` is required. `validator.py` L378 requires `raven.base_rpf_file` to exist.
- **Render block.** `RenderConfig` mixes `sample_rate_hz` with RAVEN tuning fields.
- **Directivity.** Source directivity is `.daff`-only (validator L689-693).

### MATLAB array signal chain
The chain is `apps/matlab/static/render_static_scene.m`, its `core/` helpers, and `export_render_metadata.m`. The Python synthesis stage must reproduce it; `specs/pyroomacoustics-render-backend` lists the exact steps.

### Test doubles
Existing tests fake MATLAB by monkeypatching either `render_pipeline.render_manifest_with_matlab` or `matlab_runner.subprocess.run`, and some parse the `-batch` command string. `tests/render_fixtures.py` writes validator-conforming RAVEN sidecars and completion records.

### Environment and dependencies
- No CI exists.
- The Python tests have never been run in the exploration environment: dependencies were never installed there.
- Pyroomacoustics is absent from the repository and `uv.lock`.
- External documentation reports that the current stable release is 0.10.1, which ships cp312 wheels, adds `pyroomacoustics.random.seed` (since 0.10.0) and enables an RIR high-pass filter by default (since 0.9.0). The ISM output has a fractional-delay global offset of `frac_delay_length // 2` samples. These are external claims and must be confirmed by task 1.3.

## Goals / Non-Goals

**Goals:**
- One explicit backend dispatch point at the per-variant render invocation. No second pipeline and no plugin framework.
- Reuse attempt, completion, failure, diagnostics, retry, circuit-breaker, reconcile, resume, index and progress machinery unchanged in behavior.
- Zero observable change for `raven` configurations: manifests, normalized config, outputs and validation outcomes. The only exception is the documented one-time generator-contract change (D10).
- Make the render path for array variants runnable and testable on Linux without MATLAB.

**Non-Goals:**
- New array geometries, a receiver-model redesign, or DOA labels.
- Hybrid or ray-traced simulation, randomized ISM, directivity, binaural or SOFA support.
- Renaming `matlab_*` config keys, summary keys or directories.
- Adding a subprocess timeout. MATLAB has none today; this is a separate concern.
- Changing the output analyzer. Its behavior on Pyroomacoustics outputs is only characterized.
- Bit-exact parity with MATLAB noise, resampling or RAVEN acoustics.
- Sample rates other than 44100 Hz.
- CI/CD automation (e.g. GitHub Actions); the Linux suite is run manually.

## Decisions

### D1. Pyroomacoustics added alongside RAVEN, not instead of it
RAVEN is the only producer of the binaural/HARTF outputs that Clarity consumes, and its path carries extensive hardening (specs `static-render-*`, `matlab-*`).

- **Rejected: replacing RAVEN.** It would break Clarity, the analyzer's RAVEN comparison and every RAVEN-specific validation at once.
- **Rejected: a separate Pyroomacoustics pipeline.** It would duplicate planning, attempts and resume.
- **Chosen:** a backend selector with RAVEN as the default.

### D2. Integration boundary: the per-variant render process
```
 config ─> validate (backend rules) ─> sampler/planner (unchanged)
   ─> scene manifest ─> runtime manifest (+render.backend, params if non-RAVEN)
   ─> RenderAttempt snapshot (immutable)                       [unchanged]
   ─> _run_single_render_task ── backend = runtime.render.backend or "raven"
          |                                   |
          | raven                             | pyroomacoustics
          v                                   v
   render_manifest_with_matlab        render_manifest_with_pyroomacoustics
   (matlab -batch ..., unchanged)     ([sys.executable, -m, <worker>, snapshot,
          |                             --attempt-id ..., --completion-path ...])
          +---------------+-------------------+
                          v
      shared process core: attempt record, console.log, invocation.json,
      subprocess.run(check=True) -> MatlabProcessResult (CalledProcessError semantics)
                          v
      evaluate_render -> validate_render (backend-aware fs + T30 descriptor)
      -> classify_attempt / BatchCircuit / chain -> index upsert -> T30 gate
```
**Runner refactor.** `matlab_runner.py` will be refactored to extract the process-running core. `render_manifest_with_matlab` keeps its signature and its exact command, so the existing tests that parse the `-batch` string keep working. A sibling `render_manifest_with_pyroomacoustics` builds the worker command and reuses the same core.

**Selection.** `_run_single_render_task` chooses the runner from the runtime manifest's `render.backend`. Both runners stay module-level names in `render_pipeline`, so existing monkeypatches keep working.

**Rejected: an in-process call.**
- It would hold the GIL across threads.
- It would share the numpy/OpenMP state of the orchestrator.
- A crash could take down the orchestrator.
- It would need a synthetic `CalledProcessError` to satisfy acceptance.

**Cost.** One interpreter start plus the Pyroomacoustics import per attempt, about 1 s, which is negligible next to rendering.

### D3. Worker mirrors the MATLAB entrypoint contract
The worker lives at `acoustic_orchestrator/backends/pyroomacoustics/worker.py` and is run with `python -m`.

- **Inputs.** It reads only the snapshot and its CLI context.
- **Validation.** It validates its input and publishes `failure.json` (`invalid_input`) on a load or validation error.
- **Events.** It emits the same events as MATLAB:
  - stages `initialization`, `simulation` (steps `pyroomacoustics_rir`, `audio_postprocessing`) and `artifact_writing`;
  - `artifact_written` events;
  - `entrypoint_completed`.
- **Outputs.** It writes the WAV, then the sidecar, then the completion record. Each record is written to `<file>.tmp` and published by an atomic `os.replace`, after checking that the target does not already exist. A pre-existing completion path is an error.
- **Shared writers.** Completion, failure and event writers are small Python modules. They are format-compatible with the existing readers, and the readers are reused as the test oracle.

**Progress.** `MatlabActivityReader` accepts the additional step name `pyroomacoustics_rir`.

### D4. Why the MVP targets the existing circular array
The array already has:
- a backend-neutral descriptor with explicit, ordered public microphone positions;
- N-channel validation (`render_validation.py` L620-626);
- Clarity exclusion (spec `clarity-input-eligibility`).

It needs omnidirectional point receivers only, which Pyroomacoustics supports natively. Binaural outputs would need HRTF data in another format (DAFF to SOFA) and different physics. They are rejected under this backend; see the spec requirement on unsupported features.

### D5. ISM only for the MVP
ISM is deterministic, documented and well understood. External documentation describes the Pyroomacoustics hybrid ray tracer as "not thoroughly tested". Its stochastic tail also depends on version-specific seeding and a receiver radius (about 0.5 m) far larger than the array spacing.

- **Reverberation depth.** It is controlled by an explicit `max_order`.
- **Extruded rooms.** They get a separate `max_order_non_shoebox`, because general-polyhedron ISM cost grows roughly as walls^order (L-shape: 8 surfaces), whereas shoebox ISM is lattice-based.
- **No silent defaults.** Both orders are configuration, not hidden defaults, so the cost/realism trade-off is explicit and enters resume identity.

### D6. Coordinate transform
Public frame `(x, y-up, z)` → backend frame `(X, Y, Z) = (x, -z, y)`, matrix `[[1,0,0],[0,0,-1],[0,1,0]]` (determinant +1, a proper rotation). This puts the vertical axis on backend Z. It also maps public yaw (right-hand about +Y from +X, view vector `(cos, ·, -sin)`) to standard counter-clockwise azimuth in the backend XY plane. The transform lives in one pure function pair with property tests.

- **Winding.** `Y = -z` mirrors the 2D footprint, so the counter-clockwise public polygon becomes clockwise and must be reversed. Wall materials are attached by physical edge (vertex pair → `wall_id`), never by list position.
- **Shoebox.** It uses the rectangular room primitive. Wall assignment is resolved by matching each wall's outward normal or position against the public edge, not against RAVEN's `matShoebox1..6` slot order.
- **Origin translation (amended during apply, operator-authorized).** The rectangular primitive is anchored at the backend origin (`pyroomacoustics.ShoeBox` accepts only points in `[0, dims]`), while `Y = -z` places every normalized footprint at `Y <= 0`. The rotation is therefore followed by a per-room translation `t = (-min x, max z, 0)`, giving `(X, Y, Z) = (x - min x, max z - z, y)`. The transform stays rigid with no vertical component, so distances, delays and `Z = y` are unchanged. The translation is applied to every geometry type for uniformity and is recorded next to the rotation matrix. Evidence: task 1.3/3.1 in `verification.md`.
- **Sidecar frames.** Sidecar source poses keep the legacy (z-reflected) representation, so the existing validator and analyzer read them unchanged. Backend-frame data appears only under `backend.*`.

### D7. Material conversion
Reuse `experiment/room_acoustics.py`'s `RAVEN_OCTAVE_CENTER_THIRD_INDEXES` (center-frequency mapping), which already drives the Sabine RT30 stratification. Simulation and stratification therefore see the same octave coefficients.

- **Rejected: energy-averaging three third-octave bands.** It would diverge from the Sabine estimator and from the existing `frequency_mapping: center_frequency` vocabulary.
- **Low bands.** Coefficients are passed with explicit center frequencies. How the backend maps the 31.5 Hz and 63 Hz values onto its own octave bands is characterized in task 3.4 and recorded. They are not assumed to be modelled independently.
- **Scattering.** It is converted and recorded with `scattering_applied: false` (ISM ignores it). This is a documented backend difference, not a silent degradation: scattering was never a separately requested feature.

### D8. Signal synthesis belongs to BinaScape, not to the simulator
Pyroomacoustics computes impulse responses only (`compute_rir`). Mixing, gain, offsets, trimming, noise, SNR and peak guards are reimplemented in numpy/scipy after the MATLAB semantics (spec requirement on Python signal synthesis).

**Why not use the backend's own mixing and SNR:**
- Its `simulate(snr=...)` normalizes source variance at a reference microphone. That differs from the existing SNR, which is pooled-RMS of the whole mix, layer by layer.
- It has no per-source peak normalization, downmix policy or target-duration contract.

**Bonus.** This keeps the stage backend-agnostic for future backends.

**Implementation details:**
- MATLAB `round` semantics are implemented explicitly (half away from zero).
- Convolution uses `scipy.signal.fftconvolve`, full mode.
- Resampling uses `scipy.signal.resample_poly`.
- WAV output uses `soundfile` with `PCM_16`.
- The noise RNG is `numpy.random.default_rng(seed)`. Statistically equivalent, not bit-equal to MATLAB.

### D9. Backend-aware validation via a small declarative table
A pure-Python module `pipeline/render_backends.py`, which does not import Pyroomacoustics, declares per backend:
- `name`;
- `supported_sample_rates`;
- the expected T30 descriptor (`method`, `estimated_from`);
- `summary.render_backend` (required for non-RAVEN only);
- the supported receiver output types.

**Who reads it.** Config validation and `render_validation` read it. `validate_wav`, the runtime sample-rate check and `output_index` use the variant's backend set instead of the literal 44100.

**RAVEN values reproduce today's literals exactly**, so RAVEN outcomes are unchanged.

**Future rates.** Adding 48 kHz later means changing one table entry, plus the backend capability itself.

### D10. Configuration and identity compatibility
**Model changes:**
- `RenderConfig.backend: Literal["raven","pyroomacoustics"] = "raven"`.
- `AppConfig.raven: RavenConfig | None`.
- New `AppConfig.pyroomacoustics: PyroomacousticsConfig | None`.
- RAVEN-only `render` fields are detected via `model_fields_set`, so only an *explicit* setting is rejected under Pyroomacoustics.

**Backend rules.** They live in a new `config/backend_validation.py`, called from `validate_config`.

**Dependency check.** It uses `importlib.metadata.version("pyroomacoustics")` without importing the package.

**Normalized config.** `normalized_static_config` omits `render.backend` when it is `raven` and omits `pyroomacoustics` when it is `None`. Existing configs therefore produce identical normalized configs.

**Manifests.** For `raven`, scene and runtime manifests are unchanged.

For `pyroomacoustics`:
- the manifest's `render` gains `backend` and `backend_parameters`;
- `base_rpf_file` is `null`;
- `static_plan._references` skips a null RPF.

**Unavoidable consequence.** `config/validator.py` and `experiment/scene_builder.py` are hashed in the generator contract, so editing them changes plan identity once.

- **Effect.** RAVEN run directories planned before the upgrade report the existing explicit plan conflict on resume. They are never silently reused or overwritten.
- **Accepted:** this is the current, safe behavior for any generator change.
- **Rejected: routing all edits into unhashed modules.** It would require changing the hashed file list, which is itself a generator-contract change, or awkward indirection.

**Placement of new code.** The new backend modules are placed outside the hashed set (`backends/`, `pipeline/`, `config/backend_validation.py`), so later backend work does not invalidate plans again.

### D11. T30 measurement (keeps the gate active)
`backends/pyroomacoustics/t30.py` takes each (source, microphone) RIR and computes T30 per band:
1. **Filter.** Octave bandpass (Butterworth, order 3 per side, as second-order sections), applied to the time-reversed RIR (backward filtering, which reduces filter-decay bias). The upper edge is clipped to 0.95·Nyquist.
2. **Decay curve.** Schroeder backward integration of energy.
3. **Fit.** Least-squares linear fit of the decay curve in dB between -5 and -35 dB, extrapolated: `T30 = -60 / slope`.
4. **Validity.** A band is invalid if the decay curve never reaches -35 dB.
5. **Aggregation.** The reported value is the arithmetic mean over pairs. Any invalid pair makes the band null.

The method id is `schroeder_backward_integration_v1`. Per-pair values go under `backend.t30_pairs_s`.

**Effect on the gate.**
- The existing gate, which requires ten finite positive bands, keeps working unchanged.
- An ISM order too low to reach a 35 dB decay now causes explicit acoustic rejection and resampling. It does not bypass the gate.
- The gate does not compare against Sabine, consistent with the existing spec.

**Rejected:**
- the backend's own `measure_rt60`: it is broadband-only per pair and its method changed in 0.10;
- disabling the gate for Pyroomacoustics: forbidden by the scope.

### D12. Determinism and reproducibility measurement
- **Seeding.** The worker calls `pyroomacoustics.random.seed(effective_seed)` before building the room, and seeds noise per layer (D8).
- **Disabled randomness.** Randomized ISM and ray tracing are disabled.
- **Threads.** Worker processes run with `OMP_NUM_THREADS=1` and `PRA_NUM_THREADS=1` (set by the orchestrator in the child environment). This avoids oversubscription under `num_workers` and thread-order effects.
- **Measurement.** Two separate worker runs on the same snapshot must give equal WAV SHA-256 hashes and JSON-equal sidecars.
- **Scope.** Cross-platform and cross-version equality is not claimed. The version is recorded in the sidecar.

### D13. Dependencies and environment
- **Optional extra.** On `apps/orchestrator`: `pyroomacoustics = ["pyroomacoustics>=0.10.1,<0.11", "numpy>=2.0", "scipy>=1.17", "soundfile>=0.13"]`. The lower bounds align with the versions already locked for `clarity-backend` (numpy 2.4.6, scipy 1.17.1, soundfile 0.13.1).
- **Pin is provisional.** The exact pin is confirmed by task 1.3. If 0.10.1 is incompatible with numpy 2.4.x on Python 3.12, the task records the evidence and the pin is adjusted before any backend code is written.
- **Pin confirmed (task 1.3).** `pyroomacoustics==0.10.1` installs and behaves as described here with numpy 2.4.6 and scipy 1.17.1 on CPython 3.12 / Linux x86_64; the range `>=0.10.1,<0.11` is kept unchanged. Evidence in `verification.md`.
- **Environment.** A repo-local `.venv` (git-ignored), created with `uv sync`, only after operator authorization.
- **No new runtime dependency** for RAVEN-only installs.

### D14. Test architecture
- **Pure-numpy units** (no Pyroomacoustics import): frames, winding and wall mapping, material conversion, synthesis, noise, T30 on synthetic decays, the completion, failure and event writers checked against the existing readers, the backend table, and config rules.
- **Pyroomacoustics-dependent tests**: physics checks (arrival, attenuation, inter-mic delay, channel order), determinism, worker end to end, and the `render-static` Linux end-to-end run.
  - They use `pytest.importorskip`.
  - With `BINASCAPE_REQUIRE_PYROOMACOUSTICS=1` (strict mode, e.g. for release validation), a missing package is a failure, not a skip.
- **MATLAB/RAVEN tests** (`tests/verify_*.py`, `apps/matlab/tests/*`) remain opt-in on Windows and are listed separately in the tasks.
- **Linux validation.** Run manually with the documented commands: `uv sync --package acoustic-orchestrator --extra dev --extra pyroomacoustics`, then the orchestrator tests with `BINASCAPE_REQUIRE_PYROOMACOUSTICS=1`, then the output-analyzer tests. Clarity tests (torch-heavy) are excluded. No CI workflow is added in this change.

### D15. Deferred capabilities
Deferred:
- Hybrid ray tracing.
- Directivity.
- SOFA/HRTF binaural rendering in Pyroomacoustics.
- Other array geometries and DOA metadata.
- Other sample rates.
- Analyzer support for the new T30 method.
- A subprocess timeout.
- CI/CD automation.
- Renaming the `matlab_*` identifiers.
- Correcting the circular-array angular convention, which needs an authorized spec change.

## Risks / Trade-offs

- **[ISM order too low → T30 band never reaches -35 dB → repeated scene resampling or exhaustion]** → task 7.7 calibrates `max_order` on the example room ranges and records rejection rates. Failure is explicit (`raven_t30_*` reasons, `scene_sampling_failures.jsonl`), never silent.
- **[Polyhedral ISM cost explodes with order]** → Separate `max_order_non_shoebox`; task 7.7 benchmarks it. The documented starting values come from that measurement, not from guesses.
- **[Measured T30 differs from the Sabine-stratified target bin]** → This is already true for RAVEN, and the gate does not compare against Sabine. Both values are recorded. Datasets must use measured T30 as the label. This is documented as a known difference.
- **[31.5/63 Hz absorption not independently modelled; 16 kHz band clipped at Nyquist]** → Characterized in tasks 3.4 and 4.6 and recorded in the sidecar. The T30 tolerance for those bands is relaxed in tests (±10%).
- **[Generator-contract change invalidates plans of in-flight RAVEN runs]** → Explicit conflict, no data loss. Operators finish or rename in-flight runs before upgrading; this is noted in the migration plan.
- **[External Pyroomacoustics claims (seeding API, global delay, HPF default, numpy 2 wheels) may not hold for the pinned version]** → Task 1.3 is a gating spike. The code reads the delay and HPF settings from the library at runtime and records them, rather than hardcoding them.
- **[Legacy array angular convention conflicts with yaw/azimuth convention]** → Preserved and characterized (task 7.6). Fixing it is a separate authorized change. It matters before any DOA-label work.
- **[Reusing `matlab_*` names for a non-MATLAB process is confusing]** → Documented here and in the spec. Renaming is deferred to avoid breaking configs and summaries.
- **[No subprocess timeout: a hung worker blocks a slot]** → Same as MATLAB today. Deferred.
- **[16-bit PCM quantization]** → Same as the current MATLAB output format. A different subtype is a future option.
- **[The analyzer treats non-`RAVEN + ITA` backends differently]** → Characterized in task 9.4 without code changes. A follow-up change can add first-class support.

## Migration Plan

1. **Default unchanged.** Merge with `render.backend` defaulting to `raven`; existing experiment files need no edits.
2. **Before upgrading.** Operators finish, or start a new `run_name` for, any RAVEN run directory planned before the upgrade (D10).
3. **Opting in.** To use the new backend, install the extra (`uv sync --package acoustic-orchestrator --extra pyroomacoustics`), set `render.backend: pyroomacoustics`, add a `pyroomacoustics` block, remove `raven` and disable binaural outputs and hearing degradation. Use a new `run_name`.
4. **Rollback.** Set `render.backend: raven`, or remove it. Pyroomacoustics run directories are not reusable by RAVEN, by design, and remain intact.

## Open Questions

- **Starting value for `max_order` in the documented Pyroomacoustics example config.** It is determined by task 7.7's benchmark, and affects only the example YAML, not the specs or the code structure.
