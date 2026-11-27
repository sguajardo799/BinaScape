# Design

## Context

See proposal.md - Why. Current parallel path in `pipeline/static_plan.py`:

- `_plan_scenes_in_parallel` keeps a dict of futures and, for each position in scene order, tops up submissions to `position + 2 * workers` and then blocks on `futures[position].result()`. Submission only advances when the oldest scene finishes.
- `inspect_static_plan.merge` runs in the parent, in scene order. For each planned manifest it calls `observe(dependency)` for every reference not yet in `preconditions`, which runs `_snapshot` (stat + full-file SHA-256) synchronously. Errors (`_SCENE_CONFLICTS`) become a conflict of the scene being merged; a failed dependency is not cached, so a later scene referencing it retries and reports its own conflict.
- `progress.start(scene_id)` is emitted at submission time; the parallel ETA uses the first `start` and the in-order `resolved` events.
- Existing tests pin: byte-identical publication vs sequential, worker capping, sequential fallback for unpicklable samplers, ordered-prefix interruption with no writes, and hash-once per inspection (sequential path).

## Goals / Non-Goals

**Goals:**
- Workers never wait for an earlier slow scene before receiving new work.
- Dependency hashing runs off the merge path and overlaps with sampling.
- Observable results stay identical to the current implementation (see specs/static-scene-planning).

**Non-Goals:**
- Changing the sequential path, `revalidate_static_plan`, sampler internals, or worker-count selection.
- Caching filesystem scans in the sampler or skipping the pre-publication rehash (separate follow-ups).
- Moving fingerprinting into worker processes.

## Decisions

### D1. Completion-driven dispatch with an in-order merge buffer

Rewrite `_plan_scenes_in_parallel` as a loop over `concurrent.futures.wait(..., return_when=FIRST_COMPLETED)`:

1. Keep at most `2 * workers` futures running (submitted, not done). Submit the next pending scene whenever a slot frees, emitting `progress.start` at submission as today.
2. Store each finished result in a buffer keyed by scene index.
3. After each wake-up, merge the contiguous prefix available in the buffer, starting at the next scene index to merge.

Alternatives considered:
- *Submit every scene up front*: simplest, but every scene would report `start` at once, which distorts the per-scene durations the progress view and ETA consume, and holds pickled inputs for the whole batch in the executor queue.
- *Larger fixed window*: reduces but does not remove head-of-line blocking; a slow enough scene still stalls dispatch.

The buffer of finished-but-unmerged results is bounded by the batch size; results are small manifest dicts, so this is acceptable.

### D2. Parent-owned fingerprint futures, started on completion

Add a `ThreadPoolExecutor` owned by the parallel path and a `dict[Path, Future[snapshot]]` shared with `merge`:

- When a scene result arrives (completion order), compute `_references(result.manifest)` for planned manifests and submit `_snapshot(path)` for every path that has no future yet and is not already in `preconditions`.
- `merge` keeps its current logic but obtains a dependency's snapshot through a helper: if a future exists, take `future.result()` (re-raising its exception inside the existing `_SCENE_CONFLICTS` handler); otherwise call `observe` as today. The sequential path passes no futures and behaves exactly as now.
- On success the snapshot is recorded in `preconditions` at merge time, in scene order, so `preconditions` insertion order and content match the sequential path.
- A failed future is kept; every scene referencing the dependency re-raises the same exception and reports its own conflict, matching today's per-scene conflict messages.

Thread pool size: `workers` threads (hashing is I/O bound and `hashlib` releases the GIL on large buffers). Recorded as an assumption; it can be tuned without changing specs.

Alternatives considered:
- *Hash inside worker processes*: shared dependencies (HRTF catalog files used by many scenes) would be hashed once per process instead of once per inspection, multiplying I/O and breaking the hash-once guarantee.
- *Hash at merge time in threads*: still blocks the merge on I/O; no overlap with sampling.

Note: this does not change conflict text. If a merge raises before reaching a dependency (for example a "Missing selected dependency fingerprint" check), an already-started fingerprint is simply discarded.

### D3. Interruption and shutdown

On any `BaseException` the path cancels pending futures of both executors (`shutdown(wait=False, cancel_futures=True)`) and re-raises, preserving "only the merged prefix is resolved" and no writes. On normal completion both executors are shut down with `wait=True`.

## Risks / Trade-offs

- [Async ordering bug changes plan bytes] → Merge and `preconditions` writes stay in scene order on the parent thread; the existing byte-identity test plus a new out-of-order test (slow first scene) are the acceptance gate.
- [Snapshot taken earlier than before] → Fingerprints may be computed slightly before the scene is merged. `revalidate_static_plan` re-checks every precondition before publication, so a file changed in between is still caught.
- [Thread and process pools compete for disk] → Bounded thread count; scene evaluation is CPU-bound sampling, so contention is limited.
- [Unmerged buffer grows when scene 0 is very slow] → Bounded by batch size and small per-scene payload.
- [Test flakiness from timing] → Tests force ordering deterministically (for example a sampler that blocks scene 0 on a cross-process event until the others finish) instead of relying on sleeps.

## Migration Plan

Internal change only; no config, CLI or artifact format changes. Rollback is reverting the commit.
