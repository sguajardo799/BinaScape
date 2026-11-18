# Proposal

## Why

The scene sampler draws each receiver IR (`binaural_hrtf`, `bte_front_hartf`, `bte_rear_hartf`) independently from its own catalog, so the three renders of one scene almost never come from the same measured subject (runs `directivity_experiment102`–`108` show different subject IDs in more than 99% of scenes). Comparing HRTF and BTE HARTF renders of the same scene requires them to share one subject, and no configuration option exists today to request that.

## What Changes

- Add an opt-in experiment setting `receiver_outputs.subject_pairing` with values `independent` (default, current behavior) and `shared`.
- In `shared` mode, the orchestrator samples one subject ID per scene and selects, for every enabled HRTF/HARTF output, the catalog file belonging to that subject.
- Derive the subject ID from the catalog filename prefix (`id22_HRTF.daff`, `id22_HARTF_front.daff`, `id22_HARTF_rear.daff` → `id22`).
- Validate `shared` mode at config time: every enabled HRTF/HARTF output uses `num_hrtfs: 1`, every catalog file yields a subject ID, a subject ID appears at most once per catalog, and the enabled catalogs share at least one subject.
- In `shared` mode, record the selected `subject_id` on each `receiver.hrtfs[]` entry of the scene manifest.
- `independent` mode keeps its current RNG call sequence and manifest shape, so existing configs and seeds reproduce the same scenes.

## Capabilities

### New Capabilities
- `receiver-ir-subject-pairing`: how the orchestrator selects receiver IR files per scene across HRTF/HARTF outputs, including the opt-in shared-subject mode, its validation, and the subject provenance recorded in scene manifests.

### Modified Capabilities

None.

## Impact

- `apps/orchestrator`: `config/models.py` (`ReceiverOutputsConfig`), `config/validator.py`, `experiment/sampler.py` (`_sample_hrtfs`), `experiment/scene_builder.py` (manifest `receiver.hrtfs[]`).
- Scene manifest contract: optional `subject_id` string on `receiver.hrtfs[]` entries in `shared` mode only. MATLAB (`validate_static_scene_config.m`) copies only `hrtf_id`/`hrtf_path` and ignores extra fields, so no MATLAB change is required.
- Docs/examples: `apps/orchestrator/examples/example_config.yml` and the experiment config documentation gain the new option.
- Circular array output is not an IR-catalog output and is unaffected.
- No change to already generated outputs; affected experiments must be re-rendered with `subject_pairing: shared`.
