from __future__ import annotations

import html
import json
import math
import re
import textwrap
from pathlib import Path
from typing import Any

from .scene_distributions import scene_metrics

from .loader import (
    AnalysisData,
    RT30_ABS_TOL_S,
    RT30_REL_TOL,
    summary_for,
    write_csv,
    write_json,
    write_jsonl,
)

PALETTE = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2", "#be123c"]


def build_analysis(
    data: AnalysisData, output_dir: str | Path | None = None
) -> dict[str, Path]:
    analysis_dir = (
        Path(output_dir).resolve() if output_dir else data.run_dir / "analysis"
    )
    images_dir = analysis_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "summary": analysis_dir / "summary.json",
        "report": analysis_dir / "report.html",
    }
    # Preserve existing filenames. Their observation units are explicitly documented.
    exports = {
        "scenes": data.scenes,
        "sources": data.sources,
        "render_scenes": data.render_scenes,
        "joined_render_sources": data.render_sources,
        "degraded_metadata": data.degraded_records,
        "incidents": data.incidents,
        "rt30_pairs": data.rt30_pairs,
        "rt30_records": data.rt30_records,
        "rt30_comparisons": data.rt30_comparisons,
        "rt30_consolidated": data.rt30_consolidated,
        "rt30_scene_means": data.rt30_scene_means,
        "materials": data.materials,
        "sampling_records": data.sampling_records,
        "sampling_batches": data.sampling_batches,
        "sampling_failures": data.sampling_failures,
        "coverage": data.coverage,
    }
    for name, records in exports.items():
        for extension, writer in [
            ("json", write_json),
            ("jsonl", write_jsonl),
            ("csv", write_csv),
        ]:
            path = analysis_dir / f"{name}.{extension}"
            writer(path, records)
            artifacts[f"{name}_{extension}"] = path
    artifacts.update(
        {
            "joined_sources_jsonl": artifacts["joined_render_sources_jsonl"],
            "joined_sources_csv": artifacts["joined_render_sources_csv"],
            "degraded_jsonl": artifacts["degraded_metadata_jsonl"],
            "degraded_csv": artifacts["degraded_metadata_csv"],
        }
    )
    parameters = analysis_dir / "general_parameters.json"
    write_json(parameters, data.general_parameters)
    artifacts["general_parameters_json"] = parameters
    charts, metrics = _write_charts(data, images_dir)
    for extension, writer in [
        ("json", write_json),
        ("jsonl", write_jsonl),
        ("csv", write_csv),
    ]:
        path = analysis_dir / f"metrics.{extension}"
        writer(path, metrics)
        artifacts[f"metrics_{extension}"] = path
    summary = summary_for(data)
    summary["rt30_tolerance"] = {
        "absolute_s": RT30_ABS_TOL_S,
        "relative": RT30_REL_TOL,
        "rule": "abs(a-b) <= max(absolute_s, relative * max(abs(a), abs(b))); all valid variant pairs",
    }
    summary["report_metrics"] = [
        {key: value for key, value in row.items() if key != "observations"}
        for row in metrics
    ]
    write_json(artifacts["summary"], summary)
    _write_report(artifacts["report"], data, summary, charts, metrics)
    artifacts.update(charts)
    return artifacts


def _write_charts(
    data: AnalysisData, images_dir: Path
) -> tuple[dict[str, Path], list[dict[str, Any]]]:
    charts: dict[str, Path] = {}
    metrics: list[dict[str, Any]] = []

    def metric(
        key: str,
        records: list[dict[str, Any]],
        field: str,
        title: str,
        unit: str,
        population: str,
        section: str,
        categorical: bool = False,
    ) -> None:
        observations = []
        for record in records:
            raw = record.get(field)
            value = (
                raw
                if categorical and raw is not None and raw != ""
                else (_float_or_none(raw) if not categorical else None)
            )
            if value is not None:
                observations.append(
                    {
                        "scene_id": record.get("scene_id"),
                        "source_id": record.get("source_id"),
                        "variant_id": record.get("variant_id"),
                        "value": value,
                        "frequency_hz": record.get("frequency_hz"),
                        "provenance": record.get(
                            "provenance",
                            record.get("metadata_path", record.get("path")),
                        ),
                    }
                )
        row = {
            "key": key,
            "title": title,
            "field": field,
            "unit": unit,
            "population": population,
            "section": section,
            "n_valid": len(observations),
            "n_excluded": len(records) - len(observations),
            "observations": observations,
            "exclusion_rule": "Missing, malformed or conflicted field; see incidents for document-level exclusions.",
        }
        values = [observation["value"] for observation in observations]
        if not categorical:
            row["mean"] = _mean(values)
        else:
            row["counts"] = _counts(values)
        metrics.append(row)
        path = images_dir / f"{key}.svg"
        title_with_n = title
        if categorical:
            _write_bar_chart(path, _counts(values), title_with_n, population)
        else:
            _write_histogram(path, values, title_with_n, unit)
        legend = [
            (
                None,
                None,
                f"Population: {population}; n={row['n_valid']}; excluded={row['n_excluded']}; unit: {unit}.",
            )
        ]
        if not values:
            legend.append((None, None, "No data / Not available — no observed series."))
        elif categorical:
            legend.append(
                (
                    None,
                    None,
                    "Bars count observations in each category; colors identify categories.",
                )
            )
            legend.extend(
                ("rect", PALETTE[index % len(PALETTE)], str(category))
                for index, category in enumerate(_counts(values))
            )
        else:
            legend.append(
                (
                    "rect",
                    PALETTE[0],
                    f"{title}: bars count {population} in value intervals ({unit}).",
                )
            )
        if key.startswith("rt30_"):
            bands = sorted(
                {
                    observation["frequency_hz"]
                    for observation in observations
                    if observation["frequency_hz"] is not None
                }
            )
            legend.append(
                (
                    None,
                    None,
                    "Central frequencies (Hz): "
                    + (
                        ", ".join(f"{frequency:g}" for frequency in bands)
                        or "Not available"
                    ),
                )
            )
            if key == "rt30_difference":
                legend.append(
                    (
                        None,
                        None,
                        "Difference = RAVEN − Sabine (s); positive values mean RAVEN is larger.",
                    )
                )
        _append_legend(path, legend)
        charts[key] = path

    for field, title, unit in [
        ("room_length_m", "Envelope X extent", "m"),
        ("room_width_m", "Envelope Z extent", "m"),
        ("room_height_m", "Height Y", "m"),
        ("room_area_m2", "Real polygon area", "m²"),
        ("room_volume_m3", "Room volume", "m³"),
        ("n_sources", "Sources per scene", "sources"),
    ]:
        metric(
            "hist_" + field.removesuffix("_m").removesuffix("_m2").removesuffix("_m3"),
            data.scenes,
            field,
            title,
            unit,
            "unique scenes",
            "general",
        )
    metric(
        "geometry_types",
        data.scenes,
        "geometry_type",
        "Room geometry",
        "category",
        "unique scenes",
        "general",
        True,
    )
    metric(
        "hist_employed_hrtf",
        data.render_scenes,
        "employed_hrtf",
        "HRTF coverage",
        "identity",
        "unique render variants",
        "general",
        True,
    )
    for field, key, title, unit in [
        ("global_doa_deg", "hist_global_doa", "Global azimuth", "degrees"),
        (
            "receiver_relative_doa_deg",
            "hist_receiver_relative_doa",
            "Receiver-relative DoA",
            "degrees",
        ),
        ("distance_m", "hist_distance", "Source–receiver distance", "m"),
        ("elevation_deg", "hist_elevation", "Elevation", "degrees"),
        (
            "wall_distance_m",
            "hist_wall_distance",
            "Minimum distance to real walls",
            "m",
        ),
        ("gain_db", "hist_gain", "Declared gain", "dB"),
        ("start_time_s", "hist_start_time", "Declared start time", "s"),
    ]:
        metric(key, data.sources, field, title, unit, "unique sources", "acoustics")
    metric(
        "source_events",
        data.sources,
        "source_type",
        "Source event coverage",
        "category",
        "unique sources",
        "acoustics",
        True,
    )
    # The loader owns RT30 equivalence and averaging; never reclassify its results here.
    metric(
        "rt30_sabine_estimate",
        [row for row in data.rt30_records if row.get("method") == "Sabine estimate"],
        "value_s",
        "Sabine estimate — pooled scene/frequency values",
        "s",
        "unique scene/frequency records",
        "acoustics",
    )
    metric(
        "rt30_raven_rpf_gett30",
        data.rt30_consolidated,
        "value_s",
        "RAVEN consolidated T30 — pooled scene/frequency values",
        "s",
        "unique scene/frequency records",
        "acoustics",
    )
    metric(
        "rt30_difference",
        data.rt30_comparisons,
        "difference_s",
        "RAVEN minus Sabine, aligned bands",
        "s",
        "unique scene/frequency pairs",
        "acoustics",
    )
    for field, key, title, categorical in [
        ("hearing_profile_id", "degraded_profiles", "Hearing profiles", True),
        ("status", "degraded_status", "Degradation status", True),
        (
            "ear_asymmetry_db",
            "degraded_asymmetry",
            "Declared ear asymmetry on common bands",
            False,
        ),
    ]:
        metric(
            key,
            data.degraded_records,
            field,
            title,
            "category" if categorical else "dB",
            "unique degraded records",
            "quality",
            categorical,
        )
    for field, title, categorical in [
        ("scene_attempt", "Scene sampling attempt", False),
        ("receiver_attempt", "Receiver sampling attempt", False),
        ("attempts", "RT30 sampling attempts", False),
        ("requested_bin", "Requested RT30 sampling bin", True),
        ("obtained_bin", "Obtained RT30 sampling bin", True),
        ("matched_requested_bin", "Requested RT30 bin matched", True),
        ("fallback_used", "RT30 sampling fallback used", True),
    ]:
        unit = (
            "boolean"
            if field in {"matched_requested_bin", "fallback_used"}
            else "bin"
            if categorical
            else "attempts"
        )
        metric(
            "sampling_" + field,
            data.sampling_records,
            field,
            title,
            unit,
            "unique scene sampling records",
            "quality",
            categorical,
        )
    metric(
        "background_noise",
        data.render_scenes,
        "background_noise_applied",
        "Declared background noise application",
        "boolean",
        "unique render variants",
        "quality",
        True,
    )
    metric(
        "material_coverage",
        data.materials,
        "material_id",
        "Material coverage by surface",
        "material identity",
        "unique scene surfaces",
        "quality",
        True,
    )
    metric(
        "surface_area",
        data.materials,
        "surface_area_m2",
        "Real surface area",
        "m²",
        "unique scene surfaces",
        "quality",
    )
    metric(
        "receiver_wall_distance",
        data.scenes,
        "wall_distance_m",
        "Receiver minimum distance to real walls",
        "m",
        "unique scenes",
        "acoustics",
    )
    for index, scene in enumerate(data.scenes):
        geometry = scene.get("geometry_type")
        if geometry not in {"shoebox", "l_shape", "trapezoid"}:
            continue
        sources = [
            row for row in data.sources if row.get("scene_id") == scene.get("scene_id")
        ]
        path = images_dir / f"plan_{index:05d}.svg"
        if _write_plan(path, scene, sources):
            charts[f"plan_{geometry}_{index:05d}"] = path
    for row in scene_metrics(data):
        metrics.append(row)
        path = images_dir / f"{row['key']}.svg"
        if row["chart_type"] == "scene_histogram":
            _write_scene_histogram(path, row)
        else:
            _write_scene_scatter(path, row)
        charts[row["key"]] = path
    return charts, metrics


def _write_scene_scatter(path: Path, row: dict[str, Any]) -> None:
    width, height = 760, 610
    left, top, size = 100, 90, 440
    values = [point[field] for point in row["observations"] for field in ("sabine_s", "raven_s")]
    parts = []
    bands = ", ".join(f"{f:g}" for f in row["selected_frequencies_hz"]) or "Not available"
    parts.append(_label(24, 60, f"Selected bands (Hz): {bands}", "start", size=12))
    if values:
        low, high = min(values), max(values)
        padding = max((high - low) * 0.05, abs(low) * 0.05 if low == high else 0, 0.01)
        low, high = max(0, low - padding), high + padding
        scale = size / (high - low)
        parts.append(
            f'<g class="scene-scatter" data-axis-min="{low}" data-axis-max="{high}" '
            f'data-scale-x="{scale}" data-scale-y="{scale}">'
        )
        parts.extend([
            _axes(left, top, size, size),
            _numeric_x_ticks(left, top, size, size, low, high),
            _numeric_y_ticks(left, top, size, low, high),
            f'<line class="equality-line" x1="{left}" y1="{top + size}" '
            f'x2="{left + size}" y2="{top}" stroke="#dc2626" stroke-width="2" stroke-dasharray="6 4"/>',
        ])
        for point in row["observations"]:
            x = left + (point["sabine_s"] - low) * scale
            y = top + size - (point["raven_s"] - low) * scale
            parts.append(
                f'<circle class="scene-point" data-scene-id="{html.escape(point["scene_id"], quote=True)}" '
                f'data-x="{point["sabine_s"]}" data-y="{point["raven_s"]}" '
                f'cx="{x:.3f}" cy="{y:.3f}" r="3" fill="#2563eb" fill-opacity="0.6">'
                f'<title>{html.escape(point["scene_id"])}: Sabine {point["sabine_s"]:g} s; RAVEN {point["raven_s"]:g} s</title></circle>'
            )
        parts.append("</g>")
    else:
        parts.append(_label(left + size / 2, top + size / 2, "Not available — no complete paired means", "middle"))
    parts.append(_label(left + size / 2, height - 25, "Sabine complete mean RT30 (s)", "middle"))
    parts.append(_label(24, top + size / 2, "RAVEN complete mean T30 (s)", "middle", rotate=-90))
    path.write_text(_svg_shell(width, height, row["title"], "".join(parts)), encoding="utf-8")
    legend = _scene_legend(row)
    if values:
        legend.extend([
            ("circle", PALETTE[0], "One point per scene with both complete means on the same selected bands; no HRTF weighting."),
            ("line", "#dc2626", "Equality line: RAVEN = Sabine. Both axes have the same numeric range and scale; all paired values are shown."),
        ])
    _append_legend(path, legend)


def _scene_legend(row: dict[str, Any]) -> list[tuple[str | None, str | None, str]]:
    bands = ", ".join(f"{f:g}" for f in row["selected_frequencies_hz"]) or "Not available"
    entries = [
        (None, None, f"Population: unique scenes; n={row['n_valid']}; excluded={row['n_excluded']}; unit: s."),
        (None, None, f"Selected central frequencies (Hz): {bands}. Complete arithmetic means; metadata, not WAV measurements."),
    ]
    if not row["n_valid"]:
        entries.append((None, None, "No data / Not available — no observed series."))
    return entries


def _write_scene_histogram(path: Path, row: dict[str, Any]) -> None:
    width, height = 1000, 510
    left, top, plot_w, plot_h = 80, 90, 890, 270
    edges = row["bin_edges_s"]
    configured = row["edge_source"] == "sampling_batch"
    labels = [
        f"[{a:.4g}, {b:.4g}{']' if i == len(edges) - 2 else ')'}"
        for i, (a, b) in enumerate(zip(edges, edges[1:]))
    ]
    counts = list(row["bin_counts"])
    if configured:
        labels = [f"< {edges[0]:.4g} (underflow)"] + labels + [f"> {edges[-1]:.4g} (overflow)"]
        counts = [row["underflow_count"]] + counts + [row["overflow_count"]]
    maximum = row["count_axis_max"]
    bands = ", ".join(f"{f:g}" for f in row["selected_frequencies_hz"]) or "Not available"
    parts = [_label(24, 60, f"Selected bands (Hz): {bands}", "start", size=12)]
    parts.append(f'<g class="scene-histogram" data-count-max="{maximum}" data-edge-source="{row["edge_source"]}">')
    parts.append(_axes(left, top, plot_w, plot_h))
    parts.append(_numeric_y_ticks(left, top, plot_h, 0, maximum))
    parts.append(_label(20, top + plot_h / 2, "Scene count", "middle", rotate=-90))
    step = plot_w / max(1, len(counts))
    targets = row["target_counts"] if row["method"] == "sabine" else None
    for index, (label, count) in enumerate(zip(labels, counts)):
        x = left + index * step
        tail = configured and index in (0, len(counts) - 1)
        if row["n_valid"]:
            bar_h = count / maximum * plot_h
            color = "#94a3b8" if tail else PALETTE[0 if row["method"] == "sabine" else 2]
            parts.append(
                f'<rect class="scene-bin" data-count="{count}" data-tail="{str(tail).lower()}" '
                f'x="{x + 4:.2f}" y="{top + plot_h - bar_h:.2f}" width="{step - 8:.2f}" height="{bar_h:.2f}" fill="{color}"/>'
            )
            parts.append(_label(x + step / 2, top + plot_h - bar_h - 7, str(count), "middle", size=11))
        target_index = index - 1 if configured else index
        if targets is not None and 0 <= target_index < len(targets):
            y = top + plot_h - targets[target_index] / maximum * plot_h
            parts.append(
                f'<line class="planned-target" data-count="{targets[target_index]}" '
                f'x1="{x + 2:.2f}" x2="{x + step - 2:.2f}" y1="{y:.2f}" y2="{y:.2f}" '
                'stroke="#dc2626" stroke-width="2" stroke-dasharray="5 3"/>'
            )
        parts.append(_label(x + step / 2, top + plot_h + 22, label, "end", size=11, rotate=-40))
    if not row["n_valid"]:
        parts.append(_label(left + plot_w / 2, top + plot_h / 2, "Not available — no complete means", "middle"))
    parts.append(_label(left + plot_w / 2, height - 12, "Complete mean RT30 intervals (s)", "middle"))
    parts.append("</g>")
    path.write_text(_svg_shell(width, height, row["title"], "".join(parts)), encoding="utf-8")
    legend = _scene_legend(row)
    if row["n_valid"]:
        legend.append(("rect", PALETTE[0 if row["method"] == "sabine" else 2], "Bars count unique scenes, not frequency records or render variants."))
    if configured:
        legend.append(("rect", "#94a3b8", "Underflow and overflow are separate tail counts, not finite-width intervals or RAVEN sampling failures."))
    legend.append((None, None, f"Intervals: {row['interval_closure']}. Edge source: {row['edge_source']}."))
    if targets is not None:
        legend.append(("line", "#dc2626", f"Planned Sabine target counts by interval: {targets}; not observed counts and not RAVEN limits."))
    elif row["method"] == "raven":
        legend.append((None, None, "Sampling targets apply only to Sabine; RAVEN is not constrained to that distribution."))
    if row["reference_unavailable_reason"]:
        legend.append((None, None, "Sampling reference Not available: " + row["reference_unavailable_reason"] + "."))
    _append_legend(path, legend)


def _write_plan(
    path: Path, scene: dict[str, Any], sources: list[dict[str, Any]]
) -> bool:
    vertices = scene.get("footprint_vertices_m")
    if not isinstance(vertices, list) or len(vertices) < 3:
        return False
    # The loader supplies authoritative public-frame [x,z] polygon vertices.
    points = []
    for vertex in vertices:
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
            return False
        x, z = map(_float_or_none, vertex)
        if x is None or z is None:
            return False
        points.append((x, z))
    actors = [("Receiver", scene, "receiver", "#111827")]
    events = sorted(
        {str(source.get("source_type") or "Event unavailable") for source in sources}
    )
    colors = {
        event: PALETTE[index % len(PALETTE)] for index, event in enumerate(events)
    }
    for source in sources:
        event = str(source.get("source_type") or "Event unavailable")
        actors.append(
            (f"{source.get('source_id')} · {event}", source, "source", colors[event])
        )
    positioned = []
    for label, record, prefix, color in actors:
        x, z = (
            _float_or_none(record.get(prefix + "_x_m")),
            _float_or_none(record.get(prefix + "_z_m")),
        )
        if x is not None and z is not None:
            positioned.append((label, x, z, prefix, color))
    bounds = points + [(x, z) for _, x, z, _, _ in positioned]
    xmin, xmax = min(x for x, _ in bounds), max(x for x, _ in bounds)
    zmin, zmax = min(z for _, z in bounds), max(z for _, z in bounds)
    if xmax <= xmin or zmax <= zmin:
        return False
    width, height, left, top = 760, 535, 80, 65
    scale = min(580 / (xmax - xmin), 370 / (zmax - zmin))
    plot_w, plot_h = (xmax - xmin) * scale, (zmax - zmin) * scale

    def project(x: float, z: float) -> tuple[float, float]:
        return left + (x - xmin) * scale, top + plot_h - (z - zmin) * scale

    polygon = " ".join(
        f"{x:.4f},{y:.4f}" for x, y in [project(*point) for point in points]
    )
    parts = [
        f'<g data-scale-x="{scale}" data-scale-z="{scale}"><polygon points="{polygon}" fill="#f1f5f9" stroke="#334155" stroke-width="2"/>'
    ]
    for x, z in points:
        px, py = project(x, z)
        parts.append(f'<circle cx="{px}" cy="{py}" r="1" data-x="{x}" data-z="{z}"/>')
    for label, x, z, prefix, color in positioned:
        px, py = project(x, z)
        title = html.escape(f"{label}: X={x:g}, Z={z:g} m")
        if prefix == "receiver":
            parts.append(
                f'<rect x="{px - 5}" y="{py - 5}" width="10" height="10" fill="{color}"><title>{title}</title></rect>'
            )
        else:
            parts.append(
                f'<circle cx="{px}" cy="{py}" r="5" fill="{color}"><title>{title}</title></circle>'
            )
        parts.append(_label(px + 8, py - 8, label, "start", size=10))
    parts.append("</g>")
    parts.extend(
        [
            _axes(left, top, plot_w, plot_h),
            _numeric_x_ticks(left, top, plot_w, plot_h, xmin, xmax),
            _numeric_y_ticks(left, top, plot_h, zmin, zmax),
            _label(left + plot_w / 2, 485, "X (m)", "middle"),
            _label(20, top + plot_h / 2, "Z (m)", "middle", rotate=-90),
            _label(
                left,
                510,
                f"Public frame: Y vertical; equal axis scale. Poses n={len(positioned)}, excluded={len(actors) - len(positioned)}",
                "start",
                size=11,
            ),
        ]
    )
    path.write_text(
        _svg_shell(width, height, f"Scene {scene.get('scene_id')}", "".join(parts)),
        encoding="utf-8",
    )
    legend = [
        (
            "line",
            "#334155",
            "Room contour: authoritative footprint; original X/Z coordinates in meters.",
        )
    ]
    if any(prefix == "receiver" for _, _, _, prefix, _ in positioned):
        legend.append(("rect", "#111827", "Receiver (square)."))
    else:
        legend.append(
            (
                None,
                None,
                "Receiver position: Not available; n=0; excluded=1; unit: receiver pose.",
            )
        )
    present_events = {
        str(source.get("source_type") or "Event unavailable")
        for source in sources
        if _float_or_none(source.get("source_x_m")) is not None
        and _float_or_none(source.get("source_z_m")) is not None
    }
    legend.extend(
        ("circle", colors[event], "Source event (circle): " + event)
        for event in events
        if event in present_events
    )
    valid_sources = sum(prefix == "source" for _, _, _, prefix, _ in positioned)
    if not valid_sources:
        legend.append(
            (
                None,
                None,
                f"Source positions: No data / Not available; n=0; excluded={len(sources)}; unit: source pose.",
            )
        )
    legend.append(
        (
            None,
            None,
            "Points retain their coordinates, including outside-room positions; no clipping or normalization.",
        )
    )
    _append_legend(path, legend)
    return True


def _append_legend(
    path: Path, entries: list[tuple[str | None, str | None, str]]
) -> None:
    """Append a visible, wrapped legend inside an exported SVG's expanded canvas."""
    svg = path.read_text(encoding="utf-8")
    match = re.search(r'width="(\d+)" height="(\d+)" viewBox="0 0 \d+ \d+"', svg)
    if match is None:
        raise ValueError("SVG canvas dimensions unavailable")
    width, old_height = map(int, match.groups())
    y = old_height + 22
    parts = [_label(24, y, "Legend", "start", size=13)]
    y += 24
    for shape, color, text in entries:
        lines = textwrap.wrap(
            text, width=max(30, (width - 100) // 7), break_long_words=True
        ) or [""]
        if shape == "circle":
            parts.append(f'<circle cx="32" cy="{y - 4}" r="5" fill="{color}"/>')
        elif shape == "rect":
            parts.append(
                f'<rect x="26" y="{y - 10}" width="12" height="12" fill="{color}"/>'
            )
        elif shape == "line":
            parts.append(
                f'<line x1="24" y1="{y - 4}" x2="42" y2="{y - 4}" stroke="{color}" stroke-width="2"/>'
            )
        for line in lines:
            parts.append(_label(52, y, line, "start", size=12))
            y += 19
        y += 5
    height = y + 12
    svg = (
        svg[: match.start()]
        + f'width="{width}" height="{height}" viewBox="0 0 {width} {height}"'
        + svg[match.end() :]
    )
    svg = svg.replace(
        "</svg>", '<g class="chart-legend">' + "".join(parts) + "</g></svg>"
    )
    path.write_text(svg, encoding="utf-8")


def _presentation_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _presentation_value(item)
            for key, item in value.items()
            if key != "render_configuration"
        }
    if isinstance(value, list):
        return [_presentation_value(item) for item in value]
    return value


def _records_table(records: list[dict[str, Any]]) -> str:
    records = _presentation_value(records)
    if not records:
        return "<p>Not available — n=0 valid observations.</p>"
    fields = list(dict.fromkeys(key for record in records for key in record))
    rows = [
        [
            json.dumps(record.get(key), ensure_ascii=False)
            if isinstance(record.get(key), (dict, list))
            else record.get(key)
            for key in fields
        ]
        for record in records
    ]
    return (
        f'<p>n={len(records)} normalized observations. Missing cells: Not available. See incidents for exclusions.</p><div class="scroll">'
        + _html_table(fields, rows)
        + "</div>"
    )


CAROUSEL_SCRIPT = """<script>
(function () {
  document.querySelectorAll('[data-carousel]').forEach(function (group) {
    const slides = Array.from(group.querySelectorAll('[data-slide]'));
    const previous = group.querySelector('[data-previous]');
    const next = group.querySelector('[data-next]');
    const select = group.querySelector('[data-scene-select]');
    const status = group.querySelector('[data-carousel-status]');
    let selected = 0;
    function show(index) {
      if (!slides.length) return;
      selected = Math.max(0, Math.min(slides.length - 1, index));
      slides.forEach(function (slide, position) { slide.hidden = position !== selected; });
      previous.disabled = selected === 0;
      next.disabled = selected === slides.length - 1;
      select.value = String(selected);
      status.textContent = (selected + 1) + ' of ' + slides.length + ' — ' + slides[selected].dataset.sceneId;
    }
    previous.addEventListener('click', function () { show(selected - 1); });
    next.addEventListener('click', function () { show(selected + 1); });
    select.addEventListener('change', function () { show(Number(select.value)); });
    group.addEventListener('keydown', function (event) {
      if (event.target.closest('select, input, textarea') || event.altKey || event.ctrlKey || event.metaKey) return;
      const actions = {ArrowLeft: selected - 1, ArrowRight: selected + 1, Home: 0, End: slides.length - 1};
      if (Object.prototype.hasOwnProperty.call(actions, event.key)) {
        event.preventDefault();
        show(actions[event.key]);
      }
    });
    show(0);
  });
})();
</script><noscript><p>JavaScript is disabled. All scene cards are shown below.</p><style>[data-slide][hidden]{display:block!important}</style></noscript>"""


def _scene_rt30(data: AnalysisData, scene_id: str, matched: dict[str, Any]) -> str:
    """Present existing scene/frequency results; never re-estimate or re-consolidate."""
    raw = [row for row in data.rt30_records if row.get("scene_id") == scene_id]
    consolidated = [
        row for row in data.rt30_consolidated if row.get("scene_id") == scene_id
    ]
    candidates = [
        row for row in data.rt30_comparisons if row.get("scene_id") == scene_id
    ]
    sabine = {
        row["frequency_hz"]: row
        for row in raw
        if row.get("method") == "Sabine estimate"
        and row.get("frequency_hz") is not None
    }
    raven = {
        row["frequency_hz"]: row
        for row in consolidated
        if row.get("frequency_hz") is not None
    }
    comparisons = {row["frequency_hz"]: row for row in candidates}
    included = set(matched.get("frequencies_hz", []))
    frequencies = sorted(set(sabine) | set(raven) | set(comparisons))
    issues = [
        row
        for row in data.incidents
        if row.get("entity") == f"scene:{scene_id}"
        or any(
            row.get("path") == item.get("metadata_path")
            for item in raw
            if item.get("metadata_path")
        )
    ]
    incidents_by_id = {row.get("incident_id"): row for row in data.incidents}

    def seconds(value: Any) -> str:
        return f"{value:.9g}" if _float_or_none(value) is not None else "No disponible"

    def cell(row: dict[str, Any] | None) -> str:
        if row is None:
            return "No disponible — banda ausente"
        if _float_or_none(row.get("value_s")) is not None:
            return seconds(row["value_s"])
        cause = incidents_by_id.get(row.get("cause_id"), {})
        reason = (
            "conflicto entre variantes"
            if cause.get("category") == "rt30_conflict"
            else "banda ausente, inválida o ambigua"
        )
        return "No disponible — " + reason

    rows = []
    for frequency in frequencies:
        candidate = comparisons.get(frequency, {})
        membership = (
            "Incluida"
            if frequency in included
            else (
                "Excluida — "
                + str(candidate.get("exclusion_reason") or "sin par válido")
            )
            if frequency in comparisons
            else "Adicional — fuera del promedio comparable"
        )
        rows.append(
            [
                f"{frequency:g}",
                cell(sabine.get(frequency)),
                cell(raven.get(frequency)),
                membership,
            ]
        )
    n_valid = matched.get("n_pairs", 0)
    n_excluded = len(candidates) - n_valid if candidates else None
    band_text = (
        ", ".join(f"{frequency:g}" for frequency in sorted(included)) + " Hz"
        if included
        else "No disponible"
    )
    result = f'<section class="scene-rt30" data-scene-rt30="{html.escape(scene_id, quote=True)}"><h5>RT30 por banda — {html.escape(scene_id)}</h5><p>Metadatos en segundos; no son mediciones del WAV. RAVEN: una consolidación por escena/frecuencia, sin duplicar HRTF.</p>'
    result += (
        _html_table(
            [
                "Frecuencia central (Hz)",
                "RT30 estimado — Sabine (s)",
                "T30 informado — RAVEN consolidado (s)",
                "Promedio comparable",
            ],
            rows,
        )
        if rows
        else "<p>No disponible — no hay frecuencias explícitas para esta sala.</p>"
    )
    result += f'<p class="scene-rt30-means">Promedio Sabine: {seconds(matched.get("sabine_mean_s"))}; Promedio RAVEN: {seconds(matched.get("raven_mean_s"))}. Unidad: s. Ambos promedios aritméticos usan las mismas bandas: {html.escape(band_text)}; n={n_valid}; excluidas={n_excluded if n_excluded is not None else "No disponible (conjunto esperado ausente)"}. Las bandas adicionales no participan.</p>'
    result += (
        "<details><summary>RT30: variantes, dispersión, causas y procedencia originales</summary>"
        + _records_table(consolidated)
        + _records_table(candidates)
        + _records_table(raw)
        + _records_table(issues)
        + "</details></section>"
    )
    return result


def _spatial_carousel(
    geometry: str,
    data: AnalysisData,
    charts: dict[str, Path],
    matched_means: dict[str, dict[str, Any]],
) -> str:
    scenes = [row for row in data.scenes if row.get("geometry_type") == geometry]
    plans = [
        (key, path)
        for key, path in charts.items()
        if key.startswith(f"plan_{geometry}_")
    ]
    group_id = f"geometry-{geometry}"
    title_id = f"{group_id}-title"
    select_id = f"{group_id}-select"
    viewport_id = f"{group_id}-cards"
    text = f'<section class="carousel" id="{group_id}" data-carousel="{geometry}" role="region" aria-roledescription="carousel" aria-labelledby="{title_id}" tabindex="0">'
    text += f'<h3 id="{title_id}">{geometry}</h3><p>Unique scenes: n={len(plans)} valid plans; excluded={len(scenes) - len(plans)}. Manual navigation; Left/Right arrows, Home/End.</p>'
    options, cards = [], []
    for position, (key, path) in enumerate(plans):
        scene = data.scenes[int(key.rsplit("_", 1)[-1])]
        scene_id = str(scene.get("scene_id"))
        escaped = html.escape(scene_id, quote=True)
        sources = [
            row for row in data.sources if row.get("scene_id") == scene.get("scene_id")
        ]
        variants = [
            {
                field: row.get(field)
                for field in ("hrtf_id", "variant_id", "metadata_path")
            }
            for row in data.render_scenes
            if row.get("scene_id") == scene.get("scene_id")
        ]
        options.append(f'<option value="{position}">{escaped}</option>')
        hidden = " hidden" if position else ""
        card = f'<article class="carousel-card" data-slide data-scene-id="{escaped}" role="group" aria-roledescription="slide" aria-label="{position + 1} of {len(plans)}: {escaped}"{hidden}>'
        card += (
            f"<h4>Scene {escaped}</h4><p>Run: {html.escape(str(data.run_dir))}</p><p>Associated HRTF/render variants (context only):</p>"
            + _records_table(variants)
        )
        card += path.read_text(encoding="utf-8")
        card += _scene_rt30(data, scene_id, matched_means.get(scene_id, {}))
        card += (
            "<details><summary>Pose provenance and declared margins</summary>"
            + _records_table([scene])
            + _records_table(sources)
            + "</details></article>"
        )
        cards.append(card)
    next_disabled = " disabled" if len(plans) <= 1 else ""
    select_disabled = " disabled" if len(plans) <= 1 else ""
    text += f'<div class="carousel-controls"><button type="button" data-previous aria-label="Previous scene in {geometry}" aria-controls="{viewport_id}" disabled>Previous</button><button type="button" data-next aria-label="Next scene in {geometry}" aria-controls="{viewport_id}"{next_disabled}>Next</button><label for="{select_id}">Select scene</label><select id="{select_id}" data-scene-select aria-controls="{viewport_id}"{select_disabled}>{"".join(options)}</select></div>'
    initial = (
        f"1 of {len(plans)}"
        if plans
        else "0 of 0 — Not available: no valid floor plans."
    )
    text += f'<p data-carousel-status role="status" aria-live="polite" aria-atomic="true">{initial}</p><div id="{viewport_id}">{"".join(cards)}</div></section>'
    return text


def _write_report(
    report_path: Path,
    data: AnalysisData,
    summary: dict[str, Any],
    charts: dict[str, Path],
    metrics: list[dict[str, Any]],
) -> None:
    def grid(section: str) -> str:
        figures = []
        for metric in metrics:
            if metric["section"] == section:
                caption = f"{metric['population']}; unit: {metric['unit']}; n={metric['n_valid']}; excluded={metric['n_excluded']}. {metric['exclusion_rule']}"
                figures.append(
                    "<figure>"
                    + charts[metric["key"]].read_text(encoding="utf-8")
                    + "<figcaption>"
                    + html.escape(caption)
                    + "</figcaption></figure>"
                )
        layout = ' style="grid-template-columns:1fr"' if section == "scene_means" else ""
        return f'<div class="grid"{layout}>' + "".join(figures) + "</div>"

    matched_means = {
        row["scene_id"]: row for row in summary.get("rt30_matched_means", [])
    }
    spatial = [
        _spatial_carousel(geometry, data, charts, matched_means)
        for geometry in ("shoebox", "l_shape", "trapezoid")
    ]
    rt_by_band = []
    for frequency in sorted({row["frequency_hz"] for row in data.rt30_pairs}):
        rows = [row for row in data.rt30_pairs if row["frequency_hz"] == frequency]
        rt_by_band.append(
            {
                "frequency_hz": frequency,
                "n_pairs": len(rows),
                "mean_sabine_s": _mean(_numbers(rows, "sabine_s")),
                "mean_raven_s": _mean(_numbers(rows, "raven_s")),
                "mean_raven_minus_sabine_s": _mean(_numbers(rows, "difference_s")),
            }
        )
    status = summary.get("status", "partial")
    if not data.scenes and not data.render_scenes and not data.degraded_records:
        status = "No analyzable data — sin datos analizables"
    count_keys = [
        "unique_scene_count",
        "scenes_with_render_count",
        "render_variant_count",
        "unique_source_count",
        "source_variant_count",
        "degraded_metadata_count",
        "affected_entity_count",
        "expected_variant_count",
    ]
    counts = _html_table(
        ["Population", "Count"], [(key, summary.get(key)) for key in count_keys]
    )
    parameters = _html_table(
        ["Parameter", "Observed values and frequencies / available configuration"],
        [
            (key, json.dumps(value, ensure_ascii=False))
            for key, value in _presentation_value(data.general_parameters).items()
        ],
    )
    quality = ""
    for title, records in [
        ("Scene × HRTF coverage and index reconciliation", data.coverage),
        ("Materials by surface, wall_id and area", data.materials),
        ("Sampling records", data.sampling_records),
        ("Sampling failures (optional index)", data.sampling_failures),
        ("Unique sampling batches", data.sampling_batches),
        ("Hearing profiles, status and declared asymmetry", data.degraded_records),
        ("Render variants and declared background noise", data.render_scenes),
        ("Incidents (auditable identities and paths)", data.incidents),
    ]:
        quality += (
            f"<details><summary>{title}</summary>{_records_table(records)}</details>"
        )
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Output Analysis Report</title>
<style>body{{font:14px/1.5 Arial,sans-serif;color:#1f2937;background:#f8fafc;margin:0}}header,main{{padding:24px;max-width:1200px;margin:auto}}h2{{border-bottom:1px solid #cbd5e1}}section{{margin-bottom:32px}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{border:1px solid #cbd5e1;text-align:left;padding:8px;vertical-align:top;overflow-wrap:anywhere}}th{{background:#e2e8f0}}.scroll{{overflow:auto;max-height:560px}}.grid{{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr))}}figure{{margin:0;background:white;padding:10px}}svg{{max-width:100%;height:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}figcaption{{color:#475569}}details{{margin:12px 0;padding:10px;background:white;border:1px solid #e2e8f0}}summary{{cursor:pointer;font-weight:bold}}[hidden]{{display:none!important}}.carousel{{border:1px solid #cbd5e1;padding:16px;margin-bottom:24px}}.carousel-controls{{display:flex;flex-wrap:wrap;gap:10px;align-items:center}}button,select{{font:inherit;padding:8px;max-width:100%}}:focus-visible{{outline:3px solid #2563eb;outline-offset:3px}}.carousel-card{{overflow-wrap:anywhere}}.carousel-card svg{{display:block;margin:auto}}</style></head>
<body><header><h1>Output Analysis Report</h1><p>{html.escape(str(data.run_dir))}</p><p>Status: {html.escape(str(status))}</p></header><main>
<section id="general-parameters"><h2>General parameters</h2><p>Supported scene/render schema: 3.0 only. Metadata only; no audio measurements.</p>{counts}{parameters}{grid("general")}</section>
<section id="rt30-scene-means"><h2>RT30 mean distribution by scene</h2><p>Complete arithmetic means over each scene's explicitly selected bands. Each method counts unique scenes independently, never bands or HRTF variants. Identical selected-frequency sets form separate labeled groups. Values are metadata, not WAV measurements. Panels share interval boundaries and scene-count scales within each group. Planned sampling targets apply only to Sabine, not RAVEN; out-of-range RAVEN means are not sampling failures. Scatter: X = Sabine, Y = RAVEN; one point per scene with both complete means.</p>{grid("scene_means")}<details><summary>Complete scene means, excluded bands and provenance</summary>{_records_table(data.rt30_scene_means)}</details></section>
<section><h2>Positions by geometry</h2><p>One exact public-frame X/Z floor plan per unique scene. Y is vertical. No HRTF duplication.</p>{"".join(spatial)}</section>
<section><h2>Acoustics and sources</h2>{grid("acoustics")}<h3>RT30 aligned comparison by central frequency</h3><p>RAVEN reported T30 versus Sabine estimate, seconds. No WAV measurements. Each pair is one scene/frequency; differences and means use exactly the same valid pairs. Additional RAVEN bands remain in method records. RAVEN uses the mean of numerically equivalent variants per scene/frequency; only bands beyond tolerance are excluded. Equivalence is a numerical criterion, not proof of simulator determinism. Tolerance: absolute {RT30_ABS_TOL_S:g} s, relative {RT30_REL_TOL:g}, tested for every valid variant pair.</p>{_records_table(rt_by_band)}<details><summary>Per-scene means on matched bands</summary>{_records_table(summary.get("rt30_matched_means", []))}</details><details><summary>All comparison candidates and exclusions</summary>{_records_table(data.rt30_comparisons)}</details><details><summary>All aligned RT30 pairs</summary>{_records_table(data.rt30_pairs)}</details><details><summary>Consolidated RAVEN means, variant counts and dispersion</summary>{_records_table(data.rt30_consolidated)}</details><details><summary>Individual method values: variant/frequency audit</summary>{_records_table(data.rt30_records)}</details></section>
<section><h2>Coverage and metadata quality</h2><p>Expected coverage unavailable unless an authoritative expectation is present in the coverage records. Missing optional failure indexes do not mean zero failures. Incident categories overlap; do not sum them as distinct affected entities.</p>{_html_table(["Category", "Count"], list(summary.get("incident_counts", {}).items()))}<details><summary>Population definitions and valid/excluded counts</summary><pre>{html.escape(json.dumps(summary.get("populations", {}), ensure_ascii=False, indent=2))}</pre></details>{grid("quality")}{quality}</section>
<section><h2>Data exports</h2><p>All figures are embedded; this HTML works offline by itself. Original SVGs are also in images/. scenes and sources contain unique scene/source observations. render_scenes contains unique render variants; joined_render_sources.jsonl and .csv contain source–variant rows. degraded_metadata contains unique degraded records. Each population also has JSON, JSONL and CSV exports. general_parameters.json, metrics.json, rt30_pairs, rt30_records, materials, sampling_records, sampling_batches, coverage, incidents and summary.json preserve values, denominators and provenance for audit. Null means Not available, never an imputed zero.</p></section>
</main>{CAROUSEL_SCRIPT}</body></html>"""
    report_path.write_text(body, encoding="utf-8", newline="\n")


def _write_histogram(
    path: Path, values: list[float], title: str, xlabel: str, bins: int = 12
) -> None:
    width, height = 760, 420
    left, right, top, bottom = 70, 30, 46, 70
    plot_w = width - left - right
    plot_h = height - top - bottom
    if not values:
        _write_empty_svg(path, title, "No numeric values available")
        return
    minimum, maximum = min(values), max(values)
    if math.isclose(minimum, maximum):
        minimum -= 0.5
        maximum += 0.5
    step = (maximum - minimum) / bins
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, max(0, int((value - minimum) / step)))
        counts[index] += 1
    max_count = max(counts) or 1
    bars = []
    for index, count in enumerate(counts):
        x = left + index * (plot_w / bins)
        bar_w = plot_w / bins - 3
        bar_h = plot_h * (count / max_count)
        y = top + plot_h - bar_h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="#2563eb"/>'
        )
    svg = _svg_shell(
        width,
        height,
        title,
        "".join(bars)
        + _axes(left, top, plot_w, plot_h)
        + _numeric_x_ticks(left, top, plot_w, plot_h, minimum, maximum)
        + _numeric_y_ticks(left, top, plot_h, 0, max_count)
        + _label(left + plot_w / 2, height - 20, xlabel, "middle")
        + _label(18, top + plot_h / 2, "count", "middle", rotate=-90),
    )
    path.write_text(svg, encoding="utf-8", newline="\n")


def _write_bar_chart(
    path: Path, counts: dict[str, int], title: str, ylabel: str
) -> None:
    width, height = 820, 460
    left, right, top, bottom = 90, 30, 46, 120
    plot_w = width - left - right
    plot_h = height - top - bottom
    if not counts:
        _write_empty_svg(path, title, "No records available")
        return
    items = list(counts.items())
    max_count = max(count for _, count in items) or 1
    bar_w = max(8, plot_w / len(items) - 8)
    parts = []
    for index, (label, count) in enumerate(items):
        x = left + index * (plot_w / len(items)) + 4
        bar_h = plot_h * count / max_count
        y = top + plot_h - bar_h
        color = PALETTE[index % len(PALETTE)]
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="{color}"/>'
        )
        parts.append(_label(x + bar_w / 2, y - 5, str(count), "middle", size=11))
        parts.append(
            _label(
                x + bar_w / 2, height - 48, _short(label), "end", size=10, rotate=-35
            )
        )
    svg = _svg_shell(
        width,
        height,
        title,
        "".join(parts)
        + _axes(left, top, plot_w, plot_h)
        + _label(20, top + plot_h / 2, ylabel, "middle", rotate=-90),
    )
    path.write_text(svg, encoding="utf-8", newline="\n")


def _html_table(headers: list[str], rows: list[Any]) -> str:
    head = "".join(f"<th>{html.escape(str(header))}</th>" for header in headers)
    body = []
    for row in rows:
        values = row if isinstance(row, list | tuple) else list(row)
        body.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(_format_value(value))}</td>" for value in values
            )
            + "</tr>"
        )
    return (
        f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _write_empty_svg(path: Path, title: str, message: str) -> None:
    svg = _svg_shell(680, 280, title, _label(340, 150, message, "middle", size=16))
    path.write_text(svg, encoding="utf-8", newline="\n")


def _svg_shell(width: int, height: int, title: str, inner: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  <title>{html.escape(title)}</title>
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="24" y="30" font-family="Arial, sans-serif" font-size="14" font-weight="700" fill="#111827">{html.escape(title)}</text>
  {inner}
</svg>
"""


def _axes(left: float, top: float, plot_w: float, plot_h: float) -> str:
    return (
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#374151" stroke-width="1"/>'
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#374151" stroke-width="1"/>'
    )


def _numeric_x_ticks(
    left: float,
    top: float,
    plot_w: float,
    plot_h: float,
    minimum: float,
    maximum: float,
    count: int = 5,
) -> str:
    if count < 2:
        return ""
    parts = []
    for index in range(count):
        ratio = index / (count - 1)
        value = minimum + (maximum - minimum) * ratio
        x = left + plot_w * ratio
        y = top + plot_h
        parts.append(
            f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{y + 5:.1f}" stroke="#374151" stroke-width="1"/>'
        )
        parts.append(_label(x, y + 20, _format_tick(value), "middle", size=10))
    return "".join(parts)


def _numeric_y_ticks(
    left: float,
    top: float,
    plot_h: float,
    minimum: float,
    maximum: float,
    count: int = 5,
    axis: str = "y",
) -> str:
    if count < 2:
        return ""
    parts = []
    for index in range(count):
        ratio = index / (count - 1)
        value = minimum + (maximum - minimum) * ratio
        y = top + plot_h - plot_h * ratio
        parts.append(
            f'<line x1="{left - 5:.1f}" y1="{y:.1f}" x2="{left:.1f}" y2="{y:.1f}" stroke="#374151" stroke-width="1"/>'
        )
        parts.append(_label(left - 9, y + 4, _format_tick(value), "end", size=10))
    if axis != "y":
        parts.append(_label(left - 9, top + 12, axis, "end", size=10))
    return "".join(parts)


def _label(
    x: float,
    y: float,
    text: str,
    anchor: str,
    size: int = 12,
    rotate: int | None = None,
) -> str:
    transform = (
        f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate is not None else ""
    )
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-family="Arial, sans-serif" '
        f'font-size="{size}" fill="#374151"{transform}>{html.escape(text)}</text>'
    )


def _numbers(records: list[dict[str, Any]], field: str) -> list[float]:
    return [
        value
        for value in (_float_or_none(record.get(field)) for record in records)
        if value is not None
    ]


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if value is None or value == "":
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _short(label: str, limit: int = 24) -> str:
    return label if len(label) <= limit else label[: limit - 1] + "..."


def _slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_")
    return slug or "unknown"


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return "Not available" if value is None else str(value)


def _format_tick(value: float) -> str:
    if math.isclose(value, round(value)):
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")
