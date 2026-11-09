"""Deferred acoustic continuations: evidence first, one shared plan per batch.

Intent recording requires the render writer lock. Planning is read-only and
never samples during shutdown. Publication is a separate, globally gated step.
"""

import base64
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from acoustic_orchestrator.experiment.sampler import SceneSamplingFailure, sample_static_scene
from acoustic_orchestrator.experiment.scene_builder import build_static_manifest
from acoustic_orchestrator.pipeline.output_index import build_planned_variant_record, load_variant_index
from acoustic_orchestrator.pipeline.persistence_journal import atomic_write, json_bytes
from acoustic_orchestrator.pipeline.process_retries import RetryBlockedError, inspect_chain
from acoustic_orchestrator.pipeline.render_attempt import _json_number, read_json_object, sha256_file
from acoustic_orchestrator.pipeline.static_plan import (
    _hash as _static_hash, _loads as _static_loads, _generation_contract, normalized_static_config,
    project_static_manifest_revision, static_catalog,
)
from acoustic_orchestrator.pipeline.artifact_view import ArtifactView
from acoustic_orchestrator.pipeline.stop_control import StopControl
from acoustic_orchestrator.pipeline.render_attempt import inspect_completion
from acoustic_orchestrator.pipeline.render_validation import equivalent_render_inputs, validate_render
from acoustic_orchestrator.pipeline.raven_resources import is_private_runtime, verify_resource_inventory


def _hash(record: dict) -> str:
    # Use the same exact-number normalization as the durable evidence reader.
    return _static_hash(json.loads(json_bytes(record), parse_float=_json_number))


def _encoded(data: bytes | None) -> str | None:
    return None if data is None else base64.b64encode(data).decode("ascii")


def _decoded(data: str | None) -> bytes | None:
    return None if data is None else base64.b64decode(data, validate=True)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise RetryBlockedError(f"Non-regular acoustic evidence: {path}")
    return {"path": path.resolve().as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _assert_file(item: dict, path: Path | None = None) -> None:
    actual = _file(path or Path(item["path"]))
    if any(actual[key] != item[key] for key in ("size_bytes", "sha256")):
        raise RetryBlockedError(f"Acoustic evidence changed: {path or item['path']}")


def _require_lock(layout: dict, lock) -> None:
    path = layout["render_index_path"].parent / "static_writer.lock"
    if lock.path != path or read_json_object(path).get("token") != lock.token:
        raise RetryBlockedError("Acoustic continuation requires the batch writer lock")


def record_intent(layout: dict, scene, diagnostic: dict, *, lock) -> None:
    """Record a rejected, resolved attempt; do not sample or move its files."""
    _require_lock(layout, lock)
    task = scene.canonical_task
    attempt = task["reserved_attempt"]
    chain = inspect_chain(task["runtime_manifest_path"], layout["render_attempts_dir"].parent / "process_chains")
    if chain is None:
        raise RetryBlockedError("Acoustic continuation has no known chain")
    outcome = chain.verify_resolved()
    if (outcome["attempt_id"] != attempt.attempt_id or outcome.get("decision") != "retry_acoustic"
            or outcome.get("retry_classification") != "acoustic_rejection"):
        raise RetryBlockedError("Acoustic continuation requires a resolved acoustic rejection")
    _write_intent(layout, scene, diagnostic, attempt, chain)


def _write_intent(layout, scene, diagnostic, attempt, chain):
    files = set(Path(path) for path in scene.prepared_source_paths.values())
    variants = []
    absent = []
    for prepared in scene.prepared_tasks:
        paths = prepared["variant_paths"]
        variants.append(deepcopy(paths))
        absent.extend(str(Path(paths[field]).resolve()) for field in ("runtime_manifest_path", "rendered_wav_path", "render_metadata_path")
                      if paths.get(field) and not Path(paths[field]).exists())
        files.update(Path(paths[field]) for field in ("runtime_manifest_path", "rendered_wav_path", "render_metadata_path")
                     if paths.get(field) and Path(paths[field]).exists())
    guards = [_file(attempt.outcome_path), _file(chain.path), _file(attempt.snapshot_path)]
    for path in (attempt.completion_path, attempt.failure_path):
        if path.exists():
            guards.append(_file(path))
    plan_path = layout["render_index_path"].parent / "static_plan.json"
    record = {"schema_version": 1, "action": "defer_acoustic", "attempt_id": attempt.attempt_id,
              "chain_id": attempt.record["chain_id"], "scene_id": scene.scene_id, "scene_index": scene.scene_index,
              "manifest_path": scene.manifest_path.resolve().as_posix(),
              "manifest_before": _encoded(scene.manifest_path.read_bytes()),
              "plan_observed_sha256": sha256_file(plan_path), "diagnostic": diagnostic,
              "next_scene_attempt": int(scene.scene_manifest["reverberation_sampling"]["attempts"]) + 1,
              "variants": variants, "files": [_file(path) for path in sorted(files)], "guards": guards,
              "absent": absent}
    record["intent_sha256"] = _hash(record)
    path = attempt.directory / "acoustic_continuation" / "record.json"
    if path.exists():
        if read_json_object(path) != record:
            raise RetryBlockedError(f"Acoustic intent conflict: {path}")
        return
    path.parent.mkdir(exist_ok=True)
    atomic_write(path, json_bytes(record))


def record_observed_intent(layout: dict, entry: dict, *, lock) -> None:
    """Close a T30 continuation inside its already-verified recovery batch.

    The batch's final marker is deliberately still pending; only its completed
    per-attempt journal and exact active targets authorize this registration.
    """
    from types import SimpleNamespace
    from acoustic_orchestrator.pipeline.persistence_journal import read_journal
    from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt
    from acoustic_orchestrator.pipeline.process_retries import RetryChain

    _require_lock(layout, lock)
    outcome_path, chain_path = Path(entry["outcome_path"]), Path(entry["chain_path"])
    journal = read_journal(outcome_path)
    target = entry["targets"]
    if (journal["state"] != "complete" or journal["targets"] != target
            or read_json_object(outcome_path) != target["outcome"] or read_json_object(chain_path) != target["chain"]
            or target["outcome"]["retry_classification"] != "acoustic_rejection"):
        raise RetryBlockedError("Observed acoustic continuation is not durably resolved")
    context = entry["acoustic_context"]
    scene_path = Path(context["manifest_path"])
    if read_json_object(scene_path) != context["scene"]:
        raise RetryBlockedError("Observed acoustic scene changed")
    attempt = RenderAttempt.from_record(outcome_path, target["outcome"])
    scene = SimpleNamespace(scene_id=target["outcome"]["scene_id"], scene_index=context["scene_index"],
                            scene_manifest=context["scene"], manifest_path=scene_path,
                            prepared_source_paths=context["prepared_source_paths"],
                            prepared_tasks=[{"variant_paths": paths} for paths in context["variants"]])
    diagnostic = {**target["outcome"]["validation"]["acoustic_rejection"],
                  "metadata_path": entry["paths"]["render_metadata_path"], "scene_index": scene.scene_index,
                  "scene_id": scene.scene_id, "output_type": target["outcome"]["output_type"],
                  "render_status": target["index"]["status"],
                  "sampling_attempt": scene.scene_manifest["reverberation_sampling"]["attempts"]}
    _write_intent(layout, scene, diagnostic, attempt, RetryChain(chain_path, target["chain"]))


def _read_intent(path: Path) -> dict:
    record = read_json_object(path)
    if (record.get("schema_version") != 1 or record.get("action") != "defer_acoustic"
            or record.get("attempt_id") != path.parent.parent.name
            or record.get("intent_sha256") != _hash({k: v for k, v in record.items() if k != "intent_sha256"})):
        raise RetryBlockedError(f"Invalid acoustic intent: {path}")
    return record


@dataclass
class AcousticTransaction:
    path: Path
    plan: dict

    @property
    def participants(self) -> list[dict]:
        return self.plan["participants"]

    def position(self, config, layout: dict) -> int:
        """Recognize an exact publication prefix, not arbitrary per-file states."""
        root = layout["run_root"].resolve()
        if (self.plan.get("root") != root.as_posix() or self.plan.get("transaction_id") != self.path.parent.name
                or self.path.parent.parent != root / "metadata" / "acoustic_transactions"
                or self.plan.get("plan_sha256") != _hash({k: v for k, v in self.plan.items() if k != "plan_sha256"})):
            raise RetryBlockedError("Acoustic transaction identity or digest conflict")
        expected, current = {}, {}
        writes = set()
        for operation in self.plan["operations"]:
            path = Path(operation["path"])
            if not path.is_absolute() or not path.resolve().is_relative_to(root):
                raise RetryBlockedError("Acoustic operation outside run")
            if path.as_posix() in writes:
                raise RetryBlockedError("Overlapping acoustic operations")
            writes.add(path.as_posix())
            if operation["kind"] == "move":
                destination = Path(operation["destination"])
                if not destination.resolve().is_relative_to(self.path.parent / "preserved") or destination.as_posix() in expected:
                    raise RetryBlockedError("Acoustic preservation destination conflict")
                expected[path.as_posix()] = {k: operation[k] for k in ("size_bytes", "sha256")}
                expected[destination.as_posix()] = None
            elif operation["kind"] == "write":
                expected[path.as_posix()] = _fingerprint(_decoded(operation["before"]))
            else:
                raise RetryBlockedError("Unknown acoustic operation")
        for name in expected:
            path = Path(name)
            current[name] = {k: _file(path)[k] for k in ("size_bytes", "sha256")} if path.exists() else None
        matches = [0] if current == expected else []
        for number, operation in enumerate(self.plan["operations"], 1):
            key = Path(operation["path"]).as_posix()
            if operation["kind"] == "move":
                expected[Path(operation["destination"]).as_posix()] = expected[key]
                expected[key] = None
            else:
                expected[key] = _fingerprint(_decoded(operation["after"]))
            if current == expected:
                matches.append(number)
        if not matches:
            raise RetryBlockedError("Acoustic transaction publication prefix conflict")
        for guard in self.plan["guards"]:
            if not Path(guard["path"]).resolve().is_relative_to(root):
                raise RetryBlockedError("Acoustic evidence outside run")
            _assert_file(guard)
        participants = self.participants
        if len({p["intent"]["scene_id"] for p in participants}) != len(participants):
            raise RetryBlockedError("Ambiguous acoustic transaction participants")
        for participant in participants:
            intent = _read_intent(Path(participant["intent_path"]))
            if intent != participant["intent"]:
                raise RetryBlockedError("Acoustic participant changed")
            for name in intent.get("absent", []):
                if Path(name).exists():
                    raise RetryBlockedError(f"Uninventoried acoustic artifact: {name}")
        plan_operation = self.plan["operations"][-1]
        before = json.loads(_decoded(plan_operation["before"]))
        contract = {"config": normalized_static_config(config), "generation": _generation_contract(), "catalog": static_catalog(config)}
        if before.get("contract") != contract or before.get("contract_hash") != _static_hash(contract):
            raise RetryBlockedError("Acoustic transaction configuration changed")
        return max(matches)

    def view(self, config, layout: dict) -> ArtifactView:
        self.position(config, layout)
        result = ArtifactView()
        for operation in self.plan["operations"]:
            path = Path(operation["path"])
            if operation["kind"] == "write":
                result.contents[path.as_posix()] = _decoded(operation["before"])
            else:
                result.locations[path.as_posix()] = str(path if path.exists() else Path(operation["destination"]))
        return result

    def has_scene(self, scene_id: str) -> bool:
        return any(p["intent"]["scene_id"] == scene_id for p in self.participants)

    def preflight(self, config, layout: dict, *, lock, stop=None):
        """Validate the complete original logical batch, including participants."""
        from acoustic_orchestrator.pipeline.static_plan import inspect_static_plan
        from acoustic_orchestrator.pipeline.render_pipeline import _preflight_render_artifacts

        _require_lock(layout, lock)
        view = self.view(config, layout)
        plan = inspect_static_plan(config, layout, view=view)
        repairs = _preflight_render_artifacts(config, [item.manifest for item in plan.manifests],
                                               allow_repairs=True, stop=stop, acoustic=self, view=view)
        return plan, repairs

    def publish(self, config, layout: dict, *, lock, stop=None) -> bool:
        """Complete only this preflighted plan, never resample on re-entry."""
        _require_lock(layout, lock)
        state_path = self.path.with_name("state.json")
        pending = {"schema_version": 1, "state": "pending", "plan_sha256": self.plan["plan_sha256"]}
        completed = {**pending, "state": "complete"}
        state = read_json_object(state_path) if state_path.exists() else None
        if state is not None and not self.path.exists():
            raise RetryBlockedError("Acoustic transaction plan missing")
        if state not in (None, pending, completed):
            raise RetryBlockedError("Acoustic transaction state conflict")
        if self.path.exists() and read_json_object(self.path) != self.plan:
            raise RetryBlockedError("Acoustic transaction plan changed")
        if state == completed:
            for participant in self.participants:
                self._check_link(participant, required_state="complete")
            return False
        if stop is not None:
            stop.checkpoint()
        self.preflight(config, layout, lock=lock, stop=stop)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            atomic_write(self.path, json_bytes(self.plan))
        if state is None:
            atomic_write(state_path, json_bytes(pending))
        for participant in self.participants:
            self._recheck(config, layout, lock, stop)
            link_path, link = self._check_link(participant)
            if not link_path.exists():
                atomic_write(link_path, json_bytes(link))
        for operation in self.plan["operations"][self.position(config, layout):]:
            self._recheck(config, layout, lock, stop)
            _apply_operation(operation)
        self._recheck(config, layout, lock, stop)
        if self.position(config, layout) != len(self.plan["operations"]):
            raise RetryBlockedError("Acoustic transaction did not reach its target")
        for participant in self.participants:
            self._recheck(config, layout, lock, stop)
            link_path, link = self._check_link(participant)
            final_link = {**link, "state": "complete"}
            if read_json_object(link_path) != final_link:
                atomic_write(link_path, json_bytes(final_link))
        self._recheck(config, layout, lock, stop)
        atomic_write(state_path, json_bytes(completed))
        return True

    def _check_link(self, participant, *, required_state=None):
        path = Path(participant["intent_path"]).with_name("transaction.json")
        link = {"schema_version": 1, "path": self.path.resolve().as_posix(),
                "plan_sha256": self.plan["plan_sha256"], "state": "pending"}
        if path.exists():
            current = read_json_object(path)
            if current not in (link, {**link, "state": "complete"}) or (required_state and current["state"] != required_state):
                raise RetryBlockedError(f"Acoustic participant link conflict: {path}")
        elif required_state:
            raise RetryBlockedError(f"Acoustic participant link missing: {path}")
        return path, link

    def _recheck(self, config, layout, lock, stop) -> None:
        if stop is not None:
            stop.checkpoint()
        _require_lock(layout, lock)
        if read_json_object(self.path) != self.plan:
            raise RetryBlockedError("Acoustic transaction plan changed during publication")
        self.preflight(config, layout, lock=lock, stop=stop)
        position = self.position(config, layout)
        for participant in self.participants:
            path, _ = self._check_link(participant)
            if path.exists() and read_json_object(path)["state"] == "complete" and position != len(self.plan["operations"]):
                raise RetryBlockedError("Acoustic participant completed before target publication")

    def authorizes_chain(self, path: Path, chain: dict) -> bool:
        for participant in self.participants:
            intent = participant["intent"]
            if chain["chain_id"] == intent["chain_id"]:
                guard = next(g for g in intent["guards"] if Path(g["path"]) == path)
                _assert_file(guard)
                return (chain["state"] == "not_retryable" and chain["attempts"][-1]["state"] == "resolved"
                        and chain["attempts"][-1]["classification"] == "acoustic_rejection"
                        and chain["attempts"][-1]["attempt_id"] == intent["attempt_id"])
        return False

    def validate_variant(self, config, layout: dict, paths: dict, expected: dict, *, canonical: bool, view: ArtifactView) -> None:
        from acoustic_orchestrator.pipeline.process_retries import preflight_chain
        participant = next(p for p in self.participants if p["intent"]["scene_id"] == paths["scene_id"])
        intent = participant["intent"]
        original = next((v for v in intent["variants"] if v["variant_id"] == paths["variant_id"]), None)
        if original != paths:
            raise RetryBlockedError("Acoustic variant inventory conflict")
        runtime = Path(paths["runtime_manifest_path"])
        if view.exists(runtime):
            observed = read_json_object(view.physical(runtime))
            if not equivalent_render_inputs(expected, observed):
                raise RetryBlockedError("Acoustic runtime inputs conflict")
            if is_private_runtime(observed) and (Path(observed["base_rpf_file"]).parent / "writer.lock").exists():
                raise RetryBlockedError("Acoustic private writer lock exists")
        else:
            observed = None
        if canonical:
            chain = inspect_chain(runtime, layout["render_attempts_dir"].parent / "process_chains",
                                  input_sha256=next(g["sha256"] for g in intent["guards"] if Path(g["path"]).name == "input_manifest.json"))
            if chain is None or not self.authorizes_chain(chain.path, chain.record):
                raise RetryBlockedError("Acoustic continuation chain conflict")
            outcome = chain.verify_resolved()
            if is_private_runtime(observed):
                verify_resource_inventory(outcome, observed)
            completion = inspect_completion(outcome, resolve_file=view.physical)
            validation = validate_render(paths, metadata_required=True, canonical=True, resolve_file=view.physical)
            if (not validation.artifacts_ok or validation.acoustic_rejection is None
                    or validation.acoustic_rejection.get("reason") != intent["diagnostic"]["reason"]
                    or completion["status"] == "invalid"
                    or (outcome["exit_code"] != 0 and completion["status"] != "valid")):
                raise RetryBlockedError("Acoustic rejection evidence no longer valid")
            preflight_chain(runtime, layout["render_attempts_dir"].parent / "process_chains",
                            input_sha256=outcome["manifest_sha256"], reusable=False, overwrite_existing=False,
                            force_reset=False, acoustic=self)
        else:
            if any(view.exists(Path(paths[field])) for field in ("rendered_wav_path", "render_metadata_path") if paths.get(field)):
                raise RetryBlockedError("Acoustic continuation would replace protected variant outputs")
            # Even a variant without outputs must not hide a running lineage.
            if observed is not None:
                preflight_chain(runtime, layout["render_attempts_dir"].parent / "process_chains",
                                input_sha256=sha256_file(view.physical(runtime)), reusable=False,
                                overwrite_existing=False, force_reset=False)


def _fingerprint(data: bytes | None) -> dict | None:
    return None if data is None else {"size_bytes": len(data), "sha256": _digest(data)}


def _apply_operation(operation: dict) -> None:
    path = Path(operation["path"])
    if operation["kind"] == "move":
        destination = Path(operation["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.rename(destination)
    else:
        data = _decoded(operation["after"])
        if data is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, data)


def inspect_transaction(config, layout: dict, *, sample_scene=None,
                         deferred_observed_attempts=frozenset(), stop: StopControl | None = None) -> AcousticTransaction | None:
    """Prepare one common target in memory, or load the same durable plan."""
    if stop is not None:
        stop.checkpoint()
    root = layout["run_root"].resolve()
    directory = root / "metadata" / "acoustic_transactions"
    active = []
    for path in directory.glob("*/plan.json"):
        state_path = path.with_name("state.json")
        plan = read_json_object(path)
        if plan.get("schema_version") != 1 or plan.get("plan_sha256") != _hash({k: v for k, v in plan.items() if k != "plan_sha256"}):
            raise RetryBlockedError(f"Invalid acoustic transaction: {path}")
        state = read_json_object(state_path) if state_path.exists() else None
        if state is not None and (state.get("plan_sha256") != plan["plan_sha256"] or state.get("state") not in {"pending", "complete"}):
            raise RetryBlockedError(f"Invalid acoustic transaction state: {state_path}")
        if state is None or state["state"] != "complete":
            active.append(AcousticTransaction(path, plan))
    if len(active) > 1:
        raise RetryBlockedError("Multiple active acoustic transactions")
    if active:
        return active[0]
    intents = []
    for path in layout["render_attempts_dir"].glob("*/acoustic_continuation/record.json"):
        if path.parent.parent.name in deferred_observed_attempts:
            continue  # This exact pending observed batch validates it instead.
        if path.with_name("transaction.json").exists():
            link = read_json_object(path.with_name("transaction.json"))
            linked = Path(link["path"])
            if not linked.resolve().is_relative_to(directory) or not linked.exists():
                raise RetryBlockedError(f"Acoustic transaction link conflict: {path}")
            linked_plan = read_json_object(linked)
            state = read_json_object(linked.with_name("state.json"))
            if (link["plan_sha256"] != linked_plan.get("plan_sha256") or state.get("state") != "complete"
                    or state.get("plan_sha256") != linked_plan.get("plan_sha256") or link.get("state") != "complete"):
                raise RetryBlockedError(f"Unconfirmed acoustic transaction link: {path}")
            continue
        intents.append((path, _read_intent(path)))
    if not intents:
        return None
    intents.sort(key=lambda pair: (pair[1]["scene_index"], pair[1]["attempt_id"]))
    if len({record["scene_id"] for _, record in intents}) != len(intents):
        raise RetryBlockedError("Ambiguous acoustic continuation for the same scene")
    plan_path = layout["render_index_path"].parent / "static_plan.json"
    before = _static_loads(plan_path.read_text(encoding="utf-8"))
    contract = {"config": normalized_static_config(config), "generation": _generation_contract(), "catalog": static_catalog(config)}
    if before.get("contract") != contract or before.get("contract_hash") != _static_hash(contract):
        raise RetryBlockedError("Acoustic transaction configuration/catalog/generation conflict")
    target = deepcopy(before)
    index_path = layout["render_index_path"]
    records = load_variant_index(index_path)
    failures_path = layout["sampling_failures_path"]
    failures_before = failures_path.read_bytes() if failures_path.exists() else None
    transaction_id = uuid4().hex
    path = directory / transaction_id / "plan.json"
    participants, operations, guards = [], [], []
    sample_scene = sample_scene or sample_static_scene
    for intent_path, intent in intents:
        if stop is not None:
            stop.checkpoint()
        scene_path = Path(intent["manifest_path"])
        if not scene_path.resolve().is_relative_to(root) or scene_path.read_bytes() != _decoded(intent["manifest_before"]):
            raise RetryBlockedError(f"Acoustic scene changed: {scene_path}")
        for item in intent["guards"] + intent["files"]:
            if not Path(item["path"]).resolve().is_relative_to(root):
                raise RetryBlockedError("Acoustic inventory outside run")
            _assert_file(item)
        guards.extend(intent["guards"])
        guards.append(_file(intent_path))
        scene = json.loads(_decoded(intent["manifest_before"]))
        if intent["next_scene_attempt"] != int(scene["reverberation_sampling"]["attempts"]) + 1:
            raise RetryBlockedError("Acoustic continuation budget changed")
        rejections = [*scene["reverberation_sampling"].get("raven_t30_rejections", []), intent["diagnostic"]]
        replacement, failure = None, None
        try:
            sampled = sample_scene(config, None, intent["scene_index"], start_scene_attempt=intent["next_scene_attempt"],
                                   prior_rejections={**scene["reverberation_sampling"].get("rejections_by_reason", {}),
                                                     "raven_t30_invalid": len(rejections)})
            sampled["background_noise"] = scene.get("background_noise")
            replacement = build_static_manifest(config, sampled, intent["scene_index"], scene_path)
            replacement["reverberation_sampling"].update(raven_t30_rejections=rejections, raven_retry_count=len(rejections))
            target = project_static_manifest_revision(config, target, replacement)
        except SceneSamplingFailure as exc:
            failure = {**exc.diagnostics, "scene_id": intent["scene_id"], "stage": "raven_t30",
                       "raven_t30_rejections": rejections, "failure_record_path": failures_path.resolve().as_posix()}
            previous = target["scenes"].pop(intent["scene_id"])
            target.setdefault("failed_scenes", {})[intent["scene_id"]] = failure
            target.setdefault("scene_revision_history", []).append(
                {"scene_id": intent["scene_id"], "reason": "raven_t30_exhausted", "previous": previous, "replacement": None})
            if failure not in target["sampling_failure_history"]:
                target["sampling_failure_history"].append(failure)
        for number, item in enumerate(intent["files"]):
            operations.append({"kind": "move", **item,
                               "destination": (path.parent / "preserved" / intent["attempt_id"] / f"{number}_{Path(item['path']).name}").as_posix()})
        operations.append({"kind": "write", "path": scene_path.as_posix(), "before": intent["manifest_before"],
                           "after": _encoded(json_bytes(replacement)) if replacement is not None else None})
        for variant in intent["variants"]:
            existing = records.get(variant["variant_id"])
            if existing and existing.get("decision") in {"continue", "continue_with_warning"}:
                raise RetryBlockedError("Acoustic continuation would replace an accepted variant")
            records[variant["variant_id"]] = build_planned_variant_record(variant, metadata_required=config.execution.save_render_metadata)
        participants.append({"intent_path": intent_path.as_posix(), "intent": intent,
                             "replacement": replacement, "failure": failure})
    operations.extend([
        {"kind": "write", "path": index_path.as_posix(), "before": _encoded(index_path.read_bytes() if index_path.exists() else None),
         "after": _encoded(b"".join((json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8") for record in records.values()))},
        {"kind": "write", "path": failures_path.as_posix(), "before": _encoded(failures_before),
         "after": _encoded(b"".join((json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                                     for record in target["sampling_failure_history"]))},
        {"kind": "write", "path": plan_path.as_posix(), "before": _encoded(plan_path.read_bytes()), "after": _encoded(json_bytes(target))},
    ])
    plan = {"schema_version": 1, "transaction_id": transaction_id, "root": root.as_posix(),
            "participants": participants, "operations": operations, "guards": guards}
    plan["plan_sha256"] = _hash(plan)
    return AcousticTransaction(path, plan)
