"""Opt-in real RAVEN verification; invokes the production orchestrator, not a mock renderer."""

import argparse
import json
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.pipeline.output_index import load_variant_index
from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
from acoustic_orchestrator.pipeline.raven_resources import (
    _rpf_fields,
    verify_private_runtime,
)
from acoustic_orchestrator.pipeline.render_attempt import read_json_object, sha256_file
from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes

OUTPUTS = ("binaural_hrtf", "bte_front_hartf", "bte_rear_hartf")
FREQUENCIES = [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
ATOL_S = 1e-5
RTOL = 1e-5


def prepare_configs(config_path: Path, root: Path) -> list[Path]:
    config = load_config(config_path)
    if config.render.backend != "raven":
        raise ValueError("The isolation harness requires the RAVEN backend")
    for output in OUTPUTS:
        target = getattr(config.receiver_outputs, output)
        if target is None or not target.enabled or target.num_hrtfs != 1:
            raise ValueError(f"The harness requires exactly one enabled response for {output}")
    paths = []
    for workers in (1, 4):
        raw = config.model_dump(mode="json")
        raw["execution"].update(num_simulations=2, num_workers=workers, overwrite_existing=False,
                                 resume_if_possible=False, save_scene_manifest=True, save_render_metadata=True)
        raw["hearing_degradation"]["enabled"] = False
        if raw["receiver_outputs"].get("circular_array"):
            raw["receiver_outputs"]["circular_array"]["enabled"] = False
        raw["outputs"].update(artifact_root=(root / "runs").resolve().as_posix(), run_name=f"workers_{workers}")
        path = root / f"workers_{workers}.yml"
        with path.open("x", encoding="utf-8") as stream:
            yaml.safe_dump(raw, stream, allow_unicode=True)
        paths.append(path)
    return paths


def compare_t30(reference: dict, observed: dict) -> dict[str, Any]:
    if reference.get("band_frequencies_hz") != FREQUENCIES or observed.get("band_frequencies_hz") != FREQUENCIES:
        raise ValueError("T30 frequency mapping mismatch")
    a, b = reference["t30_s"], observed["t30_s"]
    if len(a) != 10 or len(b) != 10 or any(not math.isfinite(v) or v <= 0 for v in [*a, *b]):
        raise ValueError("Expected ten finite positive T30 bands")
    differences = [abs(x - y) for x, y in zip(a, b, strict=True)]
    return {"ok": all(delta <= ATOL_S + RTOL * abs(value) for delta, value in zip(differences, a, strict=True)),
            "max_band_difference_s": max(differences), "atol_s": ATOL_S, "rtol": RTOL}


def fingerprint(paths: list[Path]) -> dict[str, str]:
    files = set()
    for path in paths:
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(item for item in path.rglob("*") if item.is_file())
        else:
            raise FileNotFoundError(path)
    return {path.resolve().as_posix(): sha256_file(path) for path in sorted(files)}


def collect_run(config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    records = load_variant_index(layout["render_index_path"])
    measurements = {}
    resources = []
    for key, record in sorted(records.items()):
        if record["status"] != "completed":
            raise ValueError(f"Unaccepted harness variant: {key}")
        runtime_path = Path(record["runtime_manifest_path"])
        runtime = read_json_object(runtime_path)
        private = verify_private_runtime(runtime).resources
        rpf = Path("C:/ITASoftware/Raven/RavenInput") / f"{runtime['project_name']}.rpf"
        fields = _rpf_fields(rpf.read_bytes(), rpf)
        model = Path(fields[("rooms", "model")][0].replace("\\", "/")).resolve()
        materials = Path(fields[("global", "projectpath_materialdb")][0].replace("\\", "/")).resolve()
        if model.parent != private.seed_path.parent or materials != private.materials_dir:
            raise ValueError(f"RAVEN resolved shared resources: {rpf}")
        metadata_path = record["render_metadata_path"]
        if metadata_path is None:
            raise ValueError(f"Missing metadata path: {key}")
        measurements[key] = read_json_object(Path(metadata_path))["summary"]["room"]["reverberation"]
        resources.append({"variant_id": key, "runtime": runtime_path.as_posix(), "effective_rpf": rpf.as_posix(),
                          "model": model.as_posix(), "materials": materials.as_posix(),
                          "inventory": read_json_object(Path(record["attempt_outcome_path"]))["raven_resources_path"]})
    attempts = [read_json_object(p) for p in layout["render_attempts_dir"].glob("*/outcome.json")]
    return {"measurements": measurements, "resources": resources,
            "processes": [{"attempt_id": item["attempt_id"], "exit_code": item["exit_code"],
                           "manifest_sha256": item["manifest_sha256"]} for item in attempts]}


def verify(config_path: Path, root: Path, matlab: str = "matlab") -> dict[str, Any]:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    paths = prepare_configs(config_path, root)
    raven_config = load_config(paths[0]).raven
    if raven_config is None:
        raise ValueError("Missing base RPF")
    base = raven_config.base_rpf_file
    input_dir = next((path for path in base.parents if path.name.lower() == "raveninput"), None)
    if input_dir is None:
        raise ValueError("The real harness requires the standard RavenInput installation layout")
    raven = input_dir.parent
    globals_to_check = [base, raven / "RavenModels", raven / "RavenDatabase" / "MaterialDatabase"]
    before = fingerprint(globals_to_check)
    old_rpfs = set(input_dir.glob("*.rpf"))
    report: dict[str, Any] = {"schema_version": 1, "source_config": config_path.resolve().as_posix(),
                              "global_before": before, "runs": [], "comparisons": [], "ok": False}
    try:
        for path in paths:
            _, summary = render_static_scenes(path, matlab_executable=matlab)
            run = collect_run(path)
            run["summary"] = summary
            if len(run["measurements"]) != 6:
                raise ValueError("Expected two complete triplets per run")
            report["runs"].append(run)
            for scene in sorted({key.rsplit("__", 1)[0] for key in run["measurements"]}):
                reference = run["measurements"][f"{scene}__binaural_hrtf"]
                for output in OUTPUTS[1:]:
                    comparison = compare_t30(reference, run["measurements"][f"{scene}__{output}"])
                    report["comparisons"].append({"kind": "within_triplet", "run": path.stem,
                                                   "variant": f"{scene}__{output}", **comparison})
        left, right = [run["measurements"] for run in report["runs"]]
        if left.keys() != right.keys():
            raise ValueError("Sequential and parallel scene identities differ")
        for key in left:
            report["comparisons"].append({"kind": "sequential_vs_parallel", "variant": key,
                                          **compare_t30(left[key], right[key])})
        resources = [resource for run in report["runs"] for resource in run["resources"]]
        report["exclusive_resources"] = all(len({r[key] for r in resources}) == 12 for key in ("model", "materials", "effective_rpf"))
        report["global_after"] = fingerprint(globals_to_check)
        report["globals_unchanged"] = report["global_after"] == before
        report["new_technical_rpfs"] = sorted(p.as_posix() for p in set(input_dir.glob("*.rpf")) - old_rpfs)
        processes = [process for run in report["runs"] for process in run["processes"]]
        report["twelve_successful_processes"] = len(processes) == 12 and all(p["exit_code"] == 0 for p in processes)
        report["ok"] = (report["exclusive_resources"] and report["globals_unchanged"]
                        and report["twelve_successful_processes"] and all(c["ok"] for c in report["comparisons"]))
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        with (root / "report.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, default=Path("outputs") / f"raven_isolation_{uuid4().hex[:10]}")
    parser.add_argument("--matlab", default="matlab")
    args = parser.parse_args()
    result = verify(args.config, args.output, args.matlab)
    print(json.dumps({"ok": result["ok"], "report": str(args.output.resolve() / "report.json")}))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
