# Proposal

## Why

Static scene planning (`inspect_static_plan`) already runs scenes in worker processes, but two parent-side bottlenecks keep the workers idle. Scenes are submitted in a window of `2 * workers` that only moves forward when the oldest scene is merged, so one slow scene (many rejected sampling attempts) stops new scenes from being dispatched. The parent also computes full-file SHA-256 fingerprints of every newly seen dependency (source WAVs, HRTF/HARTF files, materials) synchronously inside the in-order merge, which further delays both merging and dispatch. Large batches therefore scale worse with `execution.num_workers` than the sampling work itself allows.

## What Changes

- Replace the merge-driven submission window in the parallel planning path with completion-driven dispatch: a new scene is submitted whenever a worker result arrives, independent of which scene is next to merge. The number of scenes in worker processes stays bounded.
- Fingerprint dependencies on a parent-owned thread pool as soon as a scene result arrives (in completion order), deduplicated by path, so hashing overlaps with sampling instead of running inside the merge.
- Keep merging strictly in scene order and keep all observable results unchanged: published manifests, `static_plan.json`, `sampling_failures.jsonl`, conflict reporting, preconditions, and progress outcomes are identical to the sequential path.
- Keep the existing interruption behavior: an interrupt resolves only the already merged prefix, cancels pending work, and writes nothing.
- The sequential path (`num_workers == 1`, or an unpicklable sampler) stays unchanged.
- No configuration, CLI, manifest, or plan format changes.

## Capabilities

### New Capabilities
- `static-scene-planning`: how the orchestrator evaluates the scenes of a static batch during preflight inspection, including parallel evaluation, dispatch, dependency fingerprinting, deterministic in-order merge, and interruption. Existing implemented behavior (worker capping, sequential fallback, byte-identical results, hash-once, ordered-prefix interruption) is recorded together with the new dispatch and fingerprinting requirements.

### Modified Capabilities
<!-- None: `static-render-resumption` and `pipeline-progress` requirements are preserved unchanged. -->

## Impact

- Code: `apps/orchestrator/src/acoustic_orchestrator/pipeline/static_plan.py` (`_plan_scenes_in_parallel`, the dependency-fingerprinting part of `inspect_static_plan`'s `merge`).
- Tests: `apps/orchestrator/tests/test_static_plan.py` (new tests for out-of-order completion and overlapped fingerprinting; existing parallel tests must keep passing).
- No new dependencies (standard library `concurrent.futures` only).
- Risk: concurrency (async ordering between workers, hash threads and the in-order merge). Determinism of the published plan is the acceptance gate.
