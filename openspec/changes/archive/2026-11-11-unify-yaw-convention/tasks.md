# Tasks

## 1. Orchestrator: pin the canonical convention

- [x] 1.1 Add tests in `apps/orchestrator/tests/test_generate_manifests.py` asserting `fixed_position` azimuth 0 lies along forward(yaw) for yaw ∈ {−135, −90, 0, 45, 90, 180}, and that a receiver at `[2, 1, 3]` with yaw +90, azimuth 0, distance 1 gives `[2, 1, 2]` (expected GREEN: sampler is already canonical)
- [x] 1.2 Add a test that `facing_surface_normal` on a wall with inward normal `[0, 0, 1]` yields yaw −90 and canonical forward `[0, 0, 1]` (expected GREEN)

## 2. Orchestrator: circular array geometry

- [x] 2.1 Update `apps/orchestrator/tests/test_receiver_geometry.py` to the new offsets (yaw 0: `[r,0,0]`, `[0,0,−r]`, `[−r,0,0]`, `[0,0,r]`; yaw +90: mic 0 at `[0,0,−r]`) and add the "mic 0 agrees with fixed_position azimuth 0" test; observe RED
- [x] 2.2 Change `build_circular_array_descriptor` in `experiment/receiver_geometry.py` to `[cos θ, 0, −sin θ]`; observe GREEN
- [x] 2.3 Update Pyroomacoustics array tests (`test_pra_acoustics.py`, `test_pra_adapters.py`, `pra_support.py`, `test_array_output_index.py` as needed) that encode the legacy "+Z at yaw +90" characterization; remove the legacy-convention test

## 3. Orchestrator: render validation

- [x] 3.1 Add tests in `apps/orchestrator/tests/test_render_validation.py`: schema 3.0 source yaw +90 expects RAVEN view `[0, 0, 1]`; legacy schema yaw +90 keeps `[0, 0, 1]`; observe RED for schema 3.0
- [x] 3.2 Update `_source_pose` in `pipeline/render_validation.py` to build the canonical public forward and reflect Z for schema 2.0/3.0, keeping legacy behavior; update `render_fixtures.py` if it encodes view vectors; observe GREEN

## 4. MATLAB: RAVEN adapter

- [x] 4.1 Update `apps/matlab/tests/test_build_receiver_from_pose.m` and `test_build_sources_from_config.m`: schema 2.0/3.0 yaw +90 expects view `[0 0 1]` with position Z reflected; legacy yaw +90 still expects `[0 0 1]`; add yaw 0 and yaw −90 cases; observe RED
- [x] 4.2 Change `apps/matlab/core/transform_schema2_pose_to_raven.m` to build the public forward `(cos p·cos y, sin p, −cos p·sin y)` before the `[1, 1, −1]` reflection; leave `orientation_deg_to_vectors.m` unchanged; observe GREEN
- [x] 4.3 Check `write_render_contract_fixture.m`, `test_build_circular_array_receiver.m` and `verify_raven_array_response_contract.m` for hard-coded legacy directions or array offsets and update them (also fixed the duplicated ring formula in `core/validate_circular_array_descriptor.m` and the array fixtures in `test_validate_static_scene_config.m`)

## 5. Output analyzer: DOA

- [x] 5.1 Add tests in `apps/output-analyzer/tests/test_loader.py`: source at receiver + `[1,0,0]` → global 0; receiver + `[0,0,−1]` → global +90; receiver yaw +90 with that source → relative 0; observe RED
- [x] 5.2 Change the azimuth formula in `output_analyzer/loader.py` to `atan2(−dz, dx)` wrapped to `[−180, 180)`; observe GREEN; update fixtures in `tests/conftest.py` if they encode DOA values

## 6. Cross-component verification

- [x] 6.1 Add cross-component property tests for yaw ∈ {−135, −90, 0, 45, 90, 180} and azimuth ∈ {−90, 0, 30, 90}: `apps/orchestrator/tests/test_pose_convention.py` samples a `fixed_position` source, maps both poses into RAVEN with the adapter contract and asserts the RAVEN relative angle depends only on azimuth; `apps/output-analyzer/tests/test_loader.py` round-trips the same placement to relative DOA = azimuth (split because the orchestrator does not depend on the analyzer package)
- [x] 6.2 Run the full orchestrator suite, the output-analyzer suite and the MATLAB test suite; record results
- [x] 6.3 RAVEN acceptance check: render one scene with a `fixed_position` source at azimuth +90 and one at −90 (receiver yaw +90, 0.7 m) and record ILD. Result: ILD (L − R) −6.0 dB at +90 and +2.1 dB at −90, so positive azimuth is heard on the right; the reflection is kept and the left/right meaning documented (design decision 7)

## 7. Documentation

- [x] 7.1 Replace the "Riesgo conocido: convención angular del array" paragraph in `docs/architecture/contracts.md` with the canonical convention (frame, forward formula, left/right, RAVEN mapping, analyzer DOA) and the regeneration requirement for old outputs
- [x] 7.2 Update the comments in `experiment/sampler.py` (`_fixed_position_relative_to_receiver`) and `orientation_deg_to_vectors.m` so they describe the canonical convention and the legacy-only scope of the latter
- [x] 7.3 Rewrite every left/right statement (specs, proposal, design, `contracts.md`, READMEs, array example, code comments) to the rendered meaning and add diagrams to `docs/architecture/contracts.md` showing how to read poses, azimuth, array channels and analyzer DOA from the metadata

## Workflow follow-up

- Archive the change with `/opsx:archive` after implementation and verification.
- Regenerate affected datasets in a fresh output root and re-run the analyzer.
