# Design

## Context

See proposal.md - Why. Relevant current state:

- `source_audio.list_source_wavs(directory)` iterates with `Path.iterdir()`, calls `Path.is_file()`/`is_dir()` (one `stat` per entry on Windows), and returns `sorted(files, key=lambda p: p.relative_to(directory))`. Callers: `sampler._list_files` (via `_build_audio_pools`, once per receiver attempt), `static_plan.static_catalog` (inspection start and `revalidate_static_plan`), `config/validator.py` (config validation), and tests.
- `inspect_static_plan` calls `static_catalog` once and evaluates scenes sequentially in-process or in a `ProcessPoolExecutor` created by `_plan_scenes_in_parallel` for that single inspection. `revalidate_static_plan` (inside `apply_static_plan`, after inspection returns) recomputes `static_catalog` and rejects publication if it changed.
- `render_pipeline` also calls `sample_static_scene` outside inspection for RAVEN T30 resampling.
- `PurePath` ordering on Windows compares the lowercased path split into parts; on POSIX it compares case-sensitive parts.

## Goals / Non-Goals

**Goals:**
- One listing per `audio_dir` per process per inspection.
- A listing implementation that is at least an order of magnitude faster and returns an identical list.

**Non-Goals:**
- Caching material, receiver IR or background-noise scans (not significant in the profile).
- Caching for render-time T30 resampling or config validation.
- Changing discovery rules or the `list_source_wavs` signature.

## Decisions

### D1. Inspection-scoped cache in `source_audio`

Add a module-level cache in `source_audio.py` that is disabled by default, plus a context manager (for example `source_listing_cache()`) that enables it with an empty dict and disables and clears it on exit. While enabled, `list_source_wavs` stores the listing per directory and returns a new list copy on each call (callers keep their own mutable lists).

- `inspect_static_plan` runs its body inside the context manager, so `static_catalog` and sequential sampling share the cache in the parent.
- `_plan_scenes_in_parallel` creates its `ProcessPoolExecutor` with an `initializer` that enables the cache in each worker. The pool lives only for that inspection, so the cache dies with it.
- `revalidate_static_plan` runs after `inspect_static_plan` returns, with the cache disabled, so it lists afresh and still detects catalog changes before publication.

Because `static_catalog` is computed inside the context before any scene is sampled, the catalog recorded in the plan contract and the listings used for sampling in the parent are the same snapshot. Workers take their own snapshot on first use; a file changed during inspection is caught by the pre-publication catalog check, as today.

Alternatives considered:
- *`functools.lru_cache` on the function*: never invalidates; long-lived processes (tests, render resampling, a later inspection in the same process) would see stale listings.
- *Pass the catalog from `static_catalog` into the sampler*: avoids a cache but changes sampler signatures and the picklable inputs of every scene task, and the catalog stores resolved posix strings, not the `Path` objects the sampler uses.
- *Cache keyed by directory mtime*: on Windows a directory's mtime does not change when files inside identity subfolders change, so it would miss updates.

### D2. Faster listing with identical order

Reimplement `list_source_wavs` with `os.scandir` (its `DirEntry.is_file()`/`is_dir()` use type information from the directory read, no extra `stat` on Windows) and sort by a precomputed key `relative_path.lower().split(os.sep)` on case-insensitive platforms (case-sensitive parts on POSIX), which is exactly how `PurePath` compares paths. `os.path.normcase` is not used: on Windows (CPython 3.12+) it lowercases through `LCMapStringEx`, which differs from `str.lower()` for about 420 non-ASCII code points (for example `İ`, `ẞ`), so it would reorder such names; this was found by the independent verifier and pinned by a unit test. Build result `Path` objects from `directory` and the relative parts. Benchmark on the real catalog: 2.15 s → 0.09 s, identical result.

Alternative considered: keep `Path.iterdir()` and only change the sort key. That removes the `relative_to` cost but keeps one `stat` per file.

## Risks / Trade-offs

- [Ordering differs on an edge case (case, separators, symlinks)] → Unit test with mixed-case names and identity folders compares against `sorted()` of `Path` objects; differential check on the real 14k catalog; byte-identical publish against the base commit.
- [`DirEntry.is_file()` follows symlinks differently from `Path.is_file()`] → Both follow symlinks by default; keep the default `follow_symlinks=True`.
- [Cache left enabled after an exception] → The context manager disables it in `finally`.
- [Workers' snapshots differ from the parent's if files change mid-inspection] → Same exposure as today; caught by the pre-publication catalog check.

## Migration Plan

`_generation_contract()` fingerprints `source_audio.py`, so this change alters the generation contract (observed: `source_audio.py` hash `5cd87277…` → `ffa58c05…`; manifests and failure journal byte-identical, only `static_plan.json` differs). Existing batches must be regenerated with `resume_if_possible: false` and `overwrite_existing: true`; inspection reports this explicitly. Alternatives rejected by the user: an explicit equivalence entry for the previous fingerprint, and moving the code to a non-fingerprinted module (which would bypass the guard). Rollback is reverting the commit (which restores the previous fingerprint). Expected effect: planning time for `speech_directivity_experiment.yml` drops from about 9 s per scene to a small fraction of that; re-run the profile after implementation to confirm.

Measured on `configs/experiments/speech_directivity_experiment.yml`, 40 scenes, scratch artifact root, no profiler (2026-11-19):

| Run | Before | After |
|---|---|---|
| 1 worker, total | 1362 s (under cProfile) | 18.2 s (catalog stage 6.1 s, planning 12.1 s) |
| 3 workers, total | 362.9 s | 11.7 s (catalog stage 5.6 s, planning 6.1 s) |

The remaining fixed cost is the catalog stage (about 6 s per inspection, `static_catalog` and the generation contract); it is a candidate for a later change.

