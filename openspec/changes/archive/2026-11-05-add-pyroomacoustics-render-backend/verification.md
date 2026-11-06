# Verification evidence — add-pyroomacoustics-render-backend

All commands run from the repository root unless stated. Environment: WSL2 Linux
6.6.87.1-microsoft-standard-WSL2 x86_64, uv 0.9.5.

## 1.1 Starting state (apply session start, 2026-11-03)

- Base branch at session start: `develop` @ `eb7cb920cc06368baa7985f4ee5f7fcb16245656`
  (equals the planning baseline).
- Implementation branch created with `git switch -c feat/add-pyroomacoustics-render-backend`.
  `git merge-base --is-ancestor develop HEAD` → true; `HEAD == develop == eb7cb92`;
  `develop` reference unchanged.
- Staged files: none.
- `git status --short` before and after the branch switch: identical (byte diff of the
  porcelain output empty).
  - 42 modified (`M`) entries, all agent tooling:
    `.agents/skills/openspec-*/SKILL.md` (6), `.claude/commands/opsx/*.md` (6),
    `.claude/skills/openspec-*/SKILL.md` (6), `.opencode/commands/opsx-*.md` (6),
    `.opencode/skills/openspec-*/SKILL.md` (6), `.pi/prompts/opsx-*.md` (6),
    `.pi/skills/openspec-*/SKILL.md` (6).
  - 8 untracked (`??`) entries: 7 agent tooling (`.agents/skills/openspec-new-change/`,
    `.claude/commands/opsx/new.md`, `.claude/skills/openspec-new-change/`,
    `.opencode/commands/opsx-new.md`, `.opencode/skills/openspec-new-change/`,
    `.pi/prompts/opsx-new.md`, `.pi/skills/openspec-new-change/`) plus this change
    directory `openspec/changes/add-pyroomacoustics-render-backend/`.
- No file outside the change directory differs from the start of the apply session
  (porcelain status, excluding the change directory, compared byte-for-byte: IDENTICAL).
- `openspec validate add-pyroomacoustics-render-backend --strict` →
  `Change 'add-pyroomacoustics-render-backend' is valid`.
- `openspec instructions apply` → progress 0/54.
- Environment facts: no `.venv` exists yet (`.venv/` is in `.gitignore`); uv-managed
  CPython 3.12.12 is available; `uv lock --check` resolves 129 packages (lockfile
  consistent); `pyroomacoustics` is absent from `uv.lock`; locked numpy 2.4.6,
  scipy 1.17.1, soundfile 0.13.1 (via clarity-backend). `*.wav` is git-ignored
  globally, so test WAVs must be generated at test time.

## 1.2 Repo-local environment and pre-existing test baseline

Operator authorization for 1.2 and 1.3 granted in-session (2.6 to be requested separately).

- `uv sync --package acoustic-orchestrator --extra dev` → created git-ignored `.venv/`
  (CPython 3.12.12); `uv.lock`, `pyproject.toml` files unchanged (`git status` clean for them).
  numpy 2.4.6 present transitively (matplotlib).
- `uv run --package acoustic-orchestrator pytest apps/orchestrator/tests -p no:cacheprovider`
  → **995 passed, 0 failed, 0 errors** (59.3 s).
- `uv run --package output-analyzer pytest apps/output-analyzer/tests -p no:cacheprovider`
  → **89 passed, 0 failed, 0 errors** (0.7 s).
- Pre-existing failing test ids: **none**. Baseline = 995 / 89 passing.

## 1.3 Pyroomacoustics compatibility spike

Isolated throwaway env (outside the repo; only uv's cache touched):
`uv run --isolated --no-project --python 3.12 --with pyroomacoustics==0.10.1 --with numpy==2.4.6 --with scipy==1.17.1 python -I spike.py`
(script kept in the session scratchpad, not in the repo). Run twice in separate processes
with `OMP_NUM_THREADS=1 PRA_NUM_THREADS=1`.

| Check | Result |
|---|---|
| Install on CPython 3.12.12 / Linux x86_64 (glibc 2.35, WSL2) | PASS — pyroomacoustics 0.10.1 cp312 wheel, numpy 2.4.6, scipy 1.17.1 |
| `importlib.metadata.version("pyroomacoustics")` | `0.10.1` |
| `pra.ShoeBox(dims, fs, materials=dict, max_order, air_absorption)` | PASS; walls named `west, east, south, north, floor, ceiling` with outward normals −X, +X, −Y, +Y, −Z, +Z |
| `pra.Room.from_corners(corners2xN, materials=list)` + `.extrude(h, materials={"floor","ceiling"})` | PASS; L-shape → 8 walls (6 side + floor + ceiling), volume 36.0 m³; side wall i spans corners i→i+1 |
| Winding | **`from_corners` silently reverses a clockwise footprint (`area_2d_polygon <= 0`) but does NOT reorder the materials list.** The adapter must therefore pass an already counter-clockwise footprint (spec "Winding reversal"); then the internal reversal is a no-op. |
| `pra.Material({"coeffs","center_freqs"}, {"coeffs","center_freqs"})` with explicit centers and scattering | PASS |
| Backend octave bank at 44100 Hz | 8 bands, centers 125, 250, …, 16000 Hz (`octave_bands_base_freq` 125). `Material.resample` linearly interpolates in log2(f) onto those centers and clips to [0,1]. With our 10 explicit centers, the 125–16000 Hz values pass through exactly; **31.5 Hz and 63 Hz values have no effect** (distinct 0.9/0.7 at 31.5/63 → resampled band 0 = 0.3, the 125 Hz value). The lowest band keeps DC. |
| `room.compute_rir()` / `room.rir[mic][src]` | PASS — `len(room.rir)` = n_mics, `len(room.rir[0])` = n_sources |
| `pra.random.seed(numpy=..., libroom=...)` | exists (seeds package numpy RNG and libroom RNG) |
| `frac_delay_length` | 81 → global delay `81 // 2 = 40` samples |
| Direct path (anechoic shoebox, d = 2.0 m, fs 44100, c 343) | peak index 297 = round(2.0/343·44100) + 40 → exact |
| Fractional delay | exact delay 297.64 samples → peak at 298 (sinc-interpolated, energy spread across neighbours) |
| RIR high-pass defaults | `rir_hpf_enable=True`, `rir_hpf_fc=10.0` Hz, `rir_hpf_kwargs={"n":2,"rp":5.0,"rs":60.0,"type":"butter"}`, applied with `sosfiltfilt` |
| Ray tracing / randomized ISM default | `simulator_state["rt_needed"] = False`, `["random_ism_needed"] = False` |
| Speed of sound | default `c = 343.0`; with `temperature=25, humidity=50` → `room.c = 347.02` |
| Air absorption (25 °C, 50 %) | 8 coefficients 1e-4 … 0.0229 at the bank centers |
| Determinism | `pra.random.seed(7)` + max_order 10 shoebox, SHA-256 over all RIR bytes identical in-process and across two separate processes (`a3d528b6…`; whole JSON output byte-identical) |
| Coordinate frames | backend is Z-up (floor normal −Z, extrude along +Z), consistent with D6 `(X,Y,Z) = (x,−z,y)` |

**Conclusion:** every API and behaviour assumed by design.md exists and behaves as described.
The planned pin `pyroomacoustics>=0.10.1,<0.11` is **confirmed** (no change to D13).
No contradiction with the approved specs. Note for implementation: materials stored
as float32 by libroom (e.g. 0.1 → 0.10000000149), so material-identity tests compare with
a float32 tolerance.

## 1.4 RAVEN golden references (captured before any source change)

- `git diff --quiet -- apps/orchestrator/src` → no source change at capture time.
- Capture module: `apps/orchestrator/tests/raven_golden.py` (regeneration only via
  `python tests/raven_golden.py`, never during tests). Fixtures in
  `apps/orchestrator/tests/fixtures/raven_golden/`:
  - `{circular_array_only,static_example}.normalized_config.json` — `normalized_static_config`
    of the shipped YAML (no assets needed); repository prefix → `<REPO>`.
  - `{circular_array_only,static_example}.manifests.json` — scene + runtime manifests from
    `render_static_scenes` with `render_pipeline.render_manifest_with_matlab` replaced by a
    fake runner (`render_fixtures.simulate_matlab_process`). The example YAML is copied into a
    fixture workspace with synthetic assets; only the Windows RPF path, `num_simulations`
    (100 → 2 for `static_example`) and `hearing_degradation.enabled` (→ false, avoids Clarity
    submission) are substituted. Temporary prefix → `<TMP>`. 1 scene/1 runtime manifest
    (array) and 2 scenes/2 runtime manifests (binaural) respectively.
  - `matlab_command.json` — exact `[matlab, -batch, <command>]` list from
    `render_manifest_with_matlab` with `subprocess.run` faked (attempt id and manifest hash
    replaced by placeholders); used by 6.1.
- `uv run --package acoustic-orchestrator pytest apps/orchestrator/tests/test_raven_golden.py`
  → **7 passed** on the unmodified code (fixtures load; fresh normalized configs, manifests
  and MATLAB command equal the goldens).

## 2.1–2.5 Backend declaration and configuration

Files: `pipeline/render_backends.py` (new, no Pyroomacoustics import), `config/models.py`
(`RenderConfig.backend`, `AppConfig.raven: RavenConfig | None`, `PyroomacousticsConfig`,
`AppConfig.pyroomacoustics`), `config/loader.py` (resolve the RPF path only when `raven` is
present), `config/backend_validation.py` (new), `config/validator.py` (calls
`validate_backend_config`; `raven.base_rpf_file` existence checked only under `raven`),
`pipeline/static_plan.py::normalized_static_config` (omits `render.backend: raven` and a
`None` `pyroomacoustics`).

Tests: `apps/orchestrator/tests/test_render_backend_config.py` → **60 passed**.
- 2.1: RAVEN entry = `{44100}`, `rpf.getT30`/`room`, no summary identity, no package;
  Pyroomacoustics entry = `{44100}`, `schroeder_backward_integration_v1`/`rir`,
  `Pyroomacoustics`, `{circular_array}`, `pyroomacoustics>=0.10.1,<0.11`; version-range
  parsing (plain releases only; pre-releases rejected).
- 2.2: valid model, `max_order_non_shoebox` defaults to `max_order`; missing/negative/float/bool
  orders, humidity out of [0,100], non-finite temperature and partial atmosphere rejected.
- 2.3: one test per scenario of "Backend-specific configuration blocks" and "Explicit rejection
  of unsupported backend features" (each error names the field and the backend); RAVEN config-time
  sample-rate behaviour unchanged (48000 still accepted at config time under RAVEN).
- 2.4: `importlib.metadata.version` monkeypatched: missing → error naming
  `pyroomacoustics>=0.10.1,<0.11`; `0.9.0` → error naming the version; `0.10.1` → OK; RAVEN
  validation never queries the package.
- 2.5: both golden normalized configs match exactly, with and without explicit
  `render.backend: raven`; Pyroomacoustics configs record `render.backend` and the
  `pyroomacoustics` block.
- Full orchestrator suite after these changes: **1062 passed** (995 baseline + 7 golden + 60).
- Expected, documented consequence (design D10): `config/validator.py` is hashed by
  `_generation_contract`, so plans published before this change report the existing
  "Generation contract changed" conflict on resume (demonstrated non-destructively in 6.7/10.2).

## 2.6 Optional dependency extra and lockfile (operator-authorized in-session)

- `apps/orchestrator/pyproject.toml`: new optional extra
  `pyroomacoustics = ["pyroomacoustics>=0.10.1,<0.11", "numpy>=2.0", "scipy>=1.17", "soundfile>=0.13"]`
  (pin as confirmed by 1.3).
- `uv lock` → resolved 131 packages (was 129); **added only `pyroomacoustics 0.10.1` and its
  dependency `cython 3.3.1`**; numpy 2.4.6, scipy 1.17.1, soundfile 0.13.1 reuse the existing
  locked versions. `git diff --stat uv.lock`: 50 insertions, 1 deletion.
- `uv sync --package acoustic-orchestrator --extra dev` (no extra) →
  `importlib.metadata.version("pyroomacoustics")` raises `PackageNotFoundError` (not installed);
  full orchestrator suite → **1062 passed** (= 995 baseline + 67 new, no failures).
- `uv sync --package acoustic-orchestrator --extra dev --extra pyroomacoustics` → installed
  pyroomacoustics 0.10.1, numpy 2.4.6, scipy 1.17.1, soundfile 0.13.1, cython 3.3.1 in the
  repo-local `.venv`.
- Note: subsequent commands use `uv run --package acoustic-orchestrator --extra dev --extra
  pyroomacoustics ...` so uv's sync keeps the extra installed.

## Artifact amendment A1 — coordinate transform origin (operator-authorized)

- Finding (before 3.1): `pyroomacoustics.ShoeBox` is anchored at the origin
  (`ShoeBox.is_inside`: `np.all(pos >= 0) and np.all(pos <= self.shoebox_dim)`), while the
  specified pure rotation `(X,Y,Z) = (x,-z,y)` maps every normalized public footprint
  (`z ∈ [0, W]`) to `Y ∈ [-W, 0]`. Reproduced with the installed 0.10.1: public `[1, 1.5, 2]`
  → backend `[1, -2, 1.5]` → `ShoeBox([4,3,2.7]).add_source(...)` raises
  `ValueError: The source must be added inside the room.`
- Options presented: (1) rotation + per-room translation, (2) extrude shoeboxes too (loses the
  lattice ISM), (3) a different rotation. Operator chose (1).
- Amended (minimal): `specs/pyroomacoustics-render-backend` requirement "Explicit reversible
  coordinate transform" (rotation followed by `t = (-min x, max z, 0)`, rigid, no vertical
  component, matrix and translation recorded) plus a new scenario "Room anchored at the backend
  origin"; design D6 bullet "Origin translation"; tasks 3.1 and 5.2 wording.
  `openspec validate add-pyroomacoustics-render-backend --strict` → valid.

## 3.1–3.5 Adapters: coordinates, geometry, materials, room, RIRs

New modules under `apps/orchestrator/src/acoustic_orchestrator/backends/pyroomacoustics/`:
`frames.py`, `errors.py`, `room_geometry.py`, `materials.py` (pure Python, no numpy or
Pyroomacoustics import) and `simulation.py` (the only module importing `pyroomacoustics`).

- `tests/test_pra_adapters.py` → **28 passed** (no package needed):
  - 3.1: determinant +1; 2000 random points × random translations round-trip ≤ 1e-9 m;
    `y = 1.5 → Z = 1.5`, floor `Z = 0`; public yaw +90° view `(cos, 0, −sin)` → backend `+Y`;
    normalized shoebox footprint and 200 random in-room points map into `[0,L]×[0,W]` (A1);
    metadata records rotation matrix and translation.
  - 3.2: shoebox, trapezoid, L-shape: backend area = public area within 1e-9 m², CCW; every
    backend wall's inverse-transformed edge midpoint equals the public `wall_id` edge midpoint
    within 1e-9 m; shoebox walls named west/east/south/north by position; unknown type, bad
    height, duplicate wall ids, clockwise or non-rectangular shoebox → `InvalidRenderInput`.
  - 3.3: index 17 = 0.12 → 1000 Hz = 0.12; ten bands; NaN, ±inf, −0.01, 1.01, bool, string and
    wrong length rejected (no clamping); per-surface `scattering_applied: false`.
- `tests/test_pra_simulation.py` [pra] → **7 passed**:
  - 3.4: walls: shoebox 6, trapezoid 6 (4 + floor + ceiling), L-shape 8; each backend wall's
    absorption/scattering equals its surface's converted octave values at the bank centers
    (atol 1e-6, float32 storage); extruded side wall *i* spans backend vertices *i → i+1*;
    `max_order` (shoebox) / `max_order_non_shoebox` (extruded), air absorption (8 coefficients),
    `temperature=25, humidity=50 → c = 347.02 m/s`, ray tracing and randomized ISM off.
  - **Low-band characterization (3.4):** Pyroomacoustics 0.10.1 at 44.1 kHz uses an 8-band
    bank centered 125 … 16000 Hz (first band keeps DC). Our ten explicit centers are linearly
    interpolated in log2(f) onto the bank; 125–16000 Hz pass through exactly, and the **31.5 Hz
    and 63 Hz coefficients have no effect** (set to 0.9/0.7 with 0.3 elsewhere → every backend band
    0.3). Content below ~88 Hz is governed by the 125 Hz coefficient. They are still recorded per
    surface in the sidecar.
  - 3.5: anechoic shoebox, 2 sources × 3 mics: one RIR per pair (`rirs[source][mic]`), single
    dominant peak at `round(d/c·fs) + global_delay ± 1` with > 99.9 % of the energy within
    ±40 samples; timing read at runtime: `frac_delay_length 81`, `global_delay 40`, HPF enabled
    10 Hz Butterworth n=2, `c = 343.0`; package seeded with the effective seed (negative seeds
    mapped deterministically with `& (2**64 − 1)`).

## 4.1–4.6 Signal synthesis, noise, WAV export and T30 (numpy/scipy/soundfile)

New: `backends/pyroomacoustics/synthesis.py`, `backends/pyroomacoustics/t30.py`.
`pipeline/render_validation.py::validate_t30` takes the variant's backend (part of 6.5, needed by
4.6). Tests: `tests/test_pra_synthesis.py` → **37 passed** (gated on scipy/soundfile, which ship
with the extra; `BINASCAPE_REQUIRE_PYROOMACOUSTICS=1` turns a skip into a failure).

- 4.1: `round_half_away_from_zero`: 22050.5 → 22051, −0.5 → −1, 2.5 → 3 (and −2.5 → −3,
  2.4999 → 2); offsets clamp at 0.
- 4.2: mono/stereo (arithmetic mean, `original_num_channels: 2`, `arithmetic_mean`), silent source
  unchanged, 48 kHz → 44.1 kHz by `resample_poly` (length and 1 kHz tone preserved), peak 0.99
  then `10^(gain/20)` (scaled inputs give identical outputs).
- 4.3: full convolution `len_s + len_rir − 1` (shorter channels zero-padded to the source's longest
  RIR); `trim_reverb_tail` drops `len_rir − 1`; two-source mix equals the sum of individual mixes
  within 1e-6 (no guard); 2.0 s → 88200 samples (trim and pad); guard scales the peak to 0.999.
- 4.4: pink layer at 10 dB on 8 channels → pooled SNR 10 ± 0.01 dB before the second guard,
  `mono_replicated`; pink slope ≈ −3 dB/octave; layer seeds `render_seed + i − 1` / explicit /
  `i`; two runs identical; top-level and per-layer metadata key sets equal the MATLAB sets
  (`apply_background_noise.m`, `prepare_noise_layer.m`, `scale_noise_to_snr.m`);
  `render_validation.validate_noise` accepts colored + audio-file (resampled, trimmed) layers and
  the disabled case; `silent_signal`, `silent_noise`, `all_layers_skipped` recorded; second guard
  recorded; 2-channel noise for an 8-channel array → `InvalidRenderInput` naming the mismatch.
- 4.5: `soundfile` WAV/PCM_16 with 3 and 8 channels (WAVE_FORMAT_EXTENSIBLE) accepted by
  `render_validation.validate_wav` with full decode; parents created; non-finite input refused.
- 4.6: deterministic synthetic decays (every octave center decays with exactly T60):
  T60 0.3/0.6/1.0 s within ±5 % (125 Hz–16 kHz) and ±10 % (31.5, 63 Hz); measured error
  ≤ 0.51 % in every band. Truncated decay (−20 dB) → all bands null, `status: unavailable`,
  `mean_t30_s: []`, gate reason `raven_t30_non_finite_or_non_positive`. Mean over pairs; any
  invalid pair nulls the band. Descriptor passes `validate_t30` for `pyroomacoustics` and is
  rejected (`method`/`estimated_from` inconsistent) under `raven`. 16 kHz upper edge clipped to
  0.95·Nyquist (20947.5 Hz).
- **Finding (recorded for 7.7):** single white-noise decays scatter strongly at low frequencies
  (20 seeds: max |error| 30–51 % at 31.5 Hz, 17–45 % at 63 Hz, up to 21 % at 125 Hz), while the
  20-seed mean bias stays within the tolerances (≤ 8.4 % at 31.5/63 Hz, ≤ 4.8 % elsewhere). This
  is statistical (few bandwidth·time degrees of freedom), not method bias; real RIRs in small
  rooms will show the same low-band variability. The ensemble-mean check is a test.
- Validity rule implemented for "decay does not reach −35 dB": the band energy envelope (moving
  mean over `max(10 ms, 2 periods)`) at the end of the RIR must be ≥ 35 dB below its peak, in
  addition to the Schroeder curve crossing −35 dB; otherwise the band is null.

## 5.1–5.4 Worker process, records and sidecar

New modules: `backends/pyroomacoustics/records.py` (completion/failure/event writers),
`job.py` (snapshot load + validation → `RenderJob`; every rejection is `InvalidRenderInput`),
`sidecar.py`, `worker.py` (`python -m acoustic_orchestrator.backends.pyroomacoustics.worker`).
`room_geometry.contains_backend_point` added (sources/mics must be strictly inside the room).
`pipeline/matlab_diagnostics.py`: `SIMULATION_STEPS` now includes `pyroomacoustics_rir` in both
`MatlabActivityReader.poll` and `MatlabDiagnostics.read_events` (needed for the 5.1 oracle; the
progress test is task 6.6).

`tests/test_pra_worker.py` → **16 passed** (30.5 s; subprocess tests need the extra):
- 5.1: completion written via `.tmp` + `os.replace` → `inspect_completion` = valid; existing
  completion path → `RecordExistsError`, original bytes untouched; failure report →
  `inspect_failure` = valid `invalid_input`; failure refuses unobserved bytes (hash mismatch);
  `EventWriter` events → `MatlabDiagnostics.read_events` (7 events, no diagnostic errors) and
  `collect_evidence` (last step `pyroomacoustics_rir`, entrypoint completed).
- 5.2: builder sidecar + 4.5 WAV (with and without pink noise) → `validate_render(canonical=True)`
  **valid** (metadata, sources, array, seed, noise and backend-aware T30 checks, no
  backend-specific exception other than the T30 descriptor and rate set); `summary.render_backend:
  Pyroomacoustics`; `backend` block with name, version, simulation (`method: ism`, orders,
  `air_absorption`, temperature, humidity, speed of sound, `ray_tracing: false`,
  `randomized_image_sources: false`, `seed_applied`), rotation matrix + translation, materials
  (`scattering_applied: false`), RIR timing, `t30_pairs_s`; legacy z-reflected source pose.
  Identity mismatch (summary says RAVEN, block missing, block says raven) →
  `render_metadata_backend_identity_mismatch`.
- 5.3 [pra]: success → exit 0, valid completion, 3-channel WAV with 22050 samples, event sequence
  exactly `initialization`, `simulation` (`pyroomacoustics_rir`, `audio_postprocessing`),
  `artifact_writing` with `artifact_written` wav then metadata, `entrypoint_completed`; snapshot
  without the array descriptor → non-zero exit + valid `invalid_input` report; unknown geometry and
  an absorption of 1.5 → `invalid_input` naming the problem; injected `RuntimeError` in
  `compute_rirs` → non-zero exit, no completion, no failure report; runtime manifest modified after
  the snapshot → the worker renders the snapshot (max_order 8 kept) and the completion carries the
  snapshot's SHA-256; pre-existing completion → non-zero exit, file untouched.
- 5.4 [pra]: two separate worker processes (two attempt directories, pink noise + air absorption)
  → equal WAV SHA-256 and JSON-equal sidecars.

## 6.1–6.7 Orchestrator integration

Code: `pipeline/matlab_runner.py` (shared `_prepare_process` + `_run_render_process`;
`render_manifest_with_matlab` keeps its signature and exact command; new
`render_manifest_with_pyroomacoustics`), `pipeline/render_pipeline.py` (single dispatch point in
`_run_single_render_task` by the reserved snapshot's `render.backend`, absent = RAVEN; both runners
stay module-level names), `experiment/scene_builder.py` (non-RAVEN manifests get `render.backend`,
`render.backend_parameters` and `base_rpf_file: null`; RAVEN unchanged), `pipeline/static_plan.py`
(`_references` skips a null RPF), `pipeline/render_validation.py` + `pipeline/output_index.py`
(backend-declared rate set and T30 descriptor; non-RAVEN identity check; RAVEN sidecars rejected
only if they claim another backend), `pipeline/matlab_diagnostics.py` (`pyroomacoustics_rir`).

- Full orchestrator suite after 6.1–6.4 (before the new tests): **1150 passed**. In particular the
  existing runner, diagnostics, retry, resilience and interruption tests pass unchanged
  (`test_matlab_diagnostics`, `test_render_retry_dispatch`, `test_render_resilience`,
  `test_interruption_recovery`, `test_render_diagnostics_integration`, `test_process_retries` →
  213 passed), and every existing RAVEN validation test and fixture gives the same outcome.
- `tests/test_pra_pipeline.py` → **16 passed**:
  - 6.1: the MATLAB command list is byte-identical to the 1.4 capture.
  - 6.2: worker command `[sys.executable, -m, acoustic_orchestrator.backends.pyroomacoustics.worker,
    <snapshot>, --attempt-id, --variant-id, --manifest-sha256, --completion-path, --failure-path,
    --events-path]`, `check=True`, child env `OMP_NUM_THREADS=1`, `PRA_NUM_THREADS=1`; attempt record and
    `invocation.json` store the command; `console.log` captured; exit 3 → `CalledProcessError`,
    `process_status: failed`, `exit_code: 3`, termination observed; `FileNotFoundError` →
    `launch_failed`.
  - 6.3: a RAVEN array run reaches only `render_manifest_with_matlab` (the Pyroomacoustics runner
    patched to fail); a Pyroomacoustics run reaches only `render_manifest_with_pyroomacoustics`.
  - 6.4: RAVEN scene/runtime manifests still equal the 1.4 goldens (both examples); a
    Pyroomacoustics scene and runtime manifest contain `render.backend`, the five effective
    parameters and `base_rpf_file: null`; the static plan fingerprints no RPF.
  - 6.5: Pyroomacoustics descriptor accepted; Pyroomacoustics variant declaring `rpf.getT30` and
    RAVEN variant declaring `schroeder_backward_integration_v1` → `render_metadata_t30_*_inconsistent`;
    RAVEN sidecar with `backend.name: pyroomacoustics` → identity mismatch; runtime rate 48000 →
    `runtime_sample_rate_invalid` for both backends; unknown runtime backend →
    `runtime_render_backend_invalid`.
  - 6.6: `MatlabActivityReader` and `TerminalProgress` show `simulation / pyroomacoustics_rir`.
  - 6.7: (a) a RAVEN-rendered run resumed with `render.backend: pyroomacoustics` → existing
    `StaticPlanConflict` ("Configuration, catalog or generation contract changed"), worker never
    launched, every file under the run root byte- and mtime-identical; (b) changed `max_order` →
    `equivalent_render_inputs` False and `should_resume_variant` False (True for the unchanged
    manifest); with an authorized regeneration the variant is re-rendered with `max_order: 5`;
    (c) unchanged Pyroomacoustics run → `matlab_invocations: 0`, `resumed_variants: 1`, worker never
    launched.
  - **Pre-upgrade plan (D10, spec "Run created before the upgrade"):** a RAVEN plan whose
    `config/validator.py` fingerprint predates this change → `StaticPlanConflict` "Generation
    contract changed; requires resume=false and overwrite=true"; no file under the run root
    changes (bytes + mtime), MATLAB not relaunched. Non-destructive by demonstration.

## 7.1–7.6 Acoustic and physical validation [pra][Linux]

`tests/test_pra_acoustics.py` → **11 passed** (Linux, no MATLAB). Measured values:
- 7.1: anechoic shoebox 6×5×3 m, source 2.0 m from one microphone, 44.1 kHz: peak index **297** =
  `round(2.0/343·44100) + 40` (recorded `c = 343.0`, global delay 40) — exact.
- 7.2: anechoic, 1.0 m vs 2.0 m, energy in ±40 samples around each peak: ratio **3.932** (−1.7 %,
  limit ±5 %).
- 7.3: 3- and 8-microphone arrays: rendered positions (`room.mic_array.R`, inverse-transformed)
  equal the descriptor within 1e-9 m; for every k, a source 1 m along microphone k's direction gives
  the earliest (sub-sample) arrival on microphone k; end-to-end worker WAV (3 ch): channel 2
  (microphone 1) has the earliest onset.
- 7.4: 4-mic array (r = 0.15 m), three source positions: cross-correlation lag between channels 1
  and 3 equals the geometric delay difference within ±1 sample.
- 7.5: shoebox 4×3.5×2.7 m, `max_order 40`, two mics: T30 with uniform absorption 0.1 =
  [0.637, 0.651, 0.726, 0.706, 0.598, 0.561, 0.568, 0.543, 0.561, 0.552] s; with 0.4 =
  [0.335, 0.361, 0.272, 0.21, 0.208, 0.199, 0.184, 0.197, 0.199, 0.202] s → lower in all ten
  bands. Two impulse sources at `start_time_s` 0.0 and 0.0123 → offsets 0 and 542 samples
  (half-away rounding of 542.43), each direct peak at `offset + rir_peak`.
- 7.6: yaw +90°, 4 mics: microphone 0 at public offset `[0, 0, +r]`; `fixed_position` source at
  azimuth 0° with receiver yaw +90° at `[0, 0, −1]` (public −Z). The test documents the opposite
  rotation direction as the known risk; no geometry is changed.

## 8.1–8.5 Retry, resume, recovery and diagnostics [Linux]

Code: `pipeline/interruption_recovery.py::active_render_writers` now also treats a process whose
command line runs `acoustic_orchestrator.backends.pyroomacoustics.worker` as an active renderer
(the MATLAB/RAVEN name check alone would miss a `python` worker; required so `reconcile-static`
guards govern worker invocations unchanged).

`tests/test_pra_resilience.py` → **11 passed** (process boundary = `matlab_runner.subprocess.run`
replaced by a fake worker that publishes with the real record writers; 8.2 runs the real worker):
- 8.1: injected crash (exit 1, no completion) → retried within `execution.matlab_process_retry`:
  2 invocations, `process_retries 1`, `failed_matlab_processes 1`, `variants_completed_after_retry 1`;
  one chain, two attempt ids, one `manifest_sha256` (same snapshot), same `render.seed`; first attempt
  `unknown_process_failure`/`scheduled`; circuit recorded both resolutions and reset to 0. With
  `max_consecutive_failures: 1` a single crash opens the persisted circuit
  (`open: true`, `consecutive_failures: 1`) and stops the batch.
- 8.2: real worker on a snapshot with stereo noise for a 3-microphone array → `failure.json`
  `invalid_input` ("channel mismatch"), classified `deterministic_failure` / `not_retryable`, one
  invocation; a restart launches nothing.
- 8.3: worker publishes a valid completion then exits 9 → `render-static` CLI exit 0, summary shows
  `recuperados=1` and the `ADVERTENCIA … código 9` warning; index record `completed`,
  `continue_with_warning`, `recovery_reason: post_completion`, `exit_code 9`; `evaluate_render`
  directly → `continue_with_warning`, `recovered`, `post_completion`.
- 8.4: interrupted worker attempt: `reconcile-static` inspects read-only (`restantes=2`, no file
  changed), `--apply` without `--confirm-stopped/--reason` refused with no change, full `--apply`
  records `exit_code=null termination_observed=false` and `process_status: interrupted` without
  launching a worker; the next `render-static` completes with `process_retries 1`. The process probe
  blocks a running `python -m acoustic_orchestrator.backends.pyroomacoustics.worker`.
- 8.5: worker attempts have `invocation.json`, `console.log`, `events.jsonl`; invocation records the
  worker command, `entrypoint_completed`, last step `pyroomacoustics_rir`, confirmed artifacts;
  diagnostics directory / events / summary write failures emit a `RuntimeWarning` and leave the
  summary and index decision identical to the baseline.
- `tests/test_interruption_recovery.py` (existing) unchanged and passing.

## 9.4 Output-analyzer characterization (no analyzer code changed)

Run directory: a real `render-static` run of the 9.1 example (fixture workspace, 1 scene,
Pyroomacoustics worker) in the session scratchpad; then
`uv run --package output-analyzer output-analyzer analyze <run_dir>/outputs/circular_array_pyroomacoustics`.
- Completes, exit code 0; writes `analysis/report.html` and all CSV/JSON/JSONL exports.
  Console: 1 scene, 1 render variant, 2 sources, 1 metadata incident (`missing_optional_index`:
  `clarity_index.jsonl`, expected since hearing degradation is disabled).
- `render_scenes`: `render_backend: "Pyroomacoustics"`, `output_type: circular_array`,
  `hrtf_id: null` — the backend identity is read and counted.
- Source positions come from the scene manifest (`position_provenance: "manifest public [x,y,z]"`);
  the sidecar fallback path (which treats non-`RAVEN + ITA` sidecars as an unknown frame) was not
  exercised.
- **Mislabeling (follow-up input):** the measured `schroeder_backward_integration_v1` values are
  reported in `rt30_records` as `method: "RAVEN rpf.getT30"` and compared against the Sabine
  estimate as `raven_s` in `rt30_comparisons` (7 bands); `report.html` contains the label
  "RAVEN rpf.getT30" 40 times and "Pyroomacoustics" twice. The analyzer does not read
  `summary.room.reverberation.method`/`estimated_from` nor the `backend` block.
- Observation: in this scene the measured T30 at 125 Hz (1.83 s) is about twice the Sabine estimate
  (0.93 s), consistent with the 7.7 finding on specular ISM in rooms with non-uniform absorption.

## 10.3 (partial) Clarity backend suite (operator-authorized install)

- `uv sync --package clarity-backend --inexact` into the repo-local `.venv` (adds torch, triton,
  clarity dependencies; removes nothing); `uv.lock` byte-identical before/after (`cmp`);
  pyroomacoustics 0.10.1 still installed.
- `.venv/bin/python -m pytest apps/clarity-backend/tests -p no:cacheprovider` → **21 passed**.

## 10.5 Scenario → evidence mapping

Test files are under `apps/orchestrator/tests/`. "§x.y" refers to sections of this file.

### render-backend-selection

| Scenario | Evidence |
|---|---|
| Existing configuration without a backend | `test_render_backend_config.py::test_existing_configuration_defaults_to_raven`; goldens `test_raven_golden.py`; full RAVEN suite unchanged (§10.1) |
| Explicit Pyroomacoustics selection | `test_render_backend_config.py::test_explicit_pyroomacoustics_selection_validates`; dispatch `test_pra_pipeline.py::test_pyroomacoustics_variants_reach_worker_runner_with_backend_manifests` |
| Unknown backend | `test_render_backend_config.py::test_unknown_backend_names_field_and_values` |
| Linux configuration without RAVEN | `test_render_backend_config.py::test_linux_configuration_without_raven_does_not_check_rpf`; `test_pra_e2e.py` |
| Conflicting backend blocks | `test_render_backend_config.py::test_conflicting_raven_block_rejected` (+ `test_pyroomacoustics_block_rejected_under_raven`) |
| RAVEN tuning under Pyroomacoustics | `test_render_backend_config.py::test_raven_tuning_fields_rejected_when_explicit` (6 fields) |
| Missing ISM order | `test_render_backend_config.py::test_missing_or_invalid_ism_order` |
| Partial atmospheric conditions | `test_render_backend_config.py::test_partial_atmospheric_conditions_in_config`, `test_partial_atmosphere_rejected` |
| Binaural output requested with Pyroomacoustics | `test_render_backend_config.py::test_head_related_outputs_rejected` |
| Directional source requested with Pyroomacoustics | `test_render_backend_config.py::test_directional_source_rejected` |
| Hearing degradation requested with Pyroomacoustics | `test_render_backend_config.py::test_hearing_degradation_rejected`; Clarity eligibility tests unchanged (§10.3) |
| Unsupported sample rate | `test_render_backend_config.py::test_unsupported_sample_rate_rejected` |
| Backend package unavailable | `test_render_backend_config.py::test_dependency_missing`, `test_dependency_too_old` |
| RAVEN render at 44100 Hz | existing `test_render_validation.py` outcomes unchanged (§6, §10.1); `test_raven_sample_rate_behaviour_unchanged` |
| Runtime manifest with an undeclared rate | `test_pra_pipeline.py::test_runtime_rate_outside_backend_set` (both backends) |
| Normalized configuration of a RAVEN experiment | `test_render_backend_config.py::test_normalized_config_identical_with_and_without_explicit_raven` |
| RAVEN runtime manifest content | `test_raven_golden.py::test_raven_scene_and_runtime_manifests_match_golden`; `test_pra_pipeline.py::test_raven_variants_reach_matlab_runner` |
| Run created before the upgrade | `test_pra_pipeline.py::test_pre_upgrade_raven_plan_reports_conflict_without_changes` |
| Pyroomacoustics runtime manifest | `test_pra_pipeline.py::test_pyroomacoustics_variants_reach_worker_runner_with_backend_manifests` |
| Metadata from a different backend | `test_pra_worker.py::test_backend_identity_mismatch_rejected`; `test_pra_pipeline.py::test_raven_variant_with_pra_method_or_identity_rejected` |
| Switching an existing run to Pyroomacoustics | `test_pra_pipeline.py::test_raven_run_resumed_as_pyroomacoustics_reuses_nothing_and_changes_nothing` |
| Changed ISM order | `test_pra_pipeline.py::test_changed_ism_order_is_not_equivalent_input` |
| No simplified replacement | `test_render_backend_config.py::test_head_related_outputs_rejected`, `test_directional_source_rejected`, `test_hearing_degradation_rejected`, `test_dynamic_scene_rejected` (§2.3: explicit rejection, no worker launched) |
| Dynamic scene requested with Pyroomacoustics | `test_render_backend_config.py::test_dynamic_scene_rejected` |

### pyroomacoustics-render-backend

| Scenario | Evidence |
|---|---|
| Successful worker attempt | `test_pra_worker.py::test_worker_success_publishes_valid_completion_and_events`; `test_pra_e2e.py` (`process_status: succeeded`) |
| Worker crash | `test_pra_worker.py::test_worker_injected_exception_exits_non_zero_without_completion`; `test_pra_resilience.py::test_worker_crash_is_retried_with_same_snapshot_and_seed` |
| Snapshot is the only input | `test_pra_worker.py::test_worker_renders_from_snapshot_only` |
| Invalid snapshot | `test_pra_worker.py::test_worker_invalid_snapshot_publishes_failure`; `test_pra_resilience.py::test_invalid_snapshot_is_deterministic_and_not_retried` |
| Retry budget shared with MATLAB semantics | `test_pra_resilience.py::test_worker_crash_is_retried_with_same_snapshot_and_seed`, `test_worker_crash_counts_toward_circuit_breaker` |
| Diagnostic event stream | `test_pra_worker.py::test_worker_success_publishes_valid_completion_and_events` (exact sequence) |
| Round trip | `test_pra_adapters.py::test_round_trip_property` |
| Room anchored at the backend origin (A1) | `test_pra_adapters.py::test_shoebox_anchored_at_backend_origin` |
| Vertical axis | `test_pra_adapters.py::test_vertical_axis_and_floor` |
| Public metadata unchanged | `test_pra_worker.py::test_builder_sidecar_and_wav_pass_canonical_validation` (`receiver.circular_array` equals runtime; `coordinate_frame` checked by `validate_render`) |
| Winding reversal | `test_pra_adapters.py::test_area_preserved_and_ccw` |
| Wall material identity | `test_pra_adapters.py::test_wall_identity_by_physical_edge`; `test_pra_simulation.py::test_wall_count_and_material_identity` |
| Unknown geometry type | `test_pra_adapters.py::test_invalid_geometry_rejected`; `test_pra_worker.py::test_worker_rejects_unknown_geometry_and_bad_coefficients` |
| Octave mapping | `test_pra_adapters.py::test_center_frequency_mapping` |
| Scattering under ISM | `test_pra_adapters.py::test_surface_conversion_reports_scattering_not_applied`; sidecar assertion in `test_builder_sidecar_and_wav_pass_canonical_validation` |
| Out-of-range coefficient | `test_pra_adapters.py::test_invalid_coefficients_rejected_not_clamped`; `test_pra_worker.py::test_worker_rejects_unknown_geometry_and_bad_coefficients` |
| Anechoic configuration | `test_pra_simulation.py::test_anechoic_rirs_have_single_dominant_peak_and_timing` |
| Ray tracing never enabled | `test_pra_simulation.py::test_parameters_applied`; sidecar `ray_tracing: false`, `randomized_image_sources: false` (`test_builder_sidecar…`) |
| Three-microphone array | `test_pra_acoustics.py::test_microphones_rendered_at_descriptor_positions_and_channel_order[3]`; `test_pra_e2e.py` (3 channels) |
| Channel ordering by arrival | `test_pra_acoustics.py::test_microphones_rendered_at_descriptor_positions_and_channel_order`, `test_wav_channel_order_end_to_end` |
| Half-sample rounding | `test_pra_synthesis.py::test_round_half_away_from_zero`, `test_offset_uses_matlab_rounding` |
| Stereo source | `test_pra_synthesis.py::test_stereo_source_arithmetic_mean`; e2e fixture has a stereo source (`test_pra_e2e.py`) |
| Linear mixing | `test_pra_synthesis.py::test_linear_mixing_two_sources` |
| Target duration | `test_pra_synthesis.py::test_target_duration_trims_or_pads`; e2e 88200 frames |
| Pink noise at 10 dB SNR | `test_pra_synthesis.py::test_pink_noise_at_10_db_pooled_snr` |
| Reproducible noise | `test_pra_synthesis.py::test_noise_reproducible_and_seeded_per_layer`; `test_pra_worker.py::test_two_worker_processes_are_byte_identical` |
| Incompatible noise channels | `test_pra_synthesis.py::test_incompatible_noise_channels_rejected`; `test_pra_resilience.py::test_invalid_snapshot_is_deterministic_and_not_retried` |
| Output format | `test_pra_synthesis.py::test_wav_writer_accepted_by_validator[8]`; `test_pra_worker.py::test_eight_channel_worker_output_with_gain_offset_duration` |
| Existing validator acceptance | `test_pra_worker.py::test_builder_sidecar_and_wav_pass_canonical_validation` |
| Backend block | same test + `test_worker_success…` (`backend.version == 0.10.1`) |
| Synthetic exponential decay | `test_pra_synthesis.py::test_synthetic_decay_tolerances`, `test_noise_decay_unbiased_on_average` |
| Truncated decay | `test_pra_synthesis.py::test_truncated_decay_is_null_and_unavailable` (gate reason); resampling policy unchanged (existing `test_render_retry_dispatch.py::test_t30_rejection_opens_new_chain…`, §7.7 runs) |
| More absorption, shorter decay | `test_pra_acoustics.py::test_more_absorption_shorter_decay` |
| Direct-path arrival | `test_pra_acoustics.py::test_direct_path_arrival_at_two_meters` |
| Inter-microphone delay | `test_pra_acoustics.py::test_inter_microphone_delay_by_cross_correlation` |
| Repeat render | `test_pra_worker.py::test_two_worker_processes_are_byte_identical` |
| RAVEN-only installation | §2.6 (suite without the extra: 1062 passed, package not installed); `test_pra_worker.py::test_raven_only_code_paths_never_import_pyroomacoustics`; `test_render_backend_config.py::test_raven_validation_never_queries_pyroomacoustics` |
| Characterized yaw behavior | `test_pra_acoustics.py::test_legacy_angular_convention_characterized` |
| Existing process configuration and evidence contracts | §6.1–6.7, §8.1–8.5: shared runner/attempt readers, unchanged retry keys and diagnostics layout; `test_pra_pipeline.py`, `test_pra_resilience.py` |
| Worker invocation evidence | `test_pra_resilience.py::test_worker_attempt_diagnostics_exist`; §6.2 and §8.5 record command, identity, timing, exit code and console evidence |
| Effective simulation parameters recorded | `test_pra_simulation.py::test_parameters_applied`; `test_pra_worker.py::test_builder_sidecar_and_wav_pass_canonical_validation`; §3.4–3.5, §5.2 |
| Synthesis stage ordering | §4.2–4.4: source preparation, full convolution/tail trim, offset and duration handling, first guard, noise and second guard; existing `test_pra_synthesis.py` stage tests and `test_pra_e2e.py` |
| Reverb-tail trimming | `test_pra_synthesis.py::test_full_convolution_lengths_and_tail_trim`; §4.3 |
| Noise raises the output peak | `test_pra_synthesis.py::test_second_guard_recorded`; §4.4 |
| Existing sidecar field inventory | `test_pra_worker.py::test_builder_sidecar_and_wav_pass_canonical_validation`; §5.2 lists the existing identity, seed, receiver, summary and output fields |
| Backend T30 descriptor | `test_pra_synthesis.py::test_descriptor_passes_backend_aware_gate_only_for_its_backend`; `test_pra_pipeline.py::test_pra_descriptor_accepted_and_cross_backend_method_rejected`; §4.6, §6.5 |
| Direct-path distance attenuation | `test_pra_acoustics.py::test_inverse_square_energy_ratio`; §7.2 records energy ratio 3.932 (limit 4 ±5%) |

### circular-microphone-array (modified)

| Scenario | Evidence |
|---|---|
| Eight-channel output | Pyroomacoustics: `test_pra_worker.py::test_eight_channel_worker_output_with_gain_offset_duration`; RAVEN: existing tests + successful real Windows/RAVEN E2E (§10.4 resumed 2026-11-05) |
| Unequal source distances | `test_pra_acoustics.py` 7.2 (level), 7.3/7.4 (arrival) — no per-channel normalization/alignment in `synthesis.render_source` |
| Same contract with either backend | `test_pra_pipeline.py` renders the same array config through both runners and validates both with the same `validate_render` contract; real Windows/RAVEN E2E passed (§10.4 resumed 2026-11-05) |

### static-render-acceptance (modified)

| Scenario | Evidence |
|---|---|
| Constancia de otro intento / Inventario incompleto o sustituido / Mensaje humano de éxito / Constancia interrumpida / Cabecera correcta y carga truncada / Dimensiones o valores no válidos / Duración objetivo / T30 inválido con constancia íntegra / T30 válido | existing `test_render_attempt.py`, `test_render_acceptance.py`, `test_render_validation.py` unchanged and passing (§10.1); the worker uses the same readers (`inspect_completion`, `validate_render`) |
| Constancia del worker Pyroomacoustics | `test_pra_worker.py::test_completion_accepted_by_render_attempt_reader`, `test_worker_success…` |
| Fallo exportando metadatos | `test_pra_worker.py::test_metadata_export_failure_publishes_no_completion` |
| Diagnóstico no disponible | `test_pra_resilience.py::test_diagnostic_failures_do_not_change_decision` (3 modes) |
| Constancia ya existente | `test_pra_worker.py::test_pre_existing_completion_refused`, `test_worker_refuses_existing_completion` |
| Tipo de receptor y frecuencia | `test_pra_synthesis.py::test_wav_writer_accepted_by_validator`; `test_pra_pipeline.py::test_runtime_rate_outside_backend_set` |
| Método T30 del backend Pyroomacoustics | `test_pra_synthesis.py::test_descriptor_passes_backend_aware_gate_only_for_its_backend`; `test_pra_pipeline.py::test_pra_descriptor_accepted_and_cross_backend_method_rejected` |
| Método T30 ajeno al backend | same two tests + `test_raven_variant_with_pra_method_or_identity_rejected` |

Unmapped scenarios: **none**.

## 7.7 ISM order calibration benchmark

Harness: `benchmarks/calibrate_ism_orders.py` (real pipeline: sampler, Sabine RT30 stratification
0.2–1.0 s, treatments, canonical T30 gate with resampling, `max_scene_attempts: 50`; real worker
subprocesses, 3 mics, 2 sources, air absorption on, `OMP_NUM_THREADS=1`, 10 workers on a 32-core
WSL2 host). Synthetic untreated material spectra (bricks/wood/plaster; the licensed RAVEN material
database is not in the repository). Raw results: `benchmarks/results_2026-11-03.json`. Plus two
single-pair probes (uniform absorption) for convergence and polyhedral cost.

**Shoebox (`circular_array_only` ranges), 10 scenes per order:**

| max_order | accepted | T30-exhausted | canonical attempts | rejection rate | valid-band rate | worker wall time median / max (s) |
|---|---|---|---|---|---|---|
| 30 | 9 | 1 | 26 | 65.4 % | 89.2 % | 4.8 / 7.1 |
| 50 | 10 | 0 | 14 | 28.6 % | 97.1 % | 7.2 / 7.6 |
| 80 | 10 | 0 | 10 | 0 % | 100 % | 12.0 / 12.6 |
| 100 | 10 | 0 | 10 | 0 % | 100 % | 17.5 / 19.2 |
| 120 | 10 | 0 | 10 | 0 % | 100 % | 27.4 / 41.6 |
| 150 | 10 | 0 | 10 | 0 % | 100 % | 87.6 / 126.5 |

Identical scenes for orders ≥ 80 (no rejections) show the mean-T30 convergence per scene (s):

| scene | 80 | 100 | 120 | 150 |
|---|---|---|---|---|
| 0001 / 0005 (short RT) | 0.338 / 0.361 | 0.338 / 0.361 | 0.338 / 0.361 | 0.338 / 0.361 |
| 0010 | 0.768 | 0.787 | 0.792 | 0.794 |
| 0006 / 0008 | 1.104 / 1.123 | 1.177 / 1.198 | 1.211 / 1.223 | 1.223 / 1.229 |
| 0004 | 1.307 | 1.434 | 1.498 | 1.525 |
| 0002 / 0003 / 0007 / 0009 | 1.651 / 1.339 / 1.626 / 1.546 | 1.975 / 1.615 / 1.957 / 1.871 | 2.229 / 1.847 / 2.218 / 2.125 | 2.508 / 2.093 / 2.515 / 2.415 |

Uniform-absorption probe (shoebox 4.35×3.8×2.7 m, reference order 120, error 125 Hz–16 kHz):
Sabine 0.20 s converged at order 20; 0.37 s ≤ 0.7 % at 40; 0.62 s 5.0 % at 50 / 2.0 % at 60;
1.04 s still 8.6 % at 80.

**L-shape (`static_example` ranges), extruded polygon, 10 scenes per order:**

| max_order_non_shoebox | accepted | T30-exhausted | canonical attempts | rejection rate | valid-band rate | wall median / max (s) |
|---|---|---|---|---|---|---|
| 6 | 0 | 10 | 113 | 100 % | 36 % | 6.3 / 31.7 |
| 8 | 0 | 10 | 113 | 100 % | 54 % | 6.0 / 8.1 |
| 10 | 1 | 9 | 79 | 98.7 % | 60 % | 11.9 / 15.4 |

Single-pair L-shape probe (absorption 0.15): order 10 → 0.87 s, RIR 0.165 s; order 12 → 12.2 s,
0.197 s; order 14 → **169 s**, 0.229 s, T30 0.24–0.37 s (truncation-biased; the converged shoebox
equivalent is ~0.6–1.0 s). Cost grows ~14× per two orders; order ≥ 16 was not completed after
> 30 min and was stopped.The single scene accepted at order 10 carries a truncation-biased T30 (RIRs ≈ 0.17 s).

**Findings (limitations, reported — the gate was not bypassed):**
1. Truncated ISM responses bias T30 low, and the "decays 35 dB" validity rule does not detect it
   (the order-limited image density thins out, so the tail still falls 35 dB). `max_order` must be
   chosen for convergence, not just for gate validity.
2. With non-uniform absorption (hard walls/ceiling, absorptive floor) the specular ISM, which
   ignores scattering, yields non-diffuse decays whose T30 keeps growing with order (scenes
   0002/0003/0007/0009: +12 % from 120 to 150) and reaches about 2.5× the Sabine target bin. These
   rooms do not converge at affordable orders; their measured T30 is an order-dependent lower bound.
   Datasets must use the measured T30 (and record `effective_max_order`), not the Sabine bin.
3. Extruded (trapezoid/L-shape) rooms cannot reach a T30-valid decay at affordable orders: 98.7–100 %
   rejection at orders 6–10 (29 of 30 scenes exhausted), minutes per pair at order 14. The backend rejects
   them explicitly through the gate; it does not degrade silently. Use shoebox rooms.

**Chosen example values (9.1) and rationale:** `max_order: 100` — zero T30 rejections, medium-RT
scenes within 1–6 % of order 150 (order 80: 9–14 %), median ≈ 18 s per variant single-threaded
(order 120 costs 27 s, order 150 88 s). `max_order_non_shoebox: 8` set explicitly so that adding a
non-shoebox shape cannot silently inherit an unaffordable order 100; the example is shoebox-only.
`air_absorption: true`.

## 9.1–9.3, 9.5 Example, Linux end-to-end, documentation

- 9.1: `configs/experiments/circular_array_pyroomacoustics.yml` (array-only, 3 mics, radius 0.05 m,
  shoebox, 2 sources, colored noise white/pink/brown at 20 dB, `max_order 100`,
  `max_order_non_shoebox 8`, no `raven` block) added to the `.gitignore` allowlist.
  `git check-ignore` → exit 1 (not ignored; control path exits 0).
  `test_pra_e2e.py::test_example_configuration_validates` (unchanged YAML in a fixture workspace
  mirroring the repository layout) and `test_example_is_tracked_not_ignored` pass.
- 9.2: `test_pra_e2e.py::test_render_static_end_to_end_without_matlab`: the
  `acoustic-orchestrator render-static` console script on an unchanged copy of the example
  (1 scene, fixture sources: 16 kHz mono and 44.1 kHz stereo) with
  `PATH = <venv>/bin:/usr/bin:/bin` (`shutil.which("matlab")` is None) → exit 0; index row
  `completed`, `validation.ok: true`; `inspect_completion` = valid; WAV 3 ch, 44100 Hz, PCM_16,
  88200 frames; sidecar `summary.render_backend: Pyroomacoustics`, backend block (`method: ism`),
  T30 `estimated`, noise applied; second run → `invocaciones=0`, no new attempt directory. Passes
  with the final `max_order: 100`.
- 9.3 (amended by A2): manual Linux validation commands documented in
  `docs/development/testing.md` (`uv sync --package acoustic-orchestrator --extra dev --extra
  pyroomacoustics`; orchestrator tests with `BINASCAPE_REQUIRE_PYROOMACOUSTICS=1`; output-analyzer
  tests; Clarity backend and MATLAB/RAVEN tests excluded: pytest does not collect `verify_*.py`).
  No CI workflow is part of this change. Local run before A2 (WSL2 Linux, CPython 3.12.12):
  orchestrator **1194 passed, 0 skipped, 0 failed** (167 s); analyzer **89 passed**. The re-run
  after A2 is recorded in the A2 section below.
- 9.5: `apps/orchestrator/README.md` (Requirements; new "Render Backends": selection, extra,
  `pyroomacoustics` block, rejections, Linux usage, worker contract, limitations),
  `docs/architecture/contracts.md` (new "Backend de render seleccionable": plan identity and the
  pre-upgrade conflict, manifest `render.backend`/`backend_parameters`/`base_rpf_file: null`,
  worker process, sidecar `backend` block, T30 methods, coordinate transform (A1), angular-convention
  risk), `docs/development/setup.md` (extra install, Linux usage), `docs/development/testing.md`
  (Linux commands, the require flag, goldens, Windows-only suites, BLOCKED rule). Every
  documented sidecar field (`effective_max_order`, `positions_m`, `coordinate_transform`,
  `lengths_samples`, `t30_pairs_s`, `seed_applied`, `scattering_applied`, `speed_of_sound_m_s`,
  `frac_delay_length`, `global_delay_samples`, `randomized_image_sources`) was grep-checked in
  `apps/orchestrator/src`; documented commands match the CLI (`--no-progress`);
  `test_degraded_output_docs_contract.py` still passes; `git diff --check` clean.

## 10.1–10.4 Regression and final acceptance

- 10.1 [Linux]: orchestrator **1194 passed** (baseline 995 + 199 new; 0 failures, 0 skips with
  `BINASCAPE_REQUIRE_PYROOMACOUSTICS=1`); output-analyzer **89 passed** (= baseline). No failure
  beyond the (empty) pre-existing baseline.
- 10.2 [Linux] RAVEN compatibility: in the same run, `test_raven_golden.py` (normalized configs,
  scene/runtime manifests of both examples, MATLAB command) and `test_pra_pipeline.py` golden/command
  checks pass; every pre-existing RAVEN validation/acceptance/index/resume test passes unchanged.
  Pre-upgrade plans: explicit "Generation contract changed" conflict, demonstrated non-destructive
  (§6.7).
- 10.3 [Linux] Clarity: `test_clarity_handoff.py`, `test_clarity_runner.py`,
  `test_render_pipeline_clarity.py` and the eligibility tests pass unchanged within the 1194; with
  the operator-authorized install, `apps/clarity-backend/tests` → **21 passed**.
- 10.4 [Windows/RAVEN]: **BLOCKED** — this session runs on WSL2 Linux without MATLAB, ITA Toolbox or a
  licensed RAVEN installation, so `runtests('apps/matlab/tests')`, `tests/verify_render_acceptance.py`
  and `tests/verify_circular_array_e2e.py` were **not executed**. `apps/matlab` is unchanged
  (`git status` shows no change under it). The operator must run them on Windows on this branch and
  acknowledge the result before archive.

## 10.5 Final checks

- Scenario mapping above: every scenario of the four spec deltas mapped; unmapped: none.
- `openspec validate add-pyroomacoustics-render-backend --strict` → valid (after amendment A1).
- Branch `feat/add-pyroomacoustics-render-backend`, HEAD = `develop` = `eb7cb92` (nothing committed).
- `git status`: the 49 pre-existing agent-tooling entries are byte-identical to the session start;
  `apps/matlab`, `apps/clarity-backend`, `apps/output-analyzer` unchanged. Every other change is in
  the scope listed by design.md/tasks.md: orchestrator `config/`, `experiment/scene_builder.py`,
  `pipeline/` (`render_backends`, `matlab_runner`, `render_pipeline`, `render_validation`,
  `output_index`, `static_plan`, `matlab_diagnostics` [6.6], `interruption_recovery` [8.4]), the new
  `backends/pyroomacoustics/` package, tests and goldens, `pyproject.toml` + `uv.lock` [2.6],
  `.gitignore` + the example config [9.1], docs [9.5],
  and this change directory (`verification.md`, `benchmarks/`).
- Pending: 10.4 (BLOCKED, operator acknowledgement required before archive).

## Artifact amendment A2 — CI/CD automation removed from scope (operator-authorized, 2026-11-04)

- Decision: GitHub Actions / CI/CD automation is not part of this change; it may be addressed by a
  separate future change. Linux testing and validation requirements are unchanged.
- Dependency analysis: the four spec deltas contain no CI reference (0 matches), so no normative
  requirement changed. Amended: `proposal.md` (What Changes, Impact → Systems), `design.md`
  (Non-Goals bullet, D14 "Linux validation" replaces the CI job, D15 adds CI/CD automation as
  deferred, Open Question on Windows CI removed), `tasks.md` (section 9 title, 9.3 rewritten to
  manual Linux commands, 9.5 wording), this file (9.3 entry, 9.5 note, 10.5 inventory),
  `docs/development/testing.md` (heading, manual-run sentence), and two comments in
  `apps/orchestrator/tests/pra_support.py` ("CI" → "strict mode"; no code change).
- Removed: `.github/workflows/linux-tests.yml`; `.github/` deleted because it became empty.
- Kept: every Python test, the MATLAB-free end-to-end test, all acoustic acceptance criteria and the
  `BINASCAPE_REQUIRE_PYROOMACOUSTICS=1` strict mode. Remaining "CI" mentions are intentional
  (non-goal/deferral statements, the historical fact "No CI exists", the proposal's "Why").
- `openspec validate add-pyroomacoustics-render-backend --strict` → valid.
- Re-run after A2 (WSL2 Linux, CPython 3.12.12, pyroomacoustics 0.10.1, 2026-11-04):
  `BINASCAPE_REQUIRE_PYROOMACOUSTICS=1 uv run --package acoustic-orchestrator --extra dev --extra
  pyroomacoustics pytest apps/orchestrator/tests -p no:cacheprovider` → **1194 passed, 0 skipped,
  0 failed** (121.8 s); `uv run --package output-analyzer pytest apps/output-analyzer/tests
  -p no:cacheprovider` → **89 passed**.
- Publication inventory: **60** files (18 modified, 42 untracked); the only difference from the
  previous 61 is the removed workflow. The 49 agent-tooling entries (42 `M`, 7 `??`) match the §1.1
  inventory and no file under `.agents/ .claude/ .opencode/ .pi/` was modified after the branch was
  created (2026-11-03T22:00:41-03:00). Nothing staged; HEAD = `develop` = `eb7cb92`.
- Task 10.4 remains **BLOCKED** and unchecked (Windows MATLAB/RAVEN regression pending the
  maintainer).

## 10.4 Windows/RAVEN — resumed 2026-11-05

This section records a new Windows session; it does not replace the historical Linux evidence.

- Checkout: `pr-32`, HEAD `63168173e1a10ae07cc03c8d13063a8cde975096`.
  Starting `git status --short`: only `?? .atl/` (pre-existing, left untouched).
- Existing `.venv/Scripts/python.exe`: CPython 3.12.12; imports the orchestrator from this
  checkout. No dependencies installed or lockfile changes made.
- `validate_config(load_config(...))` succeeds for both
  `configs/experiments/circular_array_only.yml` and `circular_array_mixed.yml`.
- MATLAB: R2025b Update 2 (`25.2.0.3055257`); `which('itaAudio')` and
  `which('itaRavenProject')` resolve under `D:/Tesis/toolbox-master/toolbox-master/`.
- Initial invocation from the repository root:
  `matlab -batch "results = runtests('apps/matlab/tests'); ...; assertSuccess(results);"`
  (also printed version, dependency paths, result table and counts) → exit 1,
  **18 passed, 112 failed, 106 incomplete, 130 total**. The application paths were not added;
  the log reports `MATLAB:UndefinedFunction` for helpers such as `generate_colored_noise`,
  `fit_noise_to_audio_shape` and `scale_noise_to_snr`. This is not a valid regression verdict.
  Full log: `C:/Users/sguaj/AppData/Local/Temp/opencode/pyroom-task10-4-matlab-20261105.log`.
  A rerun adds `addpath(genpath('apps/matlab'))`, matching production runner setup.
- Corrected invocation:
  `matlab -batch "addpath(genpath('apps/matlab')); results = runtests('apps/matlab/tests');
  disp(table(results)); fprintf('COUNTS passed=%d failed=%d incomplete=%d total=%d\n',
  sum([results.Passed]), sum([results.Failed]), sum([results.Incomplete]), numel(results));
  assertSuccess(results);"` → **exit 0; 130 passed, 0 failed, 0 incomplete**.
  Full log: `C:/Users/sguaj/AppData/Local/Temp/opencode/pyroom-task10-4-matlab-path-20261105.log`.
  Expected fault-injection diagnostics and built-in-shadowing warnings occurred without failing tests.
- `openspec validate add-pyroomacoustics-render-backend --strict` → **exit 1**, with
  **16 warnings** about ADDED requirement text exceeding 500 characters (13 in
  `pyroomacoustics-render-backend`, 3 in `render-backend-selection`). This differs from the
  historical successful validation above; requirements were not edited as part of task 10.4.
- Acceptance harness launched with the existing interpreter:
  `.venv/Scripts/python.exe apps/orchestrator/tests/verify_render_acceptance.py --output-root
  C:/Users/sguaj/AppData/Local/Temp/opencode/pyroom-task10-4-acceptance-20261105`.
  → **exit 0; both phases passed** against the installed RAVEN backend:
  - Normal: attempt `b26efcd2c8174300b62a2aa5318c0ffa`, `process_status: succeeded`,
    MATLAB exit 0, `render_validity: valid`, `decision: continue`.
  - Harness-only post-completion error: attempt `883066e6b2b44a1eb59e2e0f140fab2b`,
    `process_status: failed`, MATLAB exit 1, `render_validity: valid`,
    `decision: continue_with_warning`. The harness verified the completion inventory,
    acoustic acceptance, committed outcome and diagnostic failure location.
  Evidence: `C:/Users/sguaj/AppData/Local/Temp/opencode/pyroom-task10-4-acceptance-20261105/acceptance.json`.
- Circular-array E2E launched after acceptance completed:
  `.venv/Scripts/python.exe apps/orchestrator/tests/verify_circular_array_e2e.py render
  --output-root C:/Users/sguaj/AppData/Local/Temp/opencode/pyroom-task10-4-circular-20261105`.
  → **exit 1; partial pass, harness assertion failure**:
  - `PASS array_only: 1 rendered and resumed variants` (the harness checked the 8-channel,
    44100 Hz, 88200-frame WAV, metadata, T30 and resume).
  - Mixed case rendered **40/40 variants** across **10 scenes**, with 0 failed variants,
    0 failed MATLAB processes, 0 retries, and 40 MATLAB invocations. The harness then failed
    at line 112 because it expects exactly 4 completed variants for one scene.
    `configs/experiments/circular_array_mixed.yml` sets `execution.num_simulations: 10`,
    and `verify_render` does not override it. Mixed-case `_verify_outputs` and resume checks
    were therefore **not reached**; successful render counts alone do not certify them.
  - No `render_verification.json` was published (written only after both cases pass).
    Artifacts remain under the fresh output root above; mixed index:
    `mixed/metadata/indexes/render_index.jsonl`.
- **PAUSED:** task 10.4 remains unchecked (53/54 complete). MATLAB and acceptance smoke pass,
  but circular mixed-case acceptance does not. Operator guidance is required before adjusting
  the harness to its intended one-scene case and rerunning in a new output directory.
- Operator authorized the harness correction and fresh rerun. Added
  `config.execution.num_simulations = 1` in `verify_circular_array_e2e.py::verify_render`,
  so both cases match their existing one-scene assertions independently of example batch size.
  No acceptance assertion or example configuration changed.
- Authorized rerun:
  `.venv/Scripts/python.exe apps/orchestrator/tests/verify_circular_array_e2e.py render
  --output-root C:/Users/sguaj/AppData/Local/Temp/opencode/pyroom-task10-4-circular-single-scene-20261105`.
  → **exit 1**: array-only rendered and resumed successfully; mixed case reached
  `_verify_outputs` but failed its fixed 88200-frame assertion at line 48.
  The mixed example requests `source_sampling.timing.total_duration_s: 5.0` and
  `receiver_outputs.circular_array.num_microphones: 3`, while this harness expects
  2 seconds and 8 microphones. All four mixed output WAV headers report 44100 Hz and
  220500 frames; HRTF/HARTF outputs have 2 channels and the circular array has 3.
  These header observations are diagnostic evidence only, not a pass of the remaining
  acceptance checks. Mixed metadata checks and resume were not completed; the report
  was not published. Task 10.4 remains unchecked, pending operator authorization to
  explicitly set the harness's intended 2-second, 8-microphone case and rerun.
- Operator authorized both additional harness settings. `verify_render` now explicitly sets
  `source_sampling.timing.total_duration_s = 2.0` and
  `receiver_outputs.circular_array.num_microphones = 8`, alongside the authorized one-scene
  setting. Example configs and all acceptance assertions remain unchanged.
- Fresh rerun:
  `.venv/Scripts/python.exe apps/orchestrator/tests/verify_circular_array_e2e.py render
  --output-root C:/Users/sguaj/AppData/Local/Temp/opencode/pyroom-task10-4-circular-8mic-2s-20261105`.
  → **exit 0; PASS array_only (1 rendered and resumed variant), PASS mixed (4 rendered
  and resumed variants)**. Both cases passed all unchanged `_verify_outputs` assertions:
  8-channel array / 2-channel HRTF/HARTF, 44100 Hz, 88200 frames, metadata/source poses,
  ten finite positive T30 bands, microphone mapping, receiver IR selection and serialized
  RAVEN HRTF project selection. Resume reused every variant without launching MATLAB.
  Report: `C:/Users/sguaj/AppData/Local/Temp/opencode/pyroom-task10-4-circular-8mic-2s-20261105/render_verification.json`.
- **Final task 10.4 result: PASS.** MATLAB suite: 130/130; acceptance smoke: both phases;
  circular E2E: both cases including resume. Historical BLOCKED/PAUSED entries above describe
  earlier sessions/attempts and are superseded by this result. Clarity processing was not
  rerun in this Windows task (Linux evidence remains in §10.3).
- Session changes: the three authorized harness settings, this evidence file and the 10.4
  checkbox. No production code, example config, dependency or lockfile changes; `.atl/`
  left untouched. Strict OpenSpec validation still has the 16 warnings recorded above;
  completing tracked implementation tasks does not resolve that separate validation issue.

## Editorial revision A3 — focused ADDED requirements (operator-confirmed, 2026-11-05)

- Scope: only this evidence file and the existing `specs/pyroomacoustics-render-backend/spec.md`
  and `specs/render-backend-selection/spec.md`. The previously authorized harness edits and
  completed 10.4 checkbox remain unchanged. No implementation, dependency, example, proposal,
  design or MODIFIED requirement changed during this editorial revision.
- Split the 16 overlong ADDED descriptions into focused contracts, retaining original
  requirement headers and moving their other obligations into new ADDED blocks with scenarios.
  Normative physics, record formats, field inventories, ordering, formulae, tolerances, error
  handling, exclusions and compatibility remain in the specs, not relegated to design prose.
  The transform's origin rationale already exists in design D6; no design amendment is needed.
- Obligation-preservation review:

| Original requirement group | Obligations retained across the focused blocks |
|---|---|
| Isolated worker process | Out-of-process dispatch, exact output paths, immutable snapshot/context, publication-before-zero-exit, non-zero unhandled errors |
| Existing MATLAB-process obligations | Inherited record formats/names, invalid-input report, retry budget/circuit/chains/isolation/reconcile, events/progress, invocation diagnostics |
| Coordinate transform | Forward/inverse formulae, proper rigid rotation, per-room translation, distance/time preservation, recorded transform, unchanged public/legacy frames |
| Geometry and surfaces | Manifest geometry, shoebox/extrusion, unknown-type rejection, winding/area, physical wall identity, floor/ceiling surfaces |
| Material conversion | Ten center-frequency octave values from 31 bands, same scattering conversion, invalid-value rejection, per-surface provenance, no equivalence claim |
| ISM simulation | Geometry-specific orders, atmospheric/default physics, air absorption, disabled ray tracing/randomized ISM, effective parameter provenance |
| Circular receiver | Snapshot positions/order, one RIR per pair, channel mapping, no recomputed geometry or individual alignment/normalization/beamforming |
| Signal synthesis | Ordered preparation, downmix/provenance, normalization then gain, full convolution/tail trim, rounded offset, sum length, target trim/pad, first guard |
| Noise | Layer seeds and spectral shaping, loop/trim/channel fitting, pooled pre-noise SNR and silent skips, second guard/metadata, no backend noise facilities or MATLAB byte-parity claim |
| Sidecar | Existing field inventory/semantics, copied schema version, backend identity and all provenance fields |
| T30 | Octave filtering/Schroeder/fit, pair invalidation, mean aggregation, null bands, unchanged descriptor and derived-field semantics |
| Propagation | Recorded global delay/speed, direct arrival tolerance, relative delay tolerance and inverse-square energy ratio |
| Angular convention | Existing positions, opposite ring/yaw risk, characterization only, separately authorized correction |
| Backend configuration | Conditional RAVEN file, exclusive blocks, explicit RAVEN tuning rejection, ISM orders/default, boolean air absorption, paired finite atmosphere/humidity range |
| Unsupported features | Named pre-planning rejection without substitutes, all head-related outputs, source directivity, degradation/dynamic scenes, rate and package restrictions |
| RAVEN identity | Unchanged normalized config, byte-identical manifests, no new metadata requirement, explicit generator-contract conflict without silent reuse/overwrite |

- Checked proposal/design/tasks and both unedited MODIFIED deltas against the revision: no
  scope or implementation delta; original requirement headers remain available to references.
- Structural audit against `git show HEAD:<spec-path>`: all **60 original scenarios** in these
  two files preserved with identical names and WHEN/THEN text; **11 additional scenarios** map
  to existing tests or recorded evidence in §10.5 above. No scenario removed or weakened.
  Every requirement has a scenario. Description lengths: Pyroomacoustics **39 requirements,
  maximum 424 characters**; backend selection **17 requirements, maximum 458 characters**.
- `openspec validate add-pyroomacoustics-render-backend --strict` → **exit 0**,
  `Change 'add-pyroomacoustics-render-backend' is valid`, **no warnings**. This supersedes the
  earlier 16-warning result without erasing its historical record.
- Runtime tests were not rerun for this editorial-only revision; their prior results remain
  evidence for unchanged behavior, not new executions. Tracking remains **54/54 complete**.
- Final mapping audit: **90/90 scenarios mapped** across the four deltas (3 circular-array,
  47 Pyroomacoustics, 24 backend-selection, 16 static-acceptance); no unmapped scenarios.
  Both MODIFIED delta files match HEAD exactly. `git diff --check` reports no whitespace
  errors (only Git's local LF-to-CRLF notices); `openspec list --json` confirms 54/54 complete.

## Final Linux regression and sign-off (2026-11-05)

This section adds a final Linux run on the archived change; earlier entries are kept as historical
evidence and are not rewritten.

- Tested commit: `525ef981aaed27839d9b5548ac0d314e603c71b5` (branch
  `feat/add-pyroomacoustics-render-backend`, parent `63168173e1a10ae07cc03c8d13063a8cde975096`).
- Environment: WSL2 Linux 6.6.87.1 (x86_64), CPython 3.12.12, uv 0.9.5, OpenSpec CLI 1.13.0,
  existing repo-local `.venv` (no dependency installed or upgraded; `uv.lock` unchanged):
  pyroomacoustics 0.10.1, numpy 2.4.6, scipy 1.17.1, soundfile 0.13.1, cython 3.3.1,
  pydantic 2.13.4, pytest 9.0.3.
- `BINASCAPE_REQUIRE_PYROOMACOUSTICS=1 OMP_NUM_THREADS=1 uv run --package acoustic-orchestrator
  --extra dev --extra pyroomacoustics pytest apps/orchestrator/tests -p no:cacheprovider -q -rs`
  → **1194 passed, 0 failed, 0 skipped** (154.7 s).
- `uv run --package output-analyzer pytest apps/output-analyzer/tests -p no:cacheprovider -q`
  → **89 passed**.
- No application code changed after the maintainer's Windows validation:
  `git diff 6316817..525ef98` touches only `apps/orchestrator/tests/verify_circular_array_e2e.py`
  (the three authorized harness settings, not collected by pytest) and OpenSpec documents; no change
  under `apps/orchestrator/src`, `apps/matlab`, `apps/clarity-backend`, `apps/output-analyzer`,
  `configs`, `apps/orchestrator/pyproject.toml` or `uv.lock`. The Windows evidence for tested HEAD
  `6316817` therefore applies to the code at `525ef98`.
- Task 10.4 was completed by the maintainer (section "10.4 Windows/RAVEN — resumed 2026-11-05":
  MATLAB 130/130, acceptance smoke both phases, circular E2E both cases with resume). It supersedes
  the earlier BLOCKED and PAUSED records in this file, which remain as history. Operator approval
  of the PASS conclusion: 2026-11-05. The raw Windows logs referenced there are on the maintainer's
  machine and are not part of the repository; they were not re-examined here.
- Amendment A3 (operator-approved): requirement prose of the two ADDED deltas compared with the
  pre-A3 text at `6316817`. All 60 original scenarios are retained with identical WHEN/THEN text;
  every prohibition is retained (several reworded, e.g. "never inside the orchestrator's process",
  "without silent reuse or overwrite"); no numeric tolerance or formula was lost. The only
  reduction in explicitness: the sidecar requirement now names the existing fields by category
  ("identity and output fields, seed provenance, …") instead of listing them, anchored to "every
  field required by existing array render validation"; the validator and the unchanged scenario
  still enforce the full inventory. No contradiction found. The synchronized baseline
  `openspec/specs/{pyroomacoustics-render-backend,render-backend-selection}` requirements are
  identical to the archived deltas; A1 (rotation + per-room translation) and A2 (no CI/CD
  automation) remain in effect.
- `openspec validate --all --strict` → 10 passed, 0 failed.
- Known acoustic limitations recorded in §7.7 and §9.4 still apply unchanged (ISM non-convergence
  for long, non-diffuse rooms; extruded rooms rejected by the T30 gate at affordable orders;
  31.5/63 Hz absorption without independent effect; analyzer T30 labelling; legacy array angular
  convention).
