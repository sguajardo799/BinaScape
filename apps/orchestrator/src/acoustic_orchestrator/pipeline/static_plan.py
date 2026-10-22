"""Read-only static scene preflight and explicit, guarded publication.

Call ``inspect_static_plan`` first, validate render/runtime artifacts in the
caller, then ``apply_static_plan``. Hold ``static_writer_lock`` around the whole
operation (including rendering) when coordinating a batch. Inspection never
creates directories. A lock left by an interrupted writer requires intervention.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Callable, Iterator
import uuid

from acoustic_orchestrator.config.models import AppConfig
from acoustic_orchestrator.config.validator import (
    derive_materials_root,
    resolve_background_audio_candidates,
    resolve_material_candidates,
)
from acoustic_orchestrator.experiment import sampler
from acoustic_orchestrator.experiment.acoustic_geometry import get_acoustic_geometry
from acoustic_orchestrator.experiment.geometry import canonical_wall_ids, signed_polygon_area, validate_footprint
from acoustic_orchestrator.experiment.manifest_writer import write_manifest
from acoustic_orchestrator.experiment.scene_builder import build_static_manifest
from acoustic_orchestrator.pipeline.output_paths import ArtifactLayout, resolve_artifact_layout
from acoustic_orchestrator.source_audio import list_source_wavs


PLAN_VERSION = 1


class StaticPlanConflict(ValueError):
    """Protected inputs cannot be proven compatible; no actions were applied."""

    def __init__(self, conflicts: list[str]) -> None:
        self.conflicts = conflicts
        super().__init__("Static preflight conflict:\n" + "\n".join(conflicts))


@dataclass(frozen=True)
class PlannedManifest:
    manifest: dict
    path: Path
    needs_write: bool
    scene_index: int


@dataclass
class StaticPlan:
    manifests: list[PlannedManifest]
    failures: list[dict]
    failure_history: list[dict]
    preconditions: dict[Path, tuple[int, int, str] | None]
    metadata: dict
    plan_path: Path
    failures_path: Path
    needs_plan_write: bool
    needs_failures_write: bool
    config: AppConfig
    catalog: dict
    scene_manifest_dir: Path


@dataclass(frozen=True)
class StaticWriterLock:
    path: Path
    token: str


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(path: Path) -> tuple[int, int, str] | None:
    if not path.exists():
        return None
    stat = path.stat()
    if not path.is_file():
        raise ValueError(f"Not a regular file: {path}")
    return stat.st_size, stat.st_mtime_ns, _file_hash(path)


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _finite_tree(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)


def _loads(text: str) -> dict:
    value = json.loads(text, object_pairs_hook=_pairs)
    _finite_tree(value)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def normalized_static_config(config: AppConfig) -> dict:
    """Functional identity, excluding scheduling, diagnostics and Clarity."""
    data = config.model_dump(mode="json")
    data["experiment"].pop("description", None)
    data["execution"] = {
        "num_simulations": config.execution.num_simulations,
        "save_render_metadata": config.execution.save_render_metadata,
    }
    data.pop("hearing_degradation", None)
    # MATLAB process telemetry is not an acoustic input.
    data.pop("matlab_diagnostics", None)
    return data


def static_catalog(config: AppConfig) -> dict:
    """Use exactly the sampler's scans/order (not a recursive audio glob)."""
    result: dict = {"sources": {}, "receiver_irs": {}, "materials": {}, "noise": {}}
    for source in config.source_sampling.source_types:
        result["sources"][source.event_type] = [p.resolve().as_posix() for p in list_source_wavs(source.audio_dir)]
    for name, output in sampler._enabled_outputs(config).items():
        result["receiver_irs"][name] = [p.resolve().as_posix() for p in sorted(output.ir_catalog_path.glob(output.file_pattern))]
    root = derive_materials_root(config)
    if root is None:
        raise ValueError("Cannot resolve materials root")
    materials = config.room_sampling.materials
    for material_id in sorted(set(materials.walls + materials.floor + materials.ceiling)):
        result["materials"][material_id] = [p.as_posix() for p in resolve_material_candidates(root, material_id)]
    if config.background_noise.enabled:
        for index, strategy in enumerate(config.background_noise.strategies):
            if strategy.type == "audio_folder":
                result["noise"][str(index)] = [p.as_posix() for p in resolve_background_audio_candidates(strategy)]
    return result


def _generation_contract() -> dict:
    # Explicit algorithm revision plus implementation fingerprints catches a
    # changed generator even if its author forgot to increment a version.
    experiment = Path(sampler.__file__).parent
    names = ("sampler.py", "scene_builder.py", "geometry.py", "acoustic_geometry.py",
             "room_acoustics.py", "reverberation_sampling.py", "treatment_catalog.py", "receiver_geometry.py")
    implementation = {name: _file_hash(experiment / name) for name in names}
    for name in ("source_audio.py", "config/validator.py"):
        implementation[name] = _file_hash(experiment.parent / name)
    return {"version": 1, "proposal_strategy_version": sampler.PROPOSAL_STRATEGY_VERSION,
            "implementation": implementation}


def functional_manifest(manifest: dict) -> dict:
    """Strip only mutable sampling/batch accounting, not acoustic choices."""
    result = deepcopy(manifest)
    result.pop("reverberation_batch", None)
    sampling = result.get("reverberation_sampling", {})
    for key in ("attempts", "rejections_by_reason", "raven_t30_rejections", "raven_retry_count"):
        sampling.pop(key, None)
    return result


def _scene_record(manifest: dict, index: int) -> dict:
    return {"functional_hash": _hash(functional_manifest(manifest)), "scene_index": index,
            "sampling": deepcopy(manifest["sampling"]),
            "reverberation_history": {key: deepcopy(value)
                for key, value in manifest["reverberation_sampling"].items()
                if key in {"attempts", "rejections_by_reason", "raven_t30_rejections", "raven_retry_count"}}}


def _references(manifest: dict) -> set[Path]:
    paths = {Path(manifest["base_rpf_file"])}
    for material in manifest["room"]["material_files"].values():
        paths.add(Path(material["material_path"]))
    for ir in manifest["receiver"]["hrtfs"]:
        paths.add(Path(ir["hrtf_path"]))
    for source in manifest["sources"]:
        paths.add(Path(source["audio_path"]))
        if "directivity_path" in source:
            paths.add(Path(source["directivity_path"]))
    for layer in manifest.get("background_noise", {}).get("layers", []):
        if "path" in layer:
            paths.add(Path(layer["path"]))
    return paths


def _number(value: object) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def validate_static_manifest(manifest: dict, config: AppConfig, scene_index: int) -> None:
    """Validate persisted realizations independently of stored plan hashes."""
    _finite_tree(manifest)
    scene_id = f"{config.outputs.naming.scene_id_prefix}_{scene_index + 1:04d}"
    expected = {"schema_version": "3.0", "scene_type": "static", "scene_id": scene_id,
                "project_name": config.experiment.experiment_id,
                "job_id": f"render_job_static_{scene_index + 1:04d}"}
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Invalid {key}: expected {value!r}")
    geometry = manifest["room"]["geometry"]
    if not _number(geometry["height_m"]) or geometry["height_m"] <= 0:
        raise ValueError("Invalid room height")
    validate_footprint(geometry["footprint_vertices_m"])
    vertices = geometry["footprint_vertices_m"]
    expected_vertices = {"shoebox": 4, "trapezoid": 4, "l_shape": 6}.get(geometry.get("type"))
    if (expected_vertices is None or len(vertices) != expected_vertices or signed_polygon_area(vertices) <= 1e-8
            or abs(min(point[0] for point in vertices)) > 1e-6
            or abs(min(point[1] for point in vertices)) > 1e-6):
        raise ValueError("Footprint must match its type, be normalized and counter-clockwise")
    if geometry["wall_ids"] != canonical_wall_ids(geometry["footprint_vertices_m"]):
        raise ValueError("Invalid wall identities")
    acoustic = get_acoustic_geometry(manifest["room"])
    ids = set(acoustic["surfaces"])
    room = manifest["room"]
    if any(set(room[key]) != ids for key in ("materials", "material_files", "acoustic_surfaces")):
        raise ValueError("Surface inventory mismatch")
    for surface_id, surface in room["acoustic_surfaces"].items():
        if not math.isclose(surface["surface_area_m2"], acoustic["surfaces"][surface_id]["area_m2"], rel_tol=1e-9):
            raise ValueError(f"Invalid surface area: {surface_id}")
        base, treatment = surface["base_material"], surface["treatment"]
        if (treatment.get("preset_id") not in {"none", "broadband_light", "broadband_medium", "broadband_strong"}
                or type(treatment.get("catalog_version")) is not int or treatment["catalog_version"] != 1):
            raise ValueError("Invalid treatment identity/version")
        if base["material_id"] != room["materials"][surface_id] or any(
            base[key] != room["material_files"][surface_id][key] for key in ("material_id", "material_path")
        ):
            raise ValueError(f"Material identity mismatch: {surface_id}")
        coverage = treatment["coverage"]
        if not _number(coverage) or not 0 <= coverage <= 1:
            raise ValueError("Invalid treatment coverage")
        if surface_id == "floor" and (coverage != 0 or treatment["preset_id"] != "none"):
            raise ValueError("Floor cannot receive virtual treatment")
        if not math.isclose(treatment["treated_area_m2"], surface["surface_area_m2"] * coverage, rel_tol=1e-9, abs_tol=1e-12):
            raise ValueError("Invalid treated area")
        vectors = [base["absorption"], base["scattering"], treatment["absorption"],
                   surface["effective_absorption"], surface["effective_scattering"]]
        if any(not isinstance(v, list) or len(v) != 31 or any(not _number(x) or not 0 <= x <= 1 for x in v) for v in vectors):
            raise ValueError("Invalid acoustic coefficients")
        if surface["effective_scattering"] != base["scattering"] or any(
            not math.isclose(e, (1 - coverage) * b + coverage * t, rel_tol=1e-9, abs_tol=1e-12)
            for b, t, e in zip(base["absorption"], treatment["absorption"], surface["effective_absorption"], strict=True)
        ):
            raise ValueError("Inconsistent effective coefficients")
    sources = manifest["sources"]
    if not isinstance(sources, list) or not sources or len({s["source_id"] for s in sources}) != len(sources):
        raise ValueError("Invalid source inventory")
    for pose in [manifest["receiver"], *sources]:
        position, orientation = pose["position_m"], pose["orientation_deg"]
        if not isinstance(position, list) or len(position) != 3 or not all(_number(v) for v in position):
            raise ValueError("Invalid position")
        if set(orientation) != {"yaw", "pitch", "roll"} or not all(_number(v) for v in orientation.values()):
            raise ValueError("Invalid orientation")
    if not sampler._is_valid_scene(config, room, manifest["receiver"], sources):
        raise ValueError("Invalid scene geometry/poses")
    for source in sources:
        if not _number(source["gain_db"]) or not _number(source["start_time_s"]) or source["start_time_s"] < 0:
            raise ValueError("Invalid source gain/timing")
    sampling = manifest["sampling"]
    if not _integer(sampling["effective_seed"]) or not _integer(sampling["scene_attempt"], 1) or not _integer(sampling["receiver_attempt"], 1):
        raise ValueError("Invalid integer sampling identity")
    rev = manifest["reverberation_sampling"]
    if type(rev["effective_seed"]) is not int or rev["effective_seed"] != sampling["effective_seed"] or type(rev["scene_index"]) is not int or rev["scene_index"] != scene_index:
        raise ValueError("Sampling seed/index mismatch")
    if sampling["effective_seed"] != sampler._derive_stable_seed(config.experiment.random_seed, f"scene:{scene_index}"):
        raise ValueError("Sampling seed mismatch")
    if manifest["render"]["seed"] != config.experiment.random_seed or type(manifest["render"]["seed"]) is not int:
        raise ValueError("Renderer seed mismatch")
    for path in _references(manifest):
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"Missing/invalid selected dependency: {path}")


def _replay(
    config: AppConfig, manifest: dict, index: int, path: Path, noise: dict,
    sample_scene: Callable[..., dict] | None = None,
) -> dict:
    attempt = manifest["sampling"]["scene_attempt"]
    if attempt > config.scene_validation.max_scene_attempts:
        raise ValueError("Persisted sampling attempt exceeds configured budget")
    replay_config = config.model_copy(deep=True)
    # Bound replay to this exact proposal. Replaying the remainder of the
    # original fallback reservoir can choose a different, later realization.
    replay_config.scene_validation.max_scene_attempts = attempt
    sample_scene = sample_scene or sampler.sample_static_scene
    sampled = sample_scene(replay_config, None, index, start_scene_attempt=attempt)
    sampled["background_noise"] = noise
    return build_static_manifest(config, sampled, index, path)


def inspect_static_plan(
    config: AppConfig, layout: ArtifactLayout | None = None, *,
    sample_scene: Callable[..., dict] | None = None,
) -> StaticPlan:
    """Inspect the entire batch without writing; aggregate protected conflicts.

    Sampling failures are returned separately from compatibility conflicts. A
    legacy failure journal is retained verbatim as history, including failures
    whose holes are successfully filled by this inspection.

    Config/catalog changes authorize a fresh batch only with overwrite enabled.
    A changed generation implementation additionally requires resume=false.
    Existing corrupt plan records never become trusted via overwrite permission.
    ``sample_scene`` is used for generation and exact persisted-attempt replay.
    """
    layout = layout or resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    sample_scene = sample_scene or sampler.sample_static_scene
    plan_path = layout["render_index_path"].parent / "static_plan.json"
    failures_path = layout["sampling_failures_path"]
    preconditions: dict[Path, tuple[int, int, str] | None] = {}
    conflicts: list[str] = []
    conflict_causes: list[BaseException] = []

    def observe(path: Path) -> None:
        preconditions[path] = _snapshot(path)

    observe(plan_path)
    observe(failures_path)
    catalog = static_catalog(config)
    contract = {"config": normalized_static_config(config), "generation": _generation_contract(), "catalog": catalog}
    contract_hash = _hash(contract)
    previous: dict | None = None
    fresh_requested = config.execution.overwrite_existing and not config.execution.resume_if_possible
    regeneration_reason: str | None = "explicit_fresh_run" if fresh_requested else None
    history: list[dict] = []
    if plan_path.exists():
        try:
            previous = _loads(plan_path.read_text(encoding="utf-8"))
            if type(previous.get("schema_version")) is not int or previous["schema_version"] != PLAN_VERSION:
                raise ValueError("Unsupported static plan version")
            if any(not isinstance(previous.get(key), dict) for key in ("contract", "scenes", "selected_dependencies")):
                raise ValueError("Invalid static plan inventories")
            if not isinstance(previous.get("sampling_failure_history"), list):
                raise ValueError("Invalid static plan sampling history")
            if previous.get("contract_hash") != _hash(previous["contract"]):
                raise ValueError("Invalid static plan contract hash")
            if previous.get("contract_hash") != contract_hash or previous.get("contract") != contract:
                generation_changed = previous["contract"].get("generation") != contract["generation"]
                if generation_changed and not fresh_requested:
                    raise ValueError("Generation contract changed; requires resume=false and overwrite=true")
                if not config.execution.overwrite_existing:
                    raise ValueError("Configuration, catalog or generation contract changed")
                regeneration_reason = "generation_contract_changed" if generation_changed else "functional_contract_changed"
        except (ValueError, OSError) as exc:
            conflicts.append(f"{plan_path}: {exc}")
            conflict_causes.append(exc.__cause__ or exc)
    if failures_path.exists():
        try:
            history = [_loads(line) for line in failures_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if any(not _integer(row.get("scene_index")) for row in history):
                raise ValueError("Invalid sampling failure scene index")
        except (ValueError, OSError) as exc:
            conflicts.append(f"{failures_path}: {exc}")
            conflict_causes.append(exc.__cause__ or exc)
    noise_plan = sampler.build_background_noise_plan(config, config.execution.num_simulations)
    manifests: list[PlannedManifest] = []
    failures: list[dict] = []
    records: dict = {}
    failed_records: dict = deepcopy(previous.get("failed_scenes", {})) if previous and not regeneration_reason else {}
    dependencies: dict[str, dict] = deepcopy(previous.get("selected_dependencies", {})) if previous and not regeneration_reason else {}
    expected_paths: set[Path] = set()
    for index in range(config.execution.num_simulations):
        scene_id = f"{config.outputs.naming.scene_id_prefix}_{index + 1:04d}"
        path = layout["scene_manifest_dir"] / f"{scene_id}.json"
        expected_paths.add(path)
        try:
            observe(path)
            exists = path.exists()
            if scene_id in failed_records:
                if exists:
                    raise ValueError("Failed scene has an unresolved manifest publication")
                failures.append(deepcopy(failed_records[scene_id]))
                continue
            if not exists and previous and not regeneration_reason and any(
                failure.get("scene_id") == scene_id and failure.get("stage") == "raven_t30"
                for failure in history
            ):
                raise ValueError("Uncommitted acoustic sampling failure; cannot renew its budget")
            if exists:
                if regeneration_reason or not config.execution.resume_if_possible:
                    if not config.execution.overwrite_existing:
                        raise ValueError("Scene exists with resume=false and overwrite=false")
                    # Explicit regeneration is allowed, but malformed inputs are
                    # still never silently migrated during a protected resume.
                    exists = False
                else:
                    manifest = _loads(path.read_text(encoding="utf-8"))
                    validate_static_manifest(manifest, config, index)
                    recorded = previous.get("scenes", {}).get(scene_id) if previous else None
                    if recorded is not None:
                        if recorded.get("functional_hash") != _hash(functional_manifest(manifest)):
                            raise ValueError("Persisted realization differs from static plan")
                        if recorded.get("sampling") != manifest["sampling"] or recorded.get("scene_index") != index:
                            raise ValueError("Invalid static plan scene identity")
                    else:
                        replayed = _replay(config, manifest, index, path, noise_plan[index], sample_scene)
                        if _json(functional_manifest(replayed)) != _json(functional_manifest(manifest)):
                            if not config.execution.overwrite_existing:
                                raise ValueError("Legacy realization cannot be replayed under current configuration")
                            exists = False
            if not exists:
                try:
                    saved = previous.get("scenes", {}).get(scene_id) if previous and config.execution.resume_if_possible and not regeneration_reason else None
                    if saved:
                        manifest = _replay(config, {"sampling": saved["sampling"]}, index, path, noise_plan[index], sample_scene)
                        if saved.get("functional_hash") != _hash(functional_manifest(manifest)):
                            raise ValueError("Missing scene cannot be replayed from recorded realization")
                        manifest["reverberation_sampling"].update(deepcopy(saved.get("reverberation_history", {})))
                    else:
                        sampled = sample_scene(config, None, index)
                        sampled["background_noise"] = noise_plan[index]
                        manifest = build_static_manifest(config, sampled, index, path)
                except sampler.SceneSamplingFailure as exc:
                    if path.exists():
                        raise ValueError("Cannot replace existing scene: authorized regeneration failed sampling") from exc
                    failure = {**exc.diagnostics, "scene_id": scene_id, "failure_record_path": failures_path.as_posix()}
                    failures.append(failure)
                    if failure not in history:
                        history.append(failure)
                    continue
                validate_static_manifest(manifest, config, index)
            manifests.append(PlannedManifest(manifest, path, not exists, index))
            records[scene_id] = _scene_record(manifest, index)
            for dependency in _references(manifest):
                observe(dependency)
                snapshot = preconditions[dependency]
                assert snapshot is not None
                key = dependency.as_posix()
                old = previous.get("selected_dependencies", {}).get(key) if previous and not regeneration_reason else None
                if exists and previous and scene_id in previous.get("scenes", {}) and old is None:
                    raise ValueError(f"Missing selected dependency fingerprint in static plan: {dependency}")
                if old is not None and (old.get("sha256") != snapshot[2] or old.get("size_bytes") != snapshot[0]):
                    raise ValueError(f"Selected dependency changed: {dependency}")
                dependencies[key] = old or {"sha256": snapshot[2], "size_bytes": snapshot[0],
                    "provenance": "observed_at_adoption" if exists else "observed_at_generation"}
        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
            conflicts.append(f"{path}: {exc}")
            conflict_causes.append(exc.__cause__ or exc)
    # Stray manifests can indicate a changed scene count/prefix in a legacy run.
    retired = set(previous.get("retired_scene_paths", [])) if previous else set()
    for path in sorted(layout["scene_manifest_dir"].glob("*.json")):
        if path not in expected_paths:
            if regeneration_reason or path.as_posix() in retired:
                observe(path)
                retired.add(path.as_posix())
            else:
                conflicts.append(f"{path}: Unexpected scene outside configured batch")
    if conflicts:
        raise StaticPlanConflict(conflicts) from (conflict_causes[0] if conflict_causes else None)
    metadata: dict = {"schema_version": PLAN_VERSION, "contract": contract, "contract_hash": contract_hash,
                "adoption": previous.get("adoption", "legacy_replay") if previous else
                    ("legacy_replay" if any(not m.needs_write for m in manifests) else "new"),
                 "scenes": records, "selected_dependencies": dependencies,
                 "failed_scenes": failed_records,
                "sampling_failure_history": history}
    if previous and "scene_revision_history" in previous:
        metadata["scene_revision_history"] = previous["scene_revision_history"]
    if retired:
        metadata["retired_scene_paths"] = sorted(retired - {path.as_posix() for path in expected_paths})
    if previous and "plan_revision_history" in previous:
        metadata["plan_revision_history"] = deepcopy(previous["plan_revision_history"])
    if previous and regeneration_reason:
        old_plan = deepcopy(previous)
        old_plan.pop("plan_revision_history", None)
        metadata.setdefault("plan_revision_history", []).append(
            {"reason": regeneration_reason, "previous_plan": old_plan})
        metadata["adoption"] = "authorized_regeneration"
    # Preserve history already captured by a plan even if the legacy journal
    # was compacted by an older coordinator.
    if previous:
        for failure in previous.get("sampling_failure_history", []):
            if failure not in history:
                history.append(failure)
    return StaticPlan(manifests, failures, history, preconditions, metadata, plan_path, failures_path,
                      previous != metadata, not failures_path.exists() or
                      failures_path.read_text(encoding="utf-8") != _failure_text(history), config, catalog,
                      layout["scene_manifest_dir"])


def revalidate_static_plan(plan: StaticPlan) -> None:
    conflicts = []
    for item in plan.manifests:
        try:
            validate_static_manifest(item.manifest, plan.config, item.scene_index)
            record = plan.metadata["scenes"][item.manifest["scene_id"]]
            if record["functional_hash"] != _hash(functional_manifest(item.manifest)):
                conflicts.append(f"{item.path}: Planned realization changed after preflight")
        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
            conflicts.append(f"{item.path}: {exc}")
    for path, expected in plan.preconditions.items():
        try:
            if _snapshot(path) != expected:
                conflicts.append(f"{path}: File changed after preflight")
        except (OSError, ValueError) as exc:
            conflicts.append(f"{path}: {exc}")
    if static_catalog(plan.config) != plan.catalog:
        conflicts.append("Sampling catalog changed after preflight")
    for path in plan.scene_manifest_dir.glob("*.json"):
        if path not in plan.preconditions:
            conflicts.append(f"{path}: Unexpected scene appeared after preflight")
    if _generation_contract() != plan.metadata["contract"]["generation"] or normalized_static_config(plan.config) != plan.metadata["contract"]["config"]:
        conflicts.append("Generation/configuration changed after preflight")
    if conflicts:
        raise StaticPlanConflict(conflicts)


def _failure_text(history: list[dict]) -> str:
    return "".join(_json(row) + "\n" for row in history)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def static_writer_lock(layout: ArtifactLayout | Path) -> Iterator[StaticWriterLock]:
    """Exclusive batch writer; never infer that an existing lock is stale."""
    root = layout if isinstance(layout, Path) else layout["run_root"]
    path = root / "metadata" / "indexes" / "static_writer.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(_json({"version": 1, "token": token, "pid": os.getpid()}))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise StaticPlanConflict([f"{path}: Writer lock exists; owner may still be writing. Manual intervention required."]) from exc
    lock = StaticWriterLock(path, token)
    try:
        yield lock
    finally:
        # A replaced/corrupt lock is uncertain; do not unlink somebody else's.
        if path.exists() and _loads(path.read_text(encoding="utf-8")).get("token") == token:
            path.unlink()


def apply_static_plan(plan: StaticPlan, *, lock: StaticWriterLock) -> list[Path]:
    """Publish after caller's artifact validation, with all preconditions checked.

    The lock must remain held for subsequent runtime/index/output writes too.
    Publication is individually atomic for metadata, not a multi-file transaction;
    interruption leaves deterministic scene holes for the next inspection.
    """
    expected_lock = plan.plan_path.parent / "static_writer.lock"
    if lock.path != expected_lock or _loads(lock.path.read_text(encoding="utf-8")).get("token") != lock.token:
        raise StaticPlanConflict(["Batch writer lock not held"])
    revalidate_static_plan(plan)
    for item in plan.manifests:
        if item.needs_write:
            write_manifest(item.manifest, item.path, overwrite=plan.config.execution.overwrite_existing)
    if plan.needs_failures_write:
        _atomic_text(plan.failures_path, _failure_text(plan.failure_history))
    if plan.needs_plan_write:
        _atomic_text(plan.plan_path, json.dumps(plan.metadata, indent=2, sort_keys=True, allow_nan=False))
    return [item.path for item in plan.manifests]


def record_static_manifest_revision(
    config: AppConfig, manifest: dict, manifest_path: Path, *, lock: StaticWriterLock,
    reason: str = "raven_t30_resampling",
) -> None:
    """Record a caller-authorized new realization after acoustic resampling.

    This does not write the scene or authorize its replacement. The coordinator
    owns that decision and must hold the batch lock, publish the new scene, and
    call this helper before the batch can be resumed. A crash between those
    publications is deliberately an explicit conflict, not silent adoption.
    Previous realization records remain in ``scene_revision_history``.
    """
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    plan_path = layout["render_index_path"].parent / "static_plan.json"
    if lock.path != plan_path.parent / "static_writer.lock" or _loads(lock.path.read_text(encoding="utf-8")).get("token") != lock.token:
        raise StaticPlanConflict(["Batch writer lock not held"])
    metadata = _loads(plan_path.read_text(encoding="utf-8"))
    contract = {"config": normalized_static_config(config), "generation": _generation_contract(), "catalog": static_catalog(config)}
    if metadata.get("schema_version") != PLAN_VERSION or metadata.get("contract_hash") != _hash(contract) or metadata.get("contract") != contract:
        raise StaticPlanConflict([f"{plan_path}: Configuration, catalog or generation contract changed"])
    index = manifest["reverberation_sampling"]["scene_index"]
    validate_static_manifest(manifest, config, index)
    if manifest_path != layout["scene_manifest_dir"] / f"{manifest['scene_id']}.json":
        raise StaticPlanConflict([f"{manifest_path}: Unexpected scene path"])
    if _loads(manifest_path.read_text(encoding="utf-8")) != manifest:
        raise StaticPlanConflict([f"{manifest_path}: Revision must match published scene"])
    old = metadata["scenes"].get(manifest["scene_id"])
    record = _scene_record(manifest, index)
    if old == record:
        return
    if old is not None:
        metadata.setdefault("scene_revision_history", []).append(
            {"scene_id": manifest["scene_id"], "reason": reason, "previous": old, "replacement": record})
    metadata["scenes"][manifest["scene_id"]] = record
    for dependency in _references(manifest):
        fingerprint = {"sha256": _file_hash(dependency), "size_bytes": dependency.stat().st_size,
                       "provenance": "observed_at_generation"}
        old_fingerprint = metadata["selected_dependencies"].get(dependency.as_posix())
        if old_fingerprint and any(old_fingerprint.get(key) != fingerprint[key] for key in ("sha256", "size_bytes")):
            raise StaticPlanConflict([f"{dependency}: Selected dependency changed during resampling"])
        metadata["selected_dependencies"].setdefault(dependency.as_posix(), fingerprint)
    _atomic_text(plan_path, json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False))


def record_static_scene_failure(
    config: AppConfig, scene_id: str, failure: dict, *, lock: StaticWriterLock,
) -> None:
    """Persist exhausted acoustic sampling as a terminal plan state, not a hole."""
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)
    path = layout["render_index_path"].parent / "static_plan.json"
    if lock.path != path.parent / "static_writer.lock" or _loads(lock.path.read_text(encoding="utf-8")).get("token") != lock.token:
        raise StaticPlanConflict(["Batch writer lock not held"])
    metadata = _loads(path.read_text(encoding="utf-8"))
    prior = metadata["scenes"].pop(scene_id, None)
    metadata.setdefault("failed_scenes", {})[scene_id] = deepcopy(failure)
    if prior is not None:
        metadata.setdefault("scene_revision_history", []).append(
            {"scene_id": scene_id, "reason": "raven_t30_exhausted", "previous": prior, "replacement": None})
    if failure not in metadata["sampling_failure_history"]:
        metadata["sampling_failure_history"].append(deepcopy(failure))
    _atomic_text(path, json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False))
