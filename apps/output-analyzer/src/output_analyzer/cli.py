from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .loader import load_run
from .report import build_analysis


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="output-analyzer",
        description="Analyze orchestrator render and degraded output metadata.",
    )
    subparsers = parser.add_subparsers(dest="command")

    analyze = subparsers.add_parser("analyze", help="Analyze an orchestrator run output directory.")
    analyze.add_argument("run_dir", type=Path, help="Run output directory, for example outputs/sim_001.")
    analyze.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Analysis output directory. Defaults to <run-dir>/analysis.",
    )
    analyze.add_argument("--quiet", action="store_true", help="Only print the report path.")
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
    data = load_run(run_dir)
    artifacts = build_analysis(data, args.output_dir)
    report_path = artifacts["report"]
    if args.quiet:
        print(report_path)
    else:
        print(f"Analysis report: {report_path}")
        print(f"Rendered scenes: {len(data.render_scenes)}")
        print(f"Rendered sources: {len(data.render_sources)}")
        print(f"Degraded metadata files: {len(data.degraded_records)}")
        if data.warnings:
            print("Warnings:")
            for warning in data.warnings:
                print(f"- {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
