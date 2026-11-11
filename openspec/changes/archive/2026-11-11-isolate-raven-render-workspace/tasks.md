# Tasks

## 1. MATLAB: private material database

- [x] 1.1 Extend `apps/matlab/tests/MockRavenProject.m` with `setPathMaterials` and `setModel` call recording, and make `setModelToFaces` return `<dir of last setModel>\room_<N>_faces` like the toolbox. Verify existing MATLAB tests still pass: `matlab -batch "runtests('apps/matlab/tests')"`.
- [x] 1.2 Add a failing test: `prepare_raven_project` creates `RavenInput\binascape_ws\<project_name>\MaterialDatabase`, calls `setPathMaterials` with it before any `setMaterial`, and stores it in `ctx.workspace`; two project names get different directories. Observe RED.
- [x] 1.3 Implement it in `apps/matlab/core/prepare_raven_project.m`. Verify GREEN with the test from 1.2.

## 2. MATLAB: private room model

- [x] 2.1 Add a failing test in `test_build_room_from_config.m`: for a polygon room, `setModel` is called with a path inside `ctx.workspace\RavenModels` before `setModelToFaces`, and the resulting model path is inside the workspace; a model path outside the workspace raises an error. Observe RED.
- [x] 2.2 Implement the placeholder `setModel` and the post-condition check in `build_polygon_room_from_config`. Verify GREEN and that legacy shoebox tests are unchanged.

## 3. MATLAB: cleanup and metadata

- [x] 3.1 Add failing tests: after a successful and a failing run, the workspace is removed; a removal error emits a warning without changing the outcome; render metadata contains `raven_workspace.material_database_path` and `raven_workspace.model_path`. Observe RED.
- [x] 3.2 Implement cleanup (`onCleanup` + try/warning) and the metadata fields in `render_static_scene.m`; update `apps/matlab/README.md` (RAVEN workspace section). Verify GREEN and the full MATLAB suite.

## 4. Verification with real RAVEN

- [x] 4.1 Run `python scripts/diagnostics/rt30_reproducibility.py outputs/directivity_experiment102 directivity_0001 --repeats 2 --parallel 3` and confirm all T30 are EQUAL and equal to the pre-fix sequential values (1.080 … 0.246); confirm the shared `RavenDatabase/MaterialDatabase` has no new or modified files.
- [x] 4.2 Run `configs/experiments/speech_directivity_experiment.yml` with a new seed and `num_workers: 4`, run the analyzer, and confirm zero `rt30_conflict` incidents.
