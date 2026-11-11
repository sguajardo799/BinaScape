# Design

## Context

See proposal.md for the failure. Relevant `itaRavenProject` behavior (ITA toolbox, `applications/SoundFieldSimulation/Raven/itaRavenProject.m`):

- `setMaterial(name, …)` writes `fullfile(obj.pathMaterials, [name '.mat'])`. `pathMaterials` defaults to the global `RavenDatabase\MaterialDatabase`.
- `setPathMaterials(path)` sets `pathMaterials` and writes `ProjectPath_MaterialDB` into the project `.rpf`, so the RAVEN executable reads materials from that path.
- `setModelToFaces` writes `room_<N>_faces` to the `RavenModels` folder of the current `modelFileList` (or next to the `.rpf` otherwise), then calls `setModel` on it. It takes no file name.
- `setModel(path)` only stores the path in the `.rpf`; the file does not need to exist.
- `setModelToShoebox` (legacy) writes a model named by its dimensions: identical dimensions give identical content, so it is not a corruption risk.

`prepare_raven_project` already uses a unique project name per run (`<run>__<scene>__<variant>`), and RAVEN results go to per-project output folders.

## Goals / Non-Goals

**Goals:**
- No shared mutable RAVEN file between concurrent renders.
- No change to simulation inputs or results for a run rendered alone.

**Non-Goals:**
- Repairing or detecting already-corrupted runs (the analyzer's `rt30_conflict` already flags them).
- Changing worker scheduling or the Pyroomacoustics backend.
- Isolating the legacy shoebox model file (content-addressed by dimensions).

## Decisions

- **Workspace per project:** `C:\ITASoftware\Raven\RavenInput\binascape_ws\<project_name>\` with `MaterialDatabase\` and `RavenModels\`. Under `RavenInput` because RAVEN already reads from there and paths stay short. Created in `prepare_raven_project`, stored in `ctx.workspace`.
- **Materials via `setPathMaterials`** called in `prepare_raven_project` before any `setMaterial`. Alternative: prefix slot names with the project name — rejected: it changes model material names and the slot contract checked in `build_room_from_config`, and still leaves files in the global database.
- **Model via a placeholder path:** in the polygon path, call `rpf.setModel(fullfile(ws, 'RavenModels', 'pending.ac'))` before `setModelToFaces`, so `setModelToFaces` writes `ws\RavenModels\room_<N>_faces`. Then assert the returned path is inside the workspace and fail the render otherwise. Alternative: write the AC3D file ourselves — rejected: duplicates toolbox format logic. Alternative: copy after `setModelToFaces` — rejected: still races during the write.
- **Cleanup** in `render_single_receiver_run` after results are collected, and in its error path (`onCleanup`), with `rmdir(ws, 's')` wrapped in try/warning.
- **Metadata:** add `raven_workspace.material_database_path` and `raven_workspace.model_path` to the render summary (additive).
- **Tests with the mock:** extend `MockRavenProject` with `setPathMaterials`/`setModel` recording and make its `setModelToFaces` return a path derived from the last `setModel`, mirroring the toolbox. Integration evidence uses the real RAVEN via `scripts/diagnostics/rt30_reproducibility.py` and a full parallel experiment.

## Risks / Trade-offs

- [Toolbox behavior changes in another ITA version] → the post-condition check (model path inside the workspace, `pathMaterials` equals the workspace) fails loudly instead of silently sharing files.
- [RAVEN needs base materials from the global database] → all room slots are written by `setMaterial`; the base `.rpf` model is replaced. If RAVEN logs a missing material, the render fails and the test experiment will show it.
- [Leftover workspaces after a crash] → they are isolated and harmless; names are unique per project.
- [Extra disk I/O] → a few KB per run.
