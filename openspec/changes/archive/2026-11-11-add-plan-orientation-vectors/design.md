# Design

## Context

`_write_plan` in `apps/output-analyzer/src/output_analyzer/report.py` projects public `(x, z)` to SVG with `project(x, z) = (left + (x − xmin)·s, top + plot_h − (z − zmin)·s)`: +X is right, +Z is up. Receiver yaw is already loaded as `receiver_yaw_deg` (scene record); source yaw is not loaded today — `_source_record` in `loader.py` ignores `orientation_deg`. The canonical convention (spec `spatial-pose-convention`) gives forward `(cos p·cos y, sin p, −cos p·sin y)`.

## Goals / Non-Goals

**Goals:**
- Arrows derived from the same convention as the analyzer's DOA, so a source in front of the receiver lies along the receiver arrow.

**Non-Goals:**
- Showing pitch, roll, directivity patterns or ear axes.
- Arrows on any chart other than the floor plans.
- Reading source yaw from RAVEN render summaries.

## Decisions

- **Direction from yaw only.** Plan vector `(cos y, −sin y)` in `(x, z)`; in SVG that is `dx = cos y·L`, `dy = +sin y·L` (SVG y grows downward, z grows upward). Alternative: full 3D forward projected with pitch (length `cos p`) — rejected: a fixed-length arrow is easier to read and pitch is not in the request.
- **Fixed pixel length** (~28 px) with a small arrowhead via an SVG `<marker>` per color, or an inline `<polygon>` head. Alternative: length in meters — rejected because it would vary with room size and could change bounds. Arrows are drawn after bounds are computed, so bounds/scale are untouched; an arrow may extend past the polygon, which is acceptable.
- **Source yaw in the loader, manifest only.** `_source_record` reads `source["orientation_deg"]["yaw"]` with the existing `reader.value` helper (same as receiver yaw). For the RAVEN summary path (`raven=True`) and unknown-frame sources, set `source_yaw_deg = None`: RAVEN yaw would need reflection and is not required here; manifest records take precedence for matching keys anyway (`source_map` lookup).
- **Export change is additive.** The new `source_yaw_deg` column appears in the sources export. The "Exports unchanged by presentation" requirement concerns presentation rules; this is a data addition declared in the delta spec. No existing field changes.
- **Data attributes for testability.** Each arrow carries `data-role`, `data-yaw` so tests can assert direction from the SVG without pixel parsing.

## Risks / Trade-offs

- [Arrows overlap labels when actors are close] → keep labels at their current offset; arrows are thin (1.5 px) and semi-opaque.
- [Reader's `value` helper may log an issue for missing optional yaw] → check how missing receiver yaw is handled and mirror it; do not add new incident kinds unless the helper already emits one.
- [Downstream consumers parsing sources CSV by position] → column is appended; consumers using headers are unaffected.
