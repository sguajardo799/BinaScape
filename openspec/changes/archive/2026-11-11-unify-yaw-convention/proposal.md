# Proposal

## Why

The pipeline uses three incompatible angular conventions for the same public frame (`x, y, z`, Y up, yaw 0 → +X). The sampler and `facing_surface_normal` rotate positive yaw toward public −Z; the MATLAB/RAVEN adapter, render validation and the circular array rotate it toward public +Z; and the output analyzer measures azimuth from +Z (`atan2(dx, dz)`). As a result, a `fixed_position` source at azimuth 0° with a non-zero receiver yaw is rendered in RAVEN at relative azimuth `−(2·yaw + az)` instead of 0° (with yaw +90° it lands directly behind the listener), wall-facing sources point into the wall mirror direction, and the analyzer's DOA histograms are offset. Every configured experiment samples yaw in `[-180, 180]`, so binaural renders and spatial labels are currently inconsistent.

## What Changes

- Define one canonical pose convention in the public frame: right-handed, Y up, yaw 0 → +X, positive yaw is a right-hand rotation about +Y, forward(yaw, pitch) = `(cos p·cos y, sin p, −cos p·sin y)`. Because the public→RAVEN mapping reflects Z, the rendered scene is mirrored: positive azimuth is heard on the listener's right (WAV channel 2) and +Z is the listener's left at yaw 0. Roll is ignored by direction vectors.
- **BREAKING** MATLAB/RAVEN adapter (schema 2.0/3.0 polygon contract): receiver and source view vectors are computed with the canonical convention before the public→RAVEN Z reflection. RAVEN view vectors for non-zero yaw flip their Z sign relative to today.
- **BREAKING** Render validation mirrors the corrected adapter vectors.
- **BREAKING** Circular array: microphone `k` sits at `center + r·[cos θk, 0, −sin θk]`, `θk = yaw + 360·k/N`; microphone 0 lies along the receiver forward direction and the ring index increases toward −Z (the listener's right in the rendered scene). The "legacy angular convention preserved" requirement is removed.
- **BREAKING** Output analyzer: global DOA is measured with the canonical convention (`atan2(−dz, dx)`, 0° = +X), so receiver-relative DOA = global DOA − receiver yaw is meaningful.
- Unchanged: `fixed_position` placement and `facing_surface_normal` yaw (already canonical), the Pyroomacoustics frame transform, the legacy (pre-2.0) MATLAB pose path, manifest field names.
- Previously generated renders, array outputs and analyzer reports are inconsistent with the new convention and must be regenerated; no automatic migration.

## Capabilities

### New Capabilities
- `spatial-pose-convention`: canonical public-frame orientation convention (yaw/pitch → direction), its use by `fixed_position` and `facing_surface_normal`, its mapping into the RAVEN adapter and render validation, and the analyzer DOA definition.

### Modified Capabilities
- `circular-microphone-array`: microphone geometry formula and its yaw scenarios change to the canonical rotation sense.
- `pyroomacoustics-render-backend`: the "Legacy circular-array angular convention preserved" requirement is removed; the backend keeps rendering descriptor positions unchanged.

## Impact

- `apps/matlab/core/orientation_deg_to_vectors.m`, `transform_schema2_pose_to_raven.m` (used by `build_receiver_from_pose.m`, `build_sources_from_config.m`) and their tests.
- `apps/orchestrator/src/acoustic_orchestrator/pipeline/render_validation.py` (`_source_pose`).
- `apps/orchestrator/src/acoustic_orchestrator/experiment/receiver_geometry.py` (`build_circular_array_descriptor`) and `tests/test_receiver_geometry.py`, PRA array tests.
- `apps/output-analyzer/src/output_analyzer/loader.py` (DOA computation) and its tests.
- `docs/architecture/contracts.md` (known-risk paragraph replaced by the convention).
- Existing datasets: spatial outputs are invalidated and must be regenerated.
