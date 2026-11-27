# Design

## Context

- `experiment/sampler.py::_sample_hrtfs` loops over `_enabled_outputs(config)` (order: `binaural_hrtf`, `bte_rear_hartf`, `bte_front_hartf`) and calls `rng.choice` on each output's sorted catalog glob. One `scene_rng` drives the whole scene, so any change in the number or order of RNG calls changes every later draw.
- `experiment/scene_builder.py` copies only `hrtf_id` and `hrtf_path` into `receiver.hrtfs[]`. `matlab_runner.py` deep-copies one entry per variant, so extra fields travel to the runtime manifest. MATLAB `validate_static_scene_config.m` reads only `hrtf_id`/`hrtf_path` and ignores extra fields.
- `pipeline/static_plan.py::normalized_static_config` dumps the config into the plan contract and already pops fields at their defaults (`render.backend`, `pyroomacoustics`) so older runs keep their plan identity. The plan's `generation.implementation` hashes `sampler.py`, `scene_builder.py` and `config/validator.py`.
- Catalogs in `assets/hrtf`, `assets/hartf/front`, `assets/hartf/rear` each hold 30 files named `idNN_<kind>.daff`.

## Goals / Non-Goals

**Goals:**
- Opt-in shared-subject selection with config-time validation and manifest provenance (see `specs/receiver-ir-subject-pairing/spec.md`).
- Byte-identical sampling and plan config identity in `independent` mode.

**Non-Goals:**
- Pairing for `num_hrtfs > 1` (rejected in `shared` mode).
- A configurable subject-ID regex; the filename-prefix rule covers the current catalogs.
- Exposing `subject_id` in the output-analyzer report or render index (follow-up if needed; it is recoverable from the manifest).
- Re-rendering or migrating existing outputs.

## Decisions

1. **Setting location: `receiver_outputs.subject_pairing: Literal["independent", "shared"] = "independent"`.** Pairing is a relation across outputs, so it lives on `ReceiverOutputsConfig`, not on each `ReceiverOutputConfig`. Alternative: a boolean `shared_subject` — rejected because a named mode leaves room for future strategies without a second flag.

2. **Subject ID = filename part before the first `_`.** One small helper (e.g. `receiver_ir_subject_id(path) -> str | None`) used by both validator and sampler so the rule cannot diverge. Alternative: regex `id\d+` — rejected as too tied to the current dataset naming.

3. **Shared-mode sampling: one `rng.choice` over the sorted intersection of subject IDs, then a deterministic lookup per output.** Exactly one RNG call per scene, sorted for seed stability. Independent mode keeps the existing loop untouched (separate branch, no refactor of its RNG calls).

4. **Validation in `config/validator.py`.** Add a `_validate_subject_pairing` called after the per-output checks; it only runs in `shared` mode and only over enabled HRTF/HARTF outputs (circular array excluded by construction, since it is not in `_enabled_outputs`). Errors follow the existing Spanish message style and name `receiver_outputs.<output>`.

5. **Manifest provenance: add `subject_id` in `scene_builder.py` only when the sampled entry has it.** The sampler adds `subject_id` to each hrtf dict in `shared` mode; the builder forwards it when present. Independent manifests stay unchanged.

6. **Plan identity: pop `receiver_outputs.subject_pairing` from `normalized_static_config` when it equals `independent`**, mirroring the existing `render.backend` handling, so existing plan contracts keep the same config section.

## Risks / Trade-offs

- [Editing `sampler.py`/`scene_builder.py`/`validator.py` changes `generation.implementation` hashes] → In-flight runs started before this change need `resume=false, overwrite=true` to continue; this is the existing behavior for any generation code change. Finished runs are not affected.
- [Intersection smaller than the catalogs reduces subject diversity] → Validation fails only on an empty intersection; partial overlap is allowed by spec. Current catalogs are fully aligned (30/30/30).
- [Changing the default later would break seed reproducibility] → Default stays `independent`; documented in the example config.
- [MATLAB unexpectedly rejecting extra manifest fields] → Verified by reading `validate_static_scene_config.m`; add a test asserting the manifest still validates through the existing Python-side render validation.

## Migration Plan

No migration. To pair subjects in new experiments, add `subject_pairing: shared` under `receiver_outputs` and run with a new `run_name` (or `overwrite: true`). Rollback: remove the setting.
