"""Auditable scene-level populations; no audio or producer configuration reads."""

from __future__ import annotations

from collections import defaultdict
from bisect import bisect_right

from .loader import AnalysisData, _number


def scene_groups(data: AnalysisData) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for row in data.rt30_scene_means:
        groups[row["band_group"]].append(row)
    return dict(sorted(groups.items())) or {"selection_unavailable": []}


def sampling_context(data: AnalysisData, groups: dict[str, list[dict]]) -> dict:
    context = dict(
        bin_edges_s=[], target_counts=None, edge_source="data_derived",
        reference_unavailable_reason="Sampling batch scope unavailable or ambiguous",
        reference_provenance=[],
    )
    ids = {row["scene_id"] for rows in groups.values() for row in rows}
    samples = {row["scene_id"]: row for row in data.sampling_records}
    if (
        len(groups) != 1 or "selection_unavailable" in groups or not ids
        or len(data.sampling_batches) != 1
        or set(samples) != ids
        or any(not samples[s].get("metadata_path") for s in ids)
    ):
        return context
    batch = data.sampling_batches[0]
    if (
        batch.get("eligible") is False
        or _number(batch.get("manifest_count")) != len(ids)
        or _number(batch.get("accepted_realizations")) != len(ids)
    ):
        return context
    raw = batch.get("bin_edges_s")
    edges = [_number(v) for v in raw] if isinstance(raw, list) else []
    if len(edges) < 2 or any(v is None for v in edges) or any(
        a >= b for a, b in zip(edges, edges[1:])
    ):
        context["reference_unavailable_reason"] = "Sampling batch bin edges missing or invalid"
        return context
    context.update(
        bin_edges_s=edges, edge_source="sampling_batch",
        reference_provenance=["sampling_batches[0]"] + sorted(
            {samples[s]["metadata_path"] for s in ids}
        ),
        reference_unavailable_reason="Sampling target counts missing or invalid",
    )
    bins = batch.get("bins")
    if not isinstance(bins, list) or len(bins) != len(edges) - 1:
        return context
    targets = {}
    for row in bins:
        if not isinstance(row, dict):
            return context
        index, count = _number(row.get("index")), _number(row.get("target_count"))
        if (
            index is None or not index.is_integer() or not 0 <= index < len(bins)
            or index in targets or count is None or count < 0 or not count.is_integer()
        ):
            return context
        targets[int(index)] = int(count)
    context.update(
        target_counts=[targets[i] for i in range(len(bins))],
        reference_unavailable_reason=None,
    )
    return context


def shared_edges(rows: list[dict], context: dict) -> list[float]:
    if context["bin_edges_s"]:
        return context["bin_edges_s"]
    values = [
        row[f"{method}_mean_s"] for row in rows for method in ("sabine", "raven")
        if row[f"{method}_mean_s"] is not None
    ]
    if not values:
        return []
    low, high = min(values), max(values)
    if low == high:
        padding = max(abs(low) * 0.05, 0.01)
        low, high = low - padding, high + padding
    # Preserve the observed maximum exactly, rather than reconstructing it by addition.
    return [low + (high - low) * i / 12 for i in range(12)] + [high]


def bin_counts(values: list[float], edges: list[float]) -> dict:
    counts = [0] * max(0, len(edges) - 1)
    below = above = 0
    for value in values:
        if value < edges[0]:
            below += 1
        elif value > edges[-1]:
            above += 1
        else:
            counts[min(bisect_right(edges, value) - 1, len(counts) - 1)] += 1
    return dict(bin_counts=counts, underflow_count=below, overflow_count=above)


def scene_metrics(data: AnalysisData) -> list[dict]:
    groups = scene_groups(data)
    context = sampling_context(data, groups)
    metrics = []
    for group, rows in groups.items():
        edges = shared_edges(rows, context)
        base = dict(
            band_group=group,
            selected_frequencies_hz=rows[0]["selected_frequencies_hz"] if rows else [],
            unit="s", population="unique scenes", section="scene_means",
            exclusion_rule="Complete mean requires every selected band; see scene-mean exclusions.",
            **context,
        )
        base["bin_edges_s"] = edges
        base["interval_closure"] = "[lower, upper), final interval [lower, upper]"
        panels = []
        for method, name in (("sabine", "Sabine estimate"), ("raven", "RAVEN consolidated T30")):
            observations, exclusions = [], []
            for row in rows:
                value = row[f"{method}_mean_s"]
                if value is not None:
                    observations.append(dict(scene_id=row["scene_id"], value=value, provenance=row["provenance"]))
                else:
                    exclusions.append(dict(
                        scene_id=row["scene_id"], reason=row[f"{method}_exclusion_reason"],
                        unavailable_frequencies_hz=row[f"{method}_unavailable_frequencies_hz"],
                        cause_ids=row[f"{method}_cause_ids"], provenance=row["provenance"],
                    ))
            values = [r["value"] for r in observations]
            panels.append(dict(
                base, key=f"rt30_scene_mean_{method}_{group}", method=method,
                title=f"{name} — complete mean RT30 per scene",
                chart_type="scene_histogram", n_valid=len(values), n_excluded=len(exclusions),
                observations=observations, exclusions=exclusions,
                mean=sum(values) / len(values) if values else None,
                **bin_counts(values, edges),
            ))
        scale = max([1] + (context["target_counts"] or []) + [
            count for panel in panels
            for count in panel["bin_counts"] + [panel["underflow_count"], panel["overflow_count"]]
        ])
        for panel in panels:
            panel["count_axis_max"] = scale
        metrics.extend(panels)
        paired, exclusions = [], []
        for row in rows:
            if row["sabine_mean_s"] is not None and row["raven_mean_s"] is not None:
                paired.append(dict(
                    scene_id=row["scene_id"], sabine_s=row["sabine_mean_s"],
                    raven_s=row["raven_mean_s"], provenance=row["provenance"],
                ))
            else:
                exclusions.append(dict(
                    scene_id=row["scene_id"], sabine_reason=row["sabine_exclusion_reason"],
                    raven_reason=row["raven_exclusion_reason"], provenance=row["provenance"],
                ))
        metrics.append(dict(
            base, key=f"rt30_scene_mean_scatter_{group}",
            title="Complete scene means — Sabine versus RAVEN", chart_type="scene_scatter",
            n_valid=len(paired), n_excluded=len(exclusions), observations=paired, exclusions=exclusions,
        ))
    return metrics
