# Design

## Context

See proposal.md - Why. Current direction formulas, all in the public frame unless noted:

| Component | Location | Forward / angle today | Yaw +90 → |
|---|---|---|---|
| `fixed_position` placement | `experiment/sampler.py` `_fixed_position_relative_to_receiver` | `(cos·cos, sin, −cos·sin)` | −Z |
| `facing_surface_normal` | `experiment/sampler.py` `_wall_normal_orientation` | `yaw = atan2(−nz, nx)` (assumes −sin) | −Z |
| RAVEN adapter (schema 2.0/3.0) | `apps/matlab/core/transform_schema2_pose_to_raven.m` via `orientation_deg_to_vectors.m` | builds `(cos, sin, +sin)`, then reflects Z | +Z (public) |
| RAVEN adapter (legacy) | `build_receiver_from_pose.m`, `build_sources_from_config.m` | `(cos, sin, +sin)` in RAVEN coords, no reflection | n/a |
| Render validation | `pipeline/render_validation.py` `_source_pose` | mirrors the adapter | +Z (public) |
| Circular array | `experiment/receiver_geometry.py` | `[cos θ, 0, +sin θ]` | +Z |
| Analyzer DOA | `output_analyzer/loader.py` | `atan2(dx, dz)` (0° = +Z) | n/a |

MATLAB tests (`test_build_receiver_from_pose.m`, `test_build_sources_from_config.m`) pin the current adapter output (`[0 0 −1]` for yaw +90 in schema 2.0/3.0). The Pyroomacoustics frame transform (`backends/pyroomacoustics/frames.py`) is a proper rotation and is not affected; that backend only consumes array microphone positions.

## Goals / Non-Goals

**Goals:**
- One convention, `forward = (cos p·cos y, sin p, −cos p·sin y)`, used everywhere angles become directions or directions become angles.
- A cross-component test that proves "azimuth 0 is in front of the listener" end to end (sampler → adapter coordinates → analyzer).

**Non-Goals:**
- Changing manifest field names, schema versions, or the public frame axes.
- Changing the legacy (non-polygon) MATLAB path.
- Roll support, HRTF selection, or source directivity data.
- Automatic detection or migration of outputs rendered with the old convention.

## Decisions

1. **Canonical sense = right-hand rotation about +Y (positive yaw toward −Z).**
   It is what the sampler and `facing_surface_normal` already produce, and it is the proper rotation for a right-handed Y-up frame. The RAVEN acceptance render showed that, because the public→RAVEN mapping is a reflection, positive azimuth is heard on the listener's right; see decision 7. Alternative: adopt `+sin` (what RAVEN and the array do). Rejected: it is a left-handed rotation in this frame, would require changing the sampler, the wall-normal formula and their tests, for no gain in consistency.

2. **Fix the RAVEN adapter in `transform_schema2_pose_to_raven.m`, not in `orientation_deg_to_vectors.m`.**
   The transform will build the public forward vector with the canonical `−sin` and then apply the existing `[1, 1, −1]` reflection, so RAVEN receives `(cos·cos, sin, +cos·sin)`. `orientation_deg_to_vectors.m` stays as-is because the legacy path calls it directly and must not change. Alternative: change `orientation_deg_to_vectors.m` and compensate in the legacy path. Rejected: it touches the legacy contract for no benefit.

3. **Render validation mirrors the adapter per schema.** For schema 2.0/3.0 the expected RAVEN view becomes `(cos·cos, sin, +cos·sin)`; legacy keeps `(cos·cos, sin, +cos·sin)` without reflection, as today. Both cases share one helper so they cannot drift again.

4. **Circular array uses `[cos θ, 0, −sin θ]`.** Microphone 0 follows the receiver forward direction; channel order is otherwise unchanged. The PRA backend already renders descriptor positions without recomputing, so it needs no code change, only test updates and removal of the "legacy convention" requirement.

5. **Analyzer DOA uses `atan2(−dz, dx)`.** Relative DOA keeps the formula `global − yaw`, which is now correct because both angles share the same origin and sense.

6. **One end-to-end Python property test** covers sampler → public→RAVEN reflection (reimplemented in the test from the contract: position and vector Z negated) → analyzer, over several yaws and azimuths. MATLAB tests pin the adapter output for the same cases with literal expected vectors.

7. **Keep the public→RAVEN reflection and document the rendered left/right meaning.**
   The acceptance render (receiver yaw +90, `fixed_position` at 0.7 m) gave ILD (left − right) of −6.0 dB for azimuth +90 and +2.1 dB for azimuth −90, with WAV channel 1 = left ear (the same order MSBG assumes). The `[1, 1, −1]` reflection mirrors the whole scene (room, materials, poses), so geometry and acoustics stay consistent and only the left/right reading flips. The documented meaning is therefore: positive azimuth is heard on the right; at yaw 0, +Z is the left ear and −Z the right ear. Alternative: replace the reflection with a proper rotation so that positive azimuth is heard on the left. Rejected for this change: it also moves room geometry, walls and materials in MATLAB and deserves its own change and validation.

## Risks / Trade-offs

- [Resume reuses old outputs] `static-render-resumption` reuses valid variants without knowing which convention rendered them, so a resumed run could mix old and new renders. → Migration requires a fresh output root (or `overwrite_existing: true`) for every experiment; documented in `docs/architecture/contracts.md`.
- [Sign error moves to a different place] The fix is only sign changes, which are easy to get wrong. → Literal-vector tests for yaw ∈ {0, 90, −90, 180} on every component plus the end-to-end property test.
- [RAVEN handedness assumption] The fix assumes the existing public→RAVEN Z reflection is itself correct for positions. → Acceptance check: render one scene with a `fixed_position` source at azimuth +90 and record which ear is louder. Outcome: the reflection mirrors the rendered scene; see decision 7.
- [Analyzer reports from old datasets change] Re-running the analyzer on old outputs gives different DOA histograms. → Expected; old datasets are declared inconsistent anyway.

## Migration Plan

1. Merge the code and spec change.
2. Regenerate every dataset that uses non-zero receiver yaw, `fixed_position`, `facing_surface_normal` or a circular array, into a new output root.
3. Re-run the analyzer on the regenerated outputs.
Rollback: revert the change; outputs generated after the change must then also be discarded.

## Open Questions

- Whether to also add a convention identifier to output metadata so future resumes can reject mixed outputs automatically. Not required by this change's specs; can be proposed separately.
