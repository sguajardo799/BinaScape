# Tasks

## 1. Orchestrator: completion-driven dispatch

- [x] 1.1 Add a RED test in `apps/orchestrator/tests/test_static_plan.py`: an 8-scene batch with 2 workers and a picklable module-level sampler whose scene 0 blocks on a cross-process signal (for example a marker file in `tmp_path` that the other scenes' completions produce) until scenes 1-7 have been evaluated; assert inspection completes (with a bounded timeout), resolved progress events are in scene order 0-7, and the plan equals the sequential plan. Verify it fails (deadlocks/times out) against the current window
- [x] 1.2 Add a test that wraps `ProcessPoolExecutor.submit` via the existing `SpyPool` to record running-but-unfinished submissions and asserts the count never exceeds `2 * workers`; verify it passes before and after the change
- [x] 1.3 Rewrite `_plan_scenes_in_parallel` in `apps/orchestrator/src/acoustic_orchestrator/pipeline/static_plan.py` per design D1 (`wait(FIRST_COMPLETED)`, at most `2 * workers` running, in-order merge of the buffered contiguous prefix, `progress.start` at submission); verify 1.1 and 1.2 pass and the existing `test_parallel_planning_publishes_same_bytes_as_sequential`, `test_planning_workers_are_capped_by_scene_count_and_cpus`, `test_unpicklable_sampler_falls_back_to_sequential_planning` and `test_parallel_interrupt_resolves_only_the_ordered_prefix` still pass

## 2. Orchestrator: overlapped dependency fingerprinting

- [x] 2.1 Add a RED test for parallel hash-once: with `num_workers: 3` and a fixture that shares dependencies across scenes, count `_snapshot`/`_file_hash` calls made in the parent and assert each dependency path is fingerprinted exactly once and that `plan.preconditions` equals the sequential plan's preconditions (same keys, values and insertion order)
- [x] 2.2 Add a RED test that a dependency becoming unreadable (patched `_snapshot` raising `OSError` for one path referenced only by scene 2) in parallel inspection raises `StaticPlanConflict` naming scene 2's manifest path, writes nothing, and that a path referenced by two scenes yields one conflict per referencing scene, matching the sequential path's conflict list
- [x] 2.3 Implement design D2 in `static_plan.py`: a `ThreadPoolExecutor` with `workers` threads owned by the parallel path, fingerprint futures started when a scene result arrives, and a merge-side helper that uses the future when present and `observe` otherwise (sequential path unchanged); add design D3 shutdown/cancellation for both executors; verify 2.1, 2.2 and all group 1 tests pass
- [x] 2.4 Update the parallel planning paragraph in `apps/orchestrator/README.md` (around "The same setting also parallelizes static scene planning") to state that idle workers take the next pending scene without waiting for slower earlier scenes and that dependency fingerprints are computed concurrently, once per inspection; verify by reading the paragraph back

## 3. Integration verification

- [x] 3.1 Run the full orchestrator test suite (`uv run pytest` in `apps/orchestrator`) and verify it passes with no new failures (1328 passed; 5 failures pre-existing on base 3d928df: test_pra_e2e end-to-end and 4 normalized-config golden tests)
- [x] 3.2 Differential check: publish the same fixture batch with `num_workers: 1` and `num_workers: 3` on the base commit (scratch copy under `mktemp -d`) and on the change, and verify all published files are byte-identical across the four runs
- [x] 3.3 Run `openspec validate static-plan-completion-dispatch --strict` and verify it passes

## Workflow follow-up

- Archive the change and sync `static-scene-planning` into `openspec/specs/` after review.
