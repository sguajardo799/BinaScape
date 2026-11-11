"""RAVEN T30 reproducibility diagnostic.

Re-renders the existing runtime manifests of one scene into an isolated
directory (the source run is never modified) and compares ``rpf.getT30()``
across repeats and across receiver variants.

Questions it answers:
  * Same variant rendered twice -> different T30?  RAVEN is not deterministic.
  * Sequential renders of all variants agree, but parallel ones do not?
    Concurrent RAVEN processes interfere with each other.

Usage (from the repository root):
  python scripts/diagnostics/rt30_reproducibility.py \
      outputs/directivity_experiment102 directivity_0001 --repeats 2 --parallel 1
  python scripts/diagnostics/rt30_reproducibility.py \
      outputs/directivity_experiment102 directivity_0001 --repeats 1 --parallel 3
  python scripts/diagnostics/rt30_reproducibility.py \
      outputs/directivity_experiment102 directivity_0001 --compare-only
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MATLAB_APP_DIR = REPO_ROOT / "apps" / "matlab"
# Same tolerance the output analyzer uses for rt30_conflict.
ABS_TOL_S = 1e-6
REL_TOL = 1e-6


def _matlab_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def _t30(metadata_path: Path) -> list[float] | None:
    if not metadata_path.is_file():
        return None
    doc = json.loads(metadata_path.read_text(encoding="utf-8"))
    values = (((doc.get("summary") or {}).get("room") or {}).get("reverberation") or {}).get("t30_s")
    return [float(v) for v in values] if isinstance(values, list) else None


def _equal(a: list[float], b: list[float]) -> bool:
    return len(a) == len(b) and all(
        math.isclose(x, y, rel_tol=REL_TOL, abs_tol=ABS_TOL_S) for x, y in zip(a, b)
    )


def _max_rel_diff(a: list[float], b: list[float]) -> float:
    return max(abs(x - y) / max(abs(x), abs(y), 1e-12) for x, y in zip(a, b))


def prepare_jobs(run_dir: Path, scene: str, out_dir: Path, repeats: int, tag: str) -> list[dict]:
    manifests = sorted((run_dir / "metadata/manifests/runtime/render").glob(f"{scene}__*.json"))
    if not manifests:
        raise SystemExit(f"No runtime render manifests for {scene} under {run_dir}")
    jobs = []
    for manifest in manifests:
        variant = manifest.stem.split("__", 1)[1]
        for repeat in range(1, repeats + 1):
            run_id = f"{tag}_{variant}_r{repeat}"
            job_dir = out_dir / run_id
            job_dir.mkdir(parents=True, exist_ok=True)
            doc = json.loads(manifest.read_text(encoding="utf-8"))
            # Isolate every output and the RAVEN project name from the original run.
            doc["project_name"] = f"rt30diag__{scene}__{run_id}"
            doc["render"]["output_wav_path"] = (job_dir / f"{scene}__{variant}.wav").as_posix()
            metadata = job_dir / f"{scene}__{variant}__render.json"
            doc["render"]["output_metadata_path"] = metadata.as_posix()
            job_manifest = job_dir / "manifest.json"
            job_manifest.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            jobs.append(
                dict(variant=variant, repeat=repeat, run_id=run_id, manifest=job_manifest,
                     metadata=metadata, log=job_dir / "matlab.log",
                     original=run_dir / "output_audio/render" / variant / f"{scene}__{variant}__render.json")
            )
    return jobs


def run_job(job: dict, matlab: str) -> dict:
    command = (
        f"addpath(genpath('{_matlab_path(MATLAB_APP_DIR)}')); "
        f"run_raven_static_render('{_matlab_path(job['manifest'])}');"
    )
    started = time.time()
    with job["log"].open("w", encoding="utf-8") as log:
        code = subprocess.run([matlab, "-batch", command], stdout=log, stderr=subprocess.STDOUT).returncode
    job.update(exit_code=code, started=started, finished=time.time())
    print(f"  {job['run_id']}: exit={code} ({job['finished'] - started:.0f}s)", flush=True)
    return job


def report(jobs: list[dict]) -> None:
    print("\nT30 per render (s):")
    for job in jobs:
        values = _t30(job["metadata"])
        shown = "missing" if values is None else " ".join(f"{v:.3f}" for v in values)
        print(f"  {job['run_id']:<32} {shown}")

    print("\n1) Same variant, repeated renders (determinism):")
    by_variant: dict[str, list[dict]] = {}
    for job in jobs:
        by_variant.setdefault(job["variant"], []).append(job)
    deterministic = True
    for variant, group in by_variant.items():
        for a, b in combinations(group, 2):
            ta, tb = _t30(a["metadata"]), _t30(b["metadata"])
            if ta is None or tb is None:
                print(f"  {variant}: r{a['repeat']} vs r{b['repeat']}: missing result")
                continue
            same = _equal(ta, tb)
            deterministic &= same
            print(f"  {variant}: r{a['repeat']} vs r{b['repeat']}: "
                  f"{'EQUAL' if same else 'DIFFERENT'} (max rel diff {_max_rel_diff(ta, tb):.2%})")
    if all(len(g) < 2 for g in by_variant.values()):
        print("  (needs --repeats >= 2)")

    print("\n2) Across variants, first repeat (room T30 should not depend on the HRTF):")
    firsts = [g[0] for g in by_variant.values()]
    for a, b in combinations(firsts, 2):
        ta, tb = _t30(a["metadata"]), _t30(b["metadata"])
        if ta and tb:
            print(f"  {a['variant']} vs {b['variant']}: "
                  f"{'EQUAL' if _equal(ta, tb) else 'DIFFERENT'} (max rel diff {_max_rel_diff(ta, tb):.2%})")

    print("\n3) Versus the original run:")
    for job in firsts:
        ta, to = _t30(job["metadata"]), _t30(job["original"])
        if ta and to:
            print(f"  {job['variant']}: {'EQUAL' if _equal(ta, to) else 'DIFFERENT'} "
                  f"(max rel diff {_max_rel_diff(ta, to):.2%})")

    timed = [j for j in jobs if "started" in j]
    overlaps = sum(
        1 for a, b in combinations(timed, 2)
        if a["started"] < b["finished"] and b["started"] < a["finished"]
    )
    print(f"\nOverlapping renders in this session: {overlaps}")
    if not deterministic:
        print("Reading: repeated renders of the same input differ -> RAVEN T30 is not reproducible.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, help="Existing run, e.g. outputs/directivity_experiment102")
    parser.add_argument("scene", help="Scene id, e.g. directivity_0001")
    parser.add_argument("--repeats", type=int, default=2, help="Renders per variant (default 2)")
    parser.add_argument("--parallel", type=int, default=1, help="Concurrent MATLAB processes (default 1)")
    parser.add_argument("--matlab", default="matlab", help="MATLAB executable")
    parser.add_argument("--out", type=Path, default=None,
                        help="Diagnostic directory (default outputs/rt30_repro_<scene>)")
    parser.add_argument("--compare-only", action="store_true", help="Only compare existing results")
    args = parser.parse_args()

    out_dir = args.out or REPO_ROOT / "outputs" / f"rt30_repro_{args.scene}"
    tag = f"p{args.parallel}"
    jobs = prepare_jobs(args.run_dir.resolve(), args.scene, out_dir, args.repeats, tag)
    if not args.compare_only:
        print(f"Rendering {len(jobs)} job(s) with parallel={args.parallel} into {out_dir}")
        # Repeats run in separate waves so repeat r2 never overlaps r1 of the same variant.
        for repeat in range(1, args.repeats + 1):
            wave = [j for j in jobs if j["repeat"] == repeat]
            with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
                list(pool.map(lambda job: run_job(job, args.matlab), wave))
    report(jobs)
    return 0 if all(j.get("exit_code", 0) == 0 for j in jobs) else 1


if __name__ == "__main__":
    sys.exit(main())
