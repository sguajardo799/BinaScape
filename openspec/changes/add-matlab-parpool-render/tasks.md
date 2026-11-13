# Tasks

## 1. Feasibility spike (blocking)

- [x] 1.1 On the target machine, run a throwaway MATLAB script outside the repo that opens `parpool('Processes', 4)` from one `matlab -batch` session and renders one real RAVEN variant inside a worker with `run_raven_static_render`. Verify: no MathWorks licensing error (5001 or similar) while 4 workers are alive; the WAV and metadata match the same variant rendered in `process` mode (same T30 within the analyzer tolerance). Record the result in the change; if licensing or RAVEN fails inside workers, stop and revise this change
- [x] 1.2 In the same spike, confirm that serializing the first `ita_preferences` access with `spmd` + `spmdBarrier` under a private `MATLAB_PREFDIR` lets 4 workers render concurrently without `matlabprefs.mat` read errors (run 8 variants)

### Spike evidence (2026-11-13)

- One `matlab -batch` session (private `MATLAB_PREFDIR`) opened `parpool('Processes', 3)` in 38.7 s; the client held only `matlab` + `distrib_computing_toolbox` licenses and no worker hit a licensing error. Pools of 2 and 3 workers start reliably.
- `parpool('Processes', 4)` fails on this machine (with and without the repo `addpath`): the 4th worker exits with status 1 during startup. Likely memory (13.9 GB total, 4.9 GB free, ~1.8 GB per MATLAB process); not proven. Practical cap here: `num_workers: 3`.
- Serialized `ita_preferences('AuthorStr')` per worker via `spmd`/`spmdBarrier`; 9 real variants (scenes 0001-0003 x 3 receivers of `directivity_experiment102`) rendered with `parfeval(@run_raven_static_render, ...)`: 9/9 OK in 103 s total (sequential ~30 s each), no `matlabprefs.mat` error, every T30 EQUAL to the original run (max rel diff 2.5e-7 < 1e-6).
- `Future.Diary` captured each job's console (6 `[INFO]` lines per job). `Future.Worker.ProcessId` was not available after completion: the worker id must be reported by the job itself (e.g. `getCurrentWorker` inside the task wrapper).
- User `matlabprefs.mat` unchanged (sha256 prefix `f53103c7c77cb8a`, same mtime); no leftover `binascape-matlab-prefs-*` directory.

## 2. Orchestrator: configuration

- [x] 2.1 RED→GREEN tests in `apps/orchestrator/tests/test_render_backend_config.py` (or a new `test_matlab_runner_config.py`): `execution.matlab_runner` defaults to `process`; `threads` is rejected naming the field and allowed values; `pool` with `render.backend: pyroomacoustics` is rejected before planning; `execution.matlab_pool.startup_timeout_s` / `heartbeat_timeout_s` default and reject non-positive values
- [x] 2.2 Add `matlab_runner` and the optional `matlab_pool` block to `ExecutionConfig` (`config/models.py`) and the semantic validation; add a test that the static plan identity (`static_plan.py` normalized config) is byte-identical with and without `matlab_runner: pool`

## 3. MATLAB: pool server

- [x] 3.1 Add `apps/matlab/run_raven_pool_server.m`: opens the pool, runs `addpath` on all workers, serializes the first ITA preferences access, writes `ready.json`, serves `requests/` with `parfeval(@run_raven_static_render, ...)`, publishes `results/<attempt_id>.json` atomically (exit code, error message, worker id, timestamps), writes each future's `Diary` to the attempt console path given in the request, refreshes `heartbeat.json`, exits on `stop` or when the parent PID is gone, and deletes the pool
- [x] 3.2 MATLAB tests in `apps/matlab/tests/` (new `test_raven_pool_server.m`) with a stub render function: a successful job yields `exit_code` 0; a throwing job yields non-zero and its message; `stop` ends the server; results are written atomically (no partial JSON visible)

## 4. Orchestrator: pool session and runner

- [x] 4.1 RED tests (new `apps/orchestrator/tests/test_matlab_pool_runner.py`) with a fake session process that serves the file queue from Python: success → `succeeded`/`exit_code 0`/`termination_observed`; job error → `failed` with `CalledProcessError`; session exit with in-flight jobs → each resolved `failed` with the session exit code (or 1); session exits before `ready.json` → the waiting attempt fails with the session console copied into its `console.log`; heartbeat stale → session killed and in-flight attempts failed; request publish `OSError` → `launch_failed`
- [x] 4.2 Split `_run_render_process` (`pipeline/matlab_runner.py`) into the shared shell plus an execution callable, leaving `process` mode and the Pyroomacoustics path byte-for-byte equivalent (existing `test_matlab_prefdir_isolation.py`, `test_pra_worker.py`, `test_render_diagnostics_integration.py` stay green)
- [x] 4.3 Implement `pipeline/matlab_pool.py` (`MatlabPoolSession`: lazy start under a lock, private prefdir via `isolated_matlab_env`, `invocation.json`/`console.log` under `metadata/matlab_pool/<session_id>/`, restart when dead, graceful stop then kill) and `render_manifest_with_matlab_pool`; record `matlab_runner`, `pool_session_id` and `pool_worker_id` in the attempt record; make 4.1 GREEN
- [x] 4.4 Wire the session into `render_pipeline.py`: create it in `_render_static_scenes_locked` only for `pool` mode with pending RAVEN variants, route `_run_single_render_task` to the pool runner, close it in `finally` (including circuit-open and error exits). RED→GREEN in `test_render_retry_dispatch.py` / `test_parallel_canonical_render.py`: with a fake session, at most `num_workers` jobs in flight, retries and circuit behave as in `process` mode, the session is stopped on every exit path
- [x] 4.5 Regression: `load_render_history`, `resume_evidence`, `reconcile-static`, `reset-circuit` and interruption recovery accept outcomes produced in `pool` mode (run `test_render_history.py`, `test_static_resume_safety.py`, `test_circuit_reset.py`, `test_interruption_recovery.py`); add one test that a run started in `process` mode resumes accepted variants in `pool` mode

## 5. Docs

- [x] 5.1 Document `execution.matlab_runner`, `execution.matlab_pool`, the session directory layout, the per-attempt pool fields, the failure mapping and the single-license motivation in `docs/architecture/contracts.md` and `apps/orchestrator/README.md` (Static Render, diagnostics, retries sections); add a commented `matlab_runner: pool` example to `configs/experiments/speech_directivity_experiment.yml`

## 6. End-to-end verification

- [x] 6.1 Run the full orchestrator test suite and the MATLAB test suite; both green
- [x] 6.2 Render a small real batch (e.g. 8 variants, `num_workers: 4`, `matlab_runner: pool`) on the target machine: exactly one `matlab -batch` process, no licensing error, all variants accepted, per-attempt `console.log` and `outcome.json` carry pool session/worker ids, and resuming the run renders nothing new

### Verification log (2026-11-13)

- 6.1: orchestrator suite 1302 passed, 5 failed; the same 5 fail on the base commit (`test_pra_e2e::test_render_static_end_to_end_without_matlab`, 2x `test_raven_golden::test_normalized_config_matches_golden`, 2x `test_render_backend_config::test_normalized_config_identical_with_and_without_explicit_raven`). MATLAB suite 148 passed, 0 failed.
- 6.2 attempt 1: first real run (3 scenes, `num_workers: 3`, `matlab_runner: pool`, scratch output dir) was stopped by Claude Code after ~95 s because the system ran critically low on memory, while the session was opening its 3-worker pool (`[POOL] opening pool with 3 worker(s)`; no `ready.json`, no results). No MATLAB process was left running; the orphan private prefdir was removed by hand.
- 6.2 attempt 2: blocked before MATLAB started by the `static_writer.lock` the killed attempt left in its output dir (`StaticPlanConflict`); the scratch output dir was deleted.
- 6.2 attempt 3 (pass, 5.5 GB free at launch): same config, run with `num_workers: 3` instead of the task's 4 because a 4-worker pool cannot start on this machine (see 1.2). Exit code 0; 9/9 variants accepted (3 canonical in 78 s including pool startup, 6 others in 22 s), 0 retries, 0 failed processes. One pool session (`7bdba366...`, ready 61 s after launch, `exit_code` 0, `prefdir_cleanup` removed). All 9 attempt records have `matlab_runner: pool`, that session id, and 3 distinct `pool_worker_id` values (3 jobs each). No licensing error, no MATLAB process left, no leftover prefdir. A resume of the same config rendered nothing (`reanudadas=9`, `Proceso invocaciones=0`) and opened no new pool session.
