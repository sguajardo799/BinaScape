# Tasks

## 1. Orchestrator config and validation

- [x] 1.1 Add `subject_pairing: Literal["independent", "shared"] = "independent"` to `ReceiverOutputsConfig` in `config/models.py`; verify with tests in `tests/test_receiver_ir_subject_pairing.py` that an omitted value loads as `independent` and `paired` is rejected naming `receiver_outputs.subject_pairing` (RED first)
- [x] 1.2 Add a shared helper that returns the subject ID (filename part before the first `_`, or `None`) and unit-test it with `id22_HARTF_front.daff`, a name without `_`, and a name starting with `_`
- [x] 1.3 Implement `_validate_subject_pairing` in `config/validator.py` (shared mode only: `num_hrtfs != 1`, file without subject ID, duplicate subject in one catalog, empty intersection); verify each spec scenario with a failing-then-passing test using temp catalogs, plus a test that independent mode with unprefixed files reports no subject errors
- [x] 1.4 Pop `receiver_outputs.subject_pairing` from `normalized_static_config` in `pipeline/static_plan.py` when it is `independent`; verify with a test that the normalized config of a config without the field is unchanged versus the base commit's shape

## 2. Orchestrator sampling and manifest

- [x] 2.1 Add the shared branch to `_sample_hrtfs` in `experiment/sampler.py`: one `rng.choice` over the sorted subject intersection, per-output lookup, `subject_id` on each entry; verify with a test that 200 planned scenes in shared mode have identical subject IDs across the three outputs and that a subject missing from one catalog is never chosen
- [x] 2.2 Verify independent mode is unchanged: test that a fixed seed produces the same `hrtf_path` per scene as the current implementation (pin expected paths captured from the base commit in a `mktemp -d` differential run, then hard-code them in the test)
- [x] 2.3 Verify shared-mode determinism: test that planning the same config and seed twice yields the same subject per scene
- [x] 2.4 Forward `subject_id` into `receiver.hrtfs[]` in `experiment/scene_builder.py` only when present; verify with tests that shared manifests carry `subject_id` and independent manifests contain only `hrtf_id`/`hrtf_path`, and that `render_validation` still accepts the shared manifest
- [x] 2.5 Verify the circular array case: test that shared mode with `circular_array` enabled validates and its array descriptor equals the one derived from the scene's receiver pose

## 3. Documentation

- [x] 3.1 Document `receiver_outputs.subject_pairing` (values, default, validation rules, `subject_id` manifest field) in `apps/orchestrator/examples/example_config.yml` and `apps/orchestrator/README.md`; verify the example config still loads and validates

## 4. Integration

- [x] 4.1 Run the full orchestrator test suite (`uv run pytest` in `apps/orchestrator`) and verify it passes — 1323 passed; 5 failures pre-existing and identical on the base commit (`test_pra_e2e::test_render_static_end_to_end_without_matlab`, `test_raven_golden::test_normalized_config_matches_golden[circular_array_only|static_example]`, `test_render_backend_config::test_normalized_config_identical_with_and_without_explicit_raven[circular_array_only|static_example]`)
- [x] 4.2 Plan a small experiment (e.g. 20 scenes, `subject_pairing: shared`) without rendering and verify every scene manifest has one shared `subject_id` across its three `receiver.hrtfs[]` entries

## Workflow follow-up

- Re-run the directivity experiments with `subject_pairing: shared` under a new `run_name`.
- Archive the change after review.
