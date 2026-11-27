# Proposal

## Why

Concurrent RAVEN renders (`execution.num_workers > 1`) corrupt each other's room acoustics. `itaRavenProject.setMaterial` writes every material to a machine-global `RavenDatabase/MaterialDatabase/<slot>.mat`, and `setModelToFaces` writes the room model to a global `RavenModels/room_<N>_faces`. Slot names (`wall_001`, `floor`, `ceiling`, `matShoebox1`…) and face counts repeat across scenes, so a worker can simulate its scene with another scene's materials or geometry.

Evidence from `outputs/directivity_experiment102` (30 scenes, 3 receiver variants, 4 workers): 26 scenes report `rt30_conflict`, with T30 spreads up to 10× between variants of the same room. Re-rendering `directivity_0001` sequentially, and with its three variants in parallel, gives identical T30 for every variant and repeat (deterministic). It matches only the original `bte_rear_hartf` render; the other two originals differ by up to 74 %. Both the T30 and the rendered audio of affected variants are invalid.

## What Changes

- Each RAVEN render run gets a private workspace directory keyed by its unique project name. The run's material database and generated room model are written there instead of the shared RAVEN directories.
- Applies to polygon rooms (schema 2.0/3.0) and legacy shoebox rooms, for binaural and circular-array outputs.
- The workspace is removed after the run finishes, success or failure, best-effort; a failed cleanup does not fail the render.
- Render metadata records the material database and model paths used.
- Slot names, materials, geometry and outputs stay the same. Sequential renders produce the same results as before.
- Runs rendered in parallel before this fix are not repaired; they must be re-rendered.

## Capabilities

### New Capabilities
- `raven-render-isolation`: concurrent RAVEN renders cannot read or overwrite each other's materials or room model.

### Modified Capabilities
- None.

## Impact

- `apps/matlab/core/prepare_raven_project.m`: create the workspace and point the material database to it.
- `apps/matlab/core/build_room_from_config.m`: point the model path into the workspace before generating it (polygon path).
- `apps/matlab/static/render_static_scene.m`: workspace cleanup, metadata fields.
- `apps/matlab/tests/MockRavenProject.m` and MATLAB tests.
- No orchestrator, analyzer or Pyroomacoustics changes. Depends on the ITA toolbox `itaRavenProject` behavior described in design.md.
