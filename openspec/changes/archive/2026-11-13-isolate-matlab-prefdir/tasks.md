# Tasks

## 1. Orchestrator: isolated MATLAB preferences

- [x] 1.1 Write RED tests in `apps/orchestrator/tests/` (new `test_matlab_prefdir_isolation.py`) with a fake `subprocess.run`: the MATLAB command receives `env["MATLAB_PREFDIR"]` pointing to an existing, empty directory outside the run root; an inherited `MATLAB_PREFDIR` is overridden; two concurrent attempts (threads) get distinct directories
- [x] 1.2 Add `isolated_matlab_env()` and the create/pass/cleanup lifecycle in `render_manifest_with_matlab` (`pipeline/matlab_runner.py`) per design decisions 3-4, leaving `_run_render_process` and the Pyroomacoustics path unchanged; observe GREEN
- [x] 1.3 RED→GREEN tests for cleanup: the directory is removed after exit 0, after a non-zero exit and after a launch failure (`OSError`); the attempt record has `matlab_prefdir.path` and `cleanup == "removed"`; process status, exit code and classification are unchanged
- [x] 1.4 RED→GREEN test for cleanup failure (monkeypatched `shutil.rmtree` raising): a `RuntimeWarning` is emitted, `cleanup == "failed"`, and the outcome equals the successful-cleanup case
- [x] 1.5 Regression: the Pyroomacoustics worker command/env is unchanged (existing tests plus one assertion that `MATLAB_PREFDIR` is not injected there); `load_render_history`, `reconcile-static` and `reset-circuit` accept outcomes carrying `matlab_prefdir` (run `test_interruption_recovery.py`, `test_circuit_reset.py`, `test_render_retry_dispatch.py`)

## 2. Diagnostics script

- [x] 2.1 Make `scripts/diagnostics/rt30_reproducibility.py` launch MATLAB with `isolated_matlab_env()` and remove the directory afterwards; check with `grep -rn '"-batch"'` that no other MATLAB launch remains outside `matlab_runner.py` and this script

## 3. Docs

- [x] 3.1 Document per-process MATLAB preference isolation (env var, location, cleanup, the `matlab_prefdir` attempt field, how to delete leftover `binascape-matlab-prefs-*` dirs) in `docs/architecture/contracts.md` and `apps/orchestrator/README.md`

## 4. Verification

- [x] 4.1 Run the full orchestrator suite (`uv run pytest apps/orchestrator/tests`); the only failures are the 5 known at base, reproduced on the base commit, and `openspec validate isolate-matlab-prefdir --strict` passes
- [x] 4.2 Operational check with real MATLAB in a scratch output directory (never an existing batch): render one `directivity_experiment102` variant via `rt30_reproducibility.py --repeats 1 --parallel 1` and compare T30 with the stored value within 1e-6 s; then `--parallel 3` and confirm no `matlabprefs.mat` error, the user's `matlabprefs.mat` bytes/mtime unchanged, no leftover `binascape-matlab-prefs-*` directory, and report per-attempt duration before vs after

## Verification evidence (2026-11-13)

- 4.2 `--parallel 1`: 3/3 variants exit 0 (35 s, 28 s, 28 s); T30 EQUAL to the original `directivity_experiment102` run and across variants (max rel diff 0.00%).
- 4.2 `--parallel 3`: 1/3 exit 0 with T30 EQUAL to the original; 2/3 failed with `Unable to communicate with required MathWorks services (error 5001)` (single MATLAB license, not caused by this change; tracked by `add-matlab-parpool-render`). No `matlabprefs.mat` error in any console.
- User `matlabprefs.mat` unchanged after both runs (292 bytes, same mtime, sha256 `f53103c7…01d8e1`); no leftover `binascape-matlab-prefs-*` directories.
- Duration: 28–35 s per attempt sequentially vs. median 55 s (min 14.6, max 94.6) over 3024 attempts of `directivity_experiment102`, which ran with several concurrent workers, so the comparison is indicative only.
