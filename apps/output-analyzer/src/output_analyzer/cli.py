from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .loader import load_run, summary_for
from .report import build_analysis


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="output-analyzer",
        description="Analyze orchestrator render and degraded output metadata.",
    )
    subparsers = parser.add_subparsers(dest="command")

    analyze = subparsers.add_parser(
        "analyze", help="Analyze an orchestrator run output directory."
    )
    analyze.add_argument(
        "run_dir", type=Path, help="Run output directory, for example outputs/sim_001."
    )
    analyze.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Analysis output directory. Defaults to <run-dir>/analysis.",
    )
    analyze.add_argument(
        "--quiet", action="store_true", help="Only print the report path."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "analyze":
        parser.print_help()
        return 2

    run_dir = args.run_dir.resolve()
    if not run_dir.exists():
        parser.error(f"run directory does not exist: {run_dir}")
    try:
        data = load_run(run_dir)
        artifacts = build_analysis(data, args.output_dir)
    except OSError as exc:
        print(f"Operational error: {exc}", file=sys.stderr)
        return 1
    report_path = artifacts["report"]
    if args.quiet:
        print(report_path)
    else:
        print(f"Analysis report: {report_path}")
        print(
            f"Unique scenes: {len({row['scene_id'] for row in data.scenes + data.render_scenes})}"
        )
        print(f"Render variants: {len(data.render_scenes)}")
        print(f"Unique sources: {len(data.sources)}")
        print(f"Source-variant rows: {len(data.render_sources)}")
        print(f"Degraded metadata files: {len(data.degraded_records)}")
        if data.incidents:
            summary = summary_for(data)
            scenes = {
                i["entity"] for i in data.incidents if i["entity"].startswith("scene:")
            }
            excluded = sum(row["difference_s"] is None for row in data.rt30_comparisons)
            root_causes = {
                i.get("cause_id") or i["incident_id"] for i in data.incidents
            }
            print(
                f"Metadata issues: {summary['affected_entity_count']} affected entities; {len(scenes)} scenes; {excluded} excluded RT30 pairs; {len(root_causes)} causes."
            )
            print(
                "Categories (may overlap): "
                + "; ".join(
                    f"{key}={value}"
                    for key, value in summary["incident_counts"].items()
                )
            )
            sample = list(dict.fromkeys(i["entity"] for i in data.incidents))[:5]
            print("Sample identities: " + ", ".join(sample))
            print(
                f"Full incident details: {artifacts['report'].parent / 'incidents.jsonl'}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
