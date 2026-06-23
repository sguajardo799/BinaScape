from __future__ import annotations

import html
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .loader import AnalysisData, summary_for, write_csv, write_json, write_jsonl


PALETTE = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2", "#be123c"]


def build_analysis(data: AnalysisData, output_dir: str | Path | None = None) -> dict[str, Path]:
    analysis_dir = Path(output_dir).resolve() if output_dir else data.run_dir / "analysis"
    images_dir = analysis_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    summary = summary_for(data)
    artifacts: dict[str, Path] = {
        "summary": analysis_dir / "summary.json",
        "render_scenes_json": analysis_dir / "render_scenes.json",
        "joined_sources_jsonl": analysis_dir / "joined_render_sources.jsonl",
        "joined_sources_csv": analysis_dir / "joined_render_sources.csv",
        "degraded_jsonl": analysis_dir / "degraded_metadata.jsonl",
        "degraded_csv": analysis_dir / "degraded_metadata.csv",
        "report": analysis_dir / "report.html",
    }

    write_json(artifacts["summary"], summary)
    write_json(artifacts["render_scenes_json"], data.render_scenes)
    write_jsonl(artifacts["joined_sources_jsonl"], data.render_sources)
    write_csv(artifacts["joined_sources_csv"], data.render_sources)
    write_jsonl(artifacts["degraded_jsonl"], data.degraded_records)
    write_csv(artifacts["degraded_csv"], data.degraded_records)

    chart_paths = _write_charts(data, images_dir)
    _write_report(artifacts["report"], data, summary, chart_paths, analysis_dir)
    artifacts.update(chart_paths)
    return artifacts


def _write_charts(data: AnalysisData, images_dir: Path) -> dict[str, Path]:
    charts: dict[str, Path] = {}
    source_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in data.render_sources:
        source_by_type[str(record.get("source_type") or "unknown")].append(record)

    for source_type, records in sorted(source_by_type.items()):
        key = f"heatmap_{_slug(source_type)}"
        charts[key] = images_dir / f"heatmap_locations_{_slug(source_type)}.svg"
        _write_heatmap(charts[key], records, f"Source locations: {source_type}", source_type)

    charts["heatmap_receiver_locations"] = images_dir / "heatmap_receiver_locations.svg"
    _write_heatmap(
        charts["heatmap_receiver_locations"],
        data.render_scenes,
        "Receiver locations",
        "receiver",
        x_field="receiver_x_m",
        z_field="receiver_z_m",
        empty_message="No receiver coordinates available",
    )

    histogram_specs = [
        ("hist_global_doa", data.render_sources, "global_doa_deg", "Global DoA", "degrees"),
        (
            "hist_receiver_relative_doa",
            data.render_sources,
            "receiver_relative_doa_deg",
            "Receiver-relative DoA",
            "degrees",
        ),
        ("hist_distance", data.render_sources, "distance_m", "Source distance", "meters"),
        ("hist_elevation", data.render_sources, "elevation_deg", "Source elevation", "degrees"),
        ("hist_reverb_time", data.render_scenes, "reverberation_time_s", "Reverberation time", "seconds"),
        ("hist_room_width", data.render_scenes, "room_width_m", "Room width distribution", "meters"),
        ("hist_room_length", data.render_scenes, "room_length_m", "Room length distribution", "meters"),
        ("hist_room_area", data.render_scenes, "room_area_m2", "Room area distribution", "square meters"),
    ]
    for key, records, field, title, xlabel in histogram_specs:
        charts[key] = images_dir / f"{key}.svg"
        _write_histogram(charts[key], _numbers(records, field), title, xlabel)

    charts["hist_room_dimensions"] = images_dir / "hist_room_dimensions.svg"
    _write_multi_histogram(
        charts["hist_room_dimensions"],
        [
            ("width", _numbers(data.render_scenes, "room_width_m")),
            ("length", _numbers(data.render_scenes, "room_length_m")),
            ("height", _numbers(data.render_scenes, "room_height_m")),
        ],
        "Room dimensions distribution",
        "meters",
    )

    charts["hist_employed_hrtf"] = images_dir / "hist_employed_hrtf.svg"
    _write_bar_chart(
        charts["hist_employed_hrtf"],
        _counts(record.get("employed_hrtf") for record in data.render_scenes),
        "Employed HRTF",
        "rendered scenes",
    )

    charts["degraded_profiles"] = images_dir / "degraded_profiles.svg"
    _write_bar_chart(
        charts["degraded_profiles"],
        _counts(record.get("hearing_profile_id") for record in data.degraded_records),
        "Degraded metadata by hearing profile",
        "metadata files",
    )
    charts["degraded_status"] = images_dir / "degraded_status.svg"
    _write_bar_chart(
        charts["degraded_status"],
        _counts(record.get("status") for record in data.degraded_records),
        "Degraded metadata by status",
        "metadata files",
    )
    charts["degraded_loss"] = images_dir / "degraded_mean_loss_by_profile.svg"
    _write_profile_loss_chart(charts["degraded_loss"], data.degraded_records)
    return charts


def _write_report(
    report_path: Path,
    data: AnalysisData,
    summary: dict[str, Any],
    chart_paths: dict[str, Path],
    analysis_dir: Path,
) -> None:
    heatmaps = [(key, path) for key, path in chart_paths.items() if key.startswith("heatmap_")]
    histograms = [(key, path) for key, path in chart_paths.items() if key.startswith("hist_")]
    degraded = [(key, path) for key, path in chart_paths.items() if key.startswith("degraded_")]
    rows = [
        ("Rendered scenes", summary["render_scene_count"]),
        ("Rendered sources", summary["render_source_count"]),
        ("Degraded metadata files", summary["degraded_metadata_count"]),
        ("Render index rows", summary["render_index_count"]),
        ("Clarity index rows", summary["clarity_index_count"]),
    ]
    top_degraded = _top_rows(data.degraded_records, ["hearing_profile_id", "status", "mean_loss_db", "ear_asymmetry_db"], 12)
    top_scenes = _top_rows(data.render_scenes, ["scene_id", "employed_hrtf", "n_sources", "reverberation_time_s"], 12)

    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Output Analysis Report</title>
  <style>
    body {{ color: #1f2937; font: 14px/1.45 Arial, sans-serif; margin: 0; background: #f8fafc; }}
    header, main {{ margin: 0 auto; max-width: 1180px; padding: 24px; }}
    header {{ background: #111827; color: white; max-width: none; }}
    header div {{ margin: 0 auto; max-width: 1180px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    h2 {{ border-bottom: 1px solid #d1d5db; padding-bottom: 6px; }}
    section {{ margin: 0 0 28px; }}
    table {{ border-collapse: collapse; width: 100%; background: white; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 7px 9px; text-align: left; }}
    th {{ background: #e5e7eb; }}
    .grid {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); }}
    figure {{ background: white; border: 1px solid #e5e7eb; margin: 0; padding: 12px; }}
    figure img {{ display: block; height: auto; max-width: 100%; }}
    figcaption {{ color: #4b5563; margin-top: 8px; }}
    code {{ background: #e5e7eb; padding: 1px 4px; }}
  </style>
</head>
<body>
  <header><div>
    <h1>Output Analysis Report</h1>
    <p>{html.escape(str(data.run_dir))}</p>
  </div></header>
  <main>
    <section>
      <h2>Summary</h2>
      {_html_table(["Metric", "Value"], rows)}
    </section>
    <section>
      <h2>Rendered Scene Charts</h2>
      {_chart_grid(histograms, analysis_dir)}
    </section>
    <section>
      <h2>Source Location Heatmaps</h2>
      {_chart_grid(heatmaps, analysis_dir)}
    </section>
    <section>
      <h2>Degraded Metadata</h2>
      {_chart_grid(degraded, analysis_dir)}
      <h3>Sample rows</h3>
      {top_degraded}
    </section>
    <section>
      <h2>Rendered Scene Metadata</h2>
      {top_scenes}
    </section>
    <section>
      <h2>Data Files</h2>
      <p>Normalized joined data is written beside this report as <code>joined_render_sources.jsonl</code>, <code>joined_render_sources.csv</code>, <code>render_scenes.json</code>, <code>degraded_metadata.jsonl</code>, and <code>degraded_metadata.csv</code>.</p>
    </section>
  </main>
</body>
</html>
"""
    report_path.write_text(body, encoding="utf-8", newline="\n")


def _write_histogram(path: Path, values: list[float], title: str, xlabel: str, bins: int = 12) -> None:
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
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="#2563eb"/>')
    svg = _svg_shell(
        width,
        height,
        title,
        "".join(bars)
        + _axes(left, top, plot_w, plot_h)
        + _numeric_x_ticks(left, top, plot_w, plot_h, minimum, maximum)
        + _numeric_y_ticks(left, top, plot_h, 0, max_count)
        + _label(left + plot_w / 2, height - 20, xlabel, "middle")
        + _label(18, top + plot_h / 2, "count", "middle", rotate=-90)
    )
    path.write_text(svg, encoding="utf-8", newline="\n")


def _write_multi_histogram(
    path: Path,
    series: list[tuple[str, list[float]]],
    title: str,
    xlabel: str,
    bins: int = 12,
) -> None:
    width, height = 760, 440
    left, right, top, bottom = 70, 30, 46, 88
    plot_w = width - left - right
    plot_h = height - top - bottom
    populated = [(label, values) for label, values in series if values]
    if not populated:
        _write_empty_svg(path, title, "No room dimension values available")
        return

    all_values = [value for _, values in populated for value in values]
    minimum, maximum = min(all_values), max(all_values)
    if math.isclose(minimum, maximum):
        minimum -= 0.5
        maximum += 0.5
    step = (maximum - minimum) / bins
    counts_by_series = []
    for label, values in populated:
        counts = [0] * bins
        for value in values:
            index = min(bins - 1, max(0, int((value - minimum) / step)))
            counts[index] += 1
        counts_by_series.append((label, counts))
    max_count = max(max(counts) for _, counts in counts_by_series) or 1

    group_w = plot_w / bins
    bar_w = max(2.0, (group_w - 4) / len(counts_by_series))
    parts = []
    for series_index, (label, counts) in enumerate(counts_by_series):
        color = PALETTE[series_index % len(PALETTE)]
        for index, count in enumerate(counts):
            x = left + index * group_w + 2 + series_index * bar_w
            bar_h = plot_h * (count / max_count)
            y = top + plot_h - bar_h
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(1.0, bar_w - 1):.1f}" height="{bar_h:.1f}" fill="{color}" fill-opacity="0.82"/>'
            )
        legend_x = left + series_index * 110
        parts.append(f'<rect x="{legend_x:.1f}" y="{height - 38}" width="12" height="12" fill="{color}"/>')
        parts.append(_label(legend_x + 18, height - 28, label, "start", size=11))

    svg = _svg_shell(
        width,
        height,
        title,
        "".join(parts)
        + _axes(left, top, plot_w, plot_h)
        + _numeric_x_ticks(left, top, plot_w, plot_h, minimum, maximum)
        + _numeric_y_ticks(left, top, plot_h, 0, max_count)
        + _label(left + plot_w / 2, height - 52, xlabel, "middle")
        + _label(18, top + plot_h / 2, "count", "middle", rotate=-90),
    )
    path.write_text(svg, encoding="utf-8", newline="\n")


def _write_bar_chart(path: Path, counts: dict[str, int], title: str, ylabel: str) -> None:
    width, height = 820, 460
    left, right, top, bottom = 90, 30, 46, 120
    plot_w = width - left - right
    plot_h = height - top - bottom
    if not counts:
        _write_empty_svg(path, title, "No records available")
        return
    items = list(counts.items())[:20]
    max_count = max(count for _, count in items) or 1
    bar_w = max(8, plot_w / len(items) - 8)
    parts = []
    for index, (label, count) in enumerate(items):
        x = left + index * (plot_w / len(items)) + 4
        bar_h = plot_h * count / max_count
        y = top + plot_h - bar_h
        color = PALETTE[index % len(PALETTE)]
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="{color}"/>')
        parts.append(_label(x + bar_w / 2, y - 5, str(count), "middle", size=11))
        parts.append(_label(x + bar_w / 2, height - 48, _short(label), "end", size=10, rotate=-35))
    svg = _svg_shell(
        width,
        height,
        title,
        "".join(parts)
        + _axes(left, top, plot_w, plot_h)
        + _label(20, top + plot_h / 2, ylabel, "middle", rotate=-90),
    )
    path.write_text(svg, encoding="utf-8", newline="\n")


def _write_profile_loss_chart(path: Path, records: list[dict[str, Any]]) -> None:
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"left": [], "right": []})
    for record in records:
        profile = str(record.get("hearing_profile_id") or "unknown")
        for side, field in [("left", "left_mean_loss_db"), ("right", "right_mean_loss_db")]:
            value = _float_or_none(record.get(field))
            if value is not None:
                grouped[profile][side].append(value)
    if not grouped:
        _write_empty_svg(path, "Mean loss by hearing profile", "No loss metadata available")
        return
    counts: dict[str, int] = {}
    for profile, ears in grouped.items():
        left = _mean(ears["left"]) or 0.0
        right = _mean(ears["right"]) or 0.0
        counts[f"{profile} left"] = round(left, 2)
        counts[f"{profile} right"] = round(right, 2)
    _write_bar_chart(path, counts, "Mean loss by hearing profile", "mean dB loss")


def _write_heatmap(
    path: Path,
    records: list[dict[str, Any]],
    title: str,
    source_type: str,
    x_field: str = "source_x_m",
    z_field: str = "source_z_m",
    empty_message: str = "No source coordinates available",
) -> None:
    width, height = 680, 520
    left, right, top, bottom = 70, 40, 50, 60
    plot_w = width - left - right
    plot_h = height - top - bottom
    points = [(_float_or_none(record.get(x_field)), _float_or_none(record.get(z_field))) for record in records]
    points = [(x, z) for x, z in points if x is not None and z is not None]
    if not points:
        _write_empty_svg(path, title, empty_message)
        return
    max_width = _positive_axis_limit(_max_number(record.get("room_width_m") for record in records), max(x for x, _ in points))
    max_length = _positive_axis_limit(_max_number(record.get("room_length_m") for record in records), max(z for _, z in points))
    bins_x, bins_z = 12, 10
    grid = [[0 for _ in range(bins_z)] for _ in range(bins_x)]
    for x, z in points:
        xi = min(bins_x - 1, max(0, int((x / max_width) * bins_x))) if max_width else 0
        zi = min(bins_z - 1, max(0, int((z / max_length) * bins_z))) if max_length else 0
        grid[xi][zi] += 1
    max_count = max(max(column) for column in grid) or 1
    cell_w = plot_w / bins_x
    cell_h = plot_h / bins_z
    parts = []
    for xi in range(bins_x):
        for zi in range(bins_z):
            count = grid[xi][zi]
            fill = _heat_color(count / max_count)
            x = left + xi * cell_w
            y = top + plot_h - (zi + 1) * cell_h
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w:.1f}" height="{cell_h:.1f}" fill="{fill}" stroke="#ffffff" stroke-width="1"/>'
            )
    for x_value, z_value in points:
        x = left + (x_value / max_width) * plot_w if max_width else left
        y = top + plot_h - (z_value / max_length) * plot_h if max_length else top + plot_h
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#111827" fill-opacity="0.65"/>')
    svg = _svg_shell(
        width,
        height,
        title,
        "".join(parts)
        + _axes(left, top, plot_w, plot_h)
        + _numeric_x_ticks(left, top, plot_w, plot_h, 0, max_width)
        + _numeric_y_ticks(left, top, plot_h, 0, max_length, axis="z")
        + _label(left + plot_w / 2, height - 20, "room x position (m)", "middle")
        + _label(18, top + plot_h / 2, "room z position (m)", "middle", rotate=-90)
        + _label(width - 110, top + 20, f"type: {source_type}", "start", size=12),
    )
    path.write_text(svg, encoding="utf-8", newline="\n")


def _chart_grid(charts: list[tuple[str, Path]], analysis_dir: Path) -> str:
    if not charts:
        return "<p>No charts available.</p>"
    figures = []
    for key, path in charts:
        relative = path.relative_to(analysis_dir).as_posix()
        figures.append(
            f'<figure><img src="{html.escape(relative)}" alt="{html.escape(key)}"><figcaption>{html.escape(key.replace("_", " "))}</figcaption></figure>'
        )
    return '<div class="grid">' + "".join(figures) + "</div>"


def _top_rows(records: list[dict[str, Any]], fields: list[str], limit: int) -> str:
    rows = []
    for record in records[:limit]:
        rows.append([_format_value(record.get(field)) for field in fields])
    return _html_table(fields, rows) if rows else "<p>No records available.</p>"


def _html_table(headers: list[str], rows: list[Any]) -> str:
    head = "".join(f"<th>{html.escape(str(header))}</th>" for header in headers)
    body = []
    for row in rows:
        values = row if isinstance(row, list | tuple) else list(row)
        body.append("<tr>" + "".join(f"<td>{html.escape(_format_value(value))}</td>" for value in values) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _write_empty_svg(path: Path, title: str, message: str) -> None:
    svg = _svg_shell(680, 280, title, _label(340, 150, message, "middle", size=16))
    path.write_text(svg, encoding="utf-8", newline="\n")


def _svg_shell(width: int, height: int, title: str, inner: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  <title>{html.escape(title)}</title>
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="24" y="30" font-family="Arial, sans-serif" font-size="18" font-weight="700" fill="#111827">{html.escape(title)}</text>
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
        parts.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{y + 5:.1f}" stroke="#374151" stroke-width="1"/>')
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
        parts.append(f'<line x1="{left - 5:.1f}" y1="{y:.1f}" x2="{left:.1f}" y2="{y:.1f}" stroke="#374151" stroke-width="1"/>')
        parts.append(_label(left - 9, y + 4, _format_tick(value), "end", size=10))
    if axis != "y":
        parts.append(_label(left - 9, top + 12, axis, "end", size=10))
    return "".join(parts)


def _label(x: float, y: float, text: str, anchor: str, size: int = 12, rotate: int | None = None) -> str:
    transform = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate is not None else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-family="Arial, sans-serif" '
        f'font-size="{size}" fill="#374151"{transform}>{html.escape(text)}</text>'
    )


def _numbers(records: list[dict[str, Any]], field: str) -> list[float]:
    return [value for value in (_float_or_none(record.get(field)) for record in records) if value is not None]


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
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _max_number(values: Any) -> float | None:
    numbers = [value for value in (_float_or_none(value) for value in values) if value is not None]
    return max(numbers) if numbers else None


def _positive_axis_limit(*values: float | None) -> float:
    finite = [value for value in values if value is not None and math.isfinite(value)]
    limit = max(finite) if finite else 1.0
    return limit if limit > 0 else 1.0


def _heat_color(intensity: float) -> str:
    intensity = max(0.0, min(1.0, intensity))
    red = int(255 - 20 * intensity)
    green = int(245 - 150 * intensity)
    blue = int(235 - 215 * intensity)
    return f"rgb({red},{green},{blue})"


def _short(label: str, limit: int = 24) -> str:
    return label if len(label) <= limit else label[: limit - 1] + "..."


def _slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_")
    return slug or "unknown"


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return "" if value is None else str(value)


def _format_tick(value: float) -> str:
    if math.isclose(value, round(value)):
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")
