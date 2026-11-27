# Tasks

## 1. Orchestrator: faster source listing with identical order

- [x] 1.1 Add a test in `apps/orchestrator/tests/test_sampler_audio.py` that builds root and identity-folder WAVs with mixed-case names, sidecars and a too-deep WAV, and asserts `list_source_wavs` equals `sorted()` of the expected `Path` objects and returns a fresh list on each call; verify it passes on the current implementation (regression guard)
- [x] 1.2 Reimplement `list_source_wavs` in `apps/orchestrator/src/acoustic_orchestrator/source_audio.py` with `os.scandir` and the normalized relative-path sort key (design D2); verify 1.1 and the existing tests in `test_sampler_audio.py` and `test_validator.py` pass

## 2. Orchestrator: inspection-scoped listing cache

- [x] 2.1 Add a RED test in `tests/test_static_plan.py` that counts directory scans of the speech `audio_dir` (patching `os.scandir` in `source_audio` or wrapping the listing) during a sequential inspection with more receiver attempts than scenes, and asserts exactly one listing; verify it fails today
- [x] 2.2 Add a RED test that a parallel inspection (3 workers) produces the same plan as the sequential one and that each worker process lists the speech `audio_dir` at most once (for example by having a picklable sampler record per-process scan counts in marker files); verify it fails today
- [x] 2.3 Add a test that a WAV added to `audio_dir` between two inspections (second with overwrite enabled) appears in the second plan's catalog, and that a WAV added after inspection still makes `apply_static_plan` fail with "Sampling catalog changed after preflight"; verify it passes before and after the change
- [x] 2.4 Implement design D1: the cache and context manager in `source_audio.py`, the context around `inspect_static_plan`'s body, and the worker `initializer` in `_plan_scenes_in_parallel`; verify 2.1-2.3 and the full `tests/test_static_plan.py` pass
- [x] 2.5 Document in `apps/orchestrator/README.md` (parallel planning paragraph) that source audio catalogs are listed once per inspection and process; verify by reading the paragraph back

## 3. Integration verification

- [x] 3.1 Run the full orchestrator suite (`uv run pytest` in `apps/orchestrator`) and verify no new failures beyond the 5 known pre-existing ones (`test_pra_e2e` end-to-end and 4 normalized-config golden tests) (1332 passed; same 5 pre-existing failures)
- [x] 3.2 Differential check: publish the same fixture batch on the base commit and on the change (scratch copy under `mktemp -d`) with 1 and 3 workers, and verify scene manifests and the failure journal are byte-identical and `static_plan.json` differs only in the `source_audio.py` generation fingerprint; also verify `list_source_wavs` returns an identical list on `assets/events/all/speech` with the base and new implementations
- [x] 3.3 Add a test in `tests/test_static_plan.py` that a plan whose generation contract has a different `source_audio.py` fingerprint is rejected on resume with the "Generation contract changed" conflict and accepted with `resume_if_possible: false` and `overwrite_existing: true`; verify it passes
- [x] 3.4 Re-run the planning profile on `configs/experiments/speech_directivity_experiment.yml` (40 scenes, scratch artifact root) with 1 and 3 workers and record the before/after times in this change's design.md Migration Plan
- [x] 3.5 Run `openspec validate cache-source-audio-listing --strict` and verify it passes

## Workflow follow-up

- Archive the change and sync `static-scene-planning` after review.
