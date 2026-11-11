# Tasks

## 1. Output analyzer loader: source yaw

- [x] 1.1 Add a failing test in `apps/output-analyzer/tests/test_loader.py`: a manifest source with `orientation_deg.yaw = -90` yields `source_yaw_deg == -90`; a source without `orientation_deg` yields `None` and unchanged other fields. Verify RED with `pytest apps/output-analyzer/tests/test_loader.py -k yaw`.
- [x] 1.2 In `loader.py::_source_record`, read `orientation_deg.yaw` via `reader.value` (mirroring receiver yaw); set `None` for the RAVEN summary path and unknown-frame sources. Verify GREEN with the same command and that the sources export includes the `source_yaw_deg` column.

## 2. Output analyzer report: plan arrows and legend

- [x] 2.1 Add failing tests in `apps/output-analyzer/tests/test_report.py` calling `_write_plan`: receiver yaw 0 → arrow `dx>0, dy≈0`; receiver yaw +90 → `dx≈0, dy>0` (screen down = −Z); source yaw −90 → `dy<0` in its event color; polygon/marker coordinates identical with and without yaw; missing yaw → no arrow and legend counts excluded; no yaw at all → legend contains "Orientation: Not available". Verify RED with `pytest apps/output-analyzer/tests/test_report.py -k orientation`.
- [x] 2.2 In `report.py::_write_plan`, draw a fixed-length arrow (with `data-role`/`data-yaw` attributes) from each positioned actor with a valid yaw, after bounds/scale are fixed, using `(cos y, −sin y)` in `(x, z)`. Verify with the tests from 2.1.
- [x] 2.3 Add the orientation legend entry (convention text plus drawn/excluded counts, or "Orientation: Not available" with reason). Verify with the tests from 2.1.

## 3. Verification

- [x] 3.1 Run the full analyzer suite `pytest apps/output-analyzer/tests` and confirm all pass.
- [x] 3.2 Generate a report on an existing run and visually check that a `fixed_position` source at azimuth 0 lies along the receiver arrow in "Positions by geometry".
