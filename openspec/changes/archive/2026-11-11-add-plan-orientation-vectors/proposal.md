# Proposal

## Why

The "Positions by geometry" floor plans show where the receiver and sources are, but not where they face. Orientation drives the receiver-relative DOA and source directivity, so a reader cannot check a scene's spatial setup at a glance without it.

## What Changes

- Each floor plan in "Positions by geometry" draws an orientation arrow from the receiver marker and from every source marker, pointing along the pose's yaw projected on the X/Z plane using the canonical pose convention (yaw 0 → +X, positive yaw → −Z).
- Arrows have a fixed on-screen length so they stay readable regardless of room size; they do not change plan bounds or scale.
- Poses without a valid yaw keep their marker and get no arrow; the legend states how many orientations were drawn and excluded.
- The plan legend gains an entry explaining the arrows (horizontal projection of yaw; pitch and roll not shown).
- The loader reads each manifest source's `orientation_deg.yaw` into a new `source_yaw_deg` field on source records. This adds one column to the sources export (additive, no existing value changes).

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `output-analyzer-report`: per-scene cards' floor plans show receiver and source orientation arrows; the sources export gains the additive `source_yaw_deg` field.

## Impact

- `apps/output-analyzer/src/output_analyzer/report.py` (`_write_plan`, plan legend).
- `apps/output-analyzer/src/output_analyzer/loader.py` (`_source_record`: read source yaw).
- `apps/output-analyzer/tests/test_report.py`, `test_loader.py`.
- No changes to orchestrator, MATLAB or Clarity components; manifests already carry `orientation_deg` for receiver and sources.
