"""One immutable resolution order for reevaluating observed pending attempts."""

import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
from uuid import uuid4

from acoustic_orchestrator.pipeline.persistence_journal import (
    atomic_write, journal_path, json_bytes, observed_plan_digest, read_journal, read_observed_plan, verify_repaired_outcome,
)
from acoustic_orchestrator.pipeline.process_retries import RetryBlockedError
from acoustic_orchestrator.pipeline.render_attempt import read_json_object


def bind_entry(candidate, path: Path, plan: dict, position: int):
    """Bind only independently derived destinations, never trust copied targets."""
    entry = plan["entries"][position]
    candidate.recovery_plan_path, candidate.recovery_position = path, position
    candidate.circuit_path = Path(plan["circuit_path"])
    candidate.before_circuit = plan["circuit_before"]
    if candidate.resource_inventory is not None:
        resource_path = journal_path(candidate.outcome_path).parent / "raven_resources.json"
        candidate.outcome.update(raven_resources_path=resource_path.resolve().as_posix(),
                                 raven_resources_sha256=hashlib.sha256(json_bytes(candidate.resource_inventory)).hexdigest(),
                                 raven_isolation="verified_private")
    if entry["circuit_after"]["open"]:
        candidate.outcome["retry_decision"] = "circuit_open"
        candidate.chain.update(state="circuit_open", not_before=None)
        candidate.chain["attempts"][-1]["retry_decision"] = "circuit_open"
    candidate.index["updated_at"] = entry["targets"]["index"]["updated_at"]
    if (candidate.resolution != entry["resolution"] or candidate.resource_inventory != entry.get("resource_inventory")
            or {"outcome": candidate.outcome, "chain": candidate.chain, "index": candidate.index} != entry["targets"]):
        raise RetryBlockedError("Observed evaluation targets contradict independently verified evidence")
    return candidate


@dataclass
class ObservedRecovery:
    path: Path
    plan: dict

    def validates_open_circuit(self) -> bool:
        if not self.path.exists():
            return False
        plan = read_observed_plan(self.path)
        state_path = self.path.with_name("state.json")
        return (plan == self.plan and (not state_path.exists() or read_json_object(state_path)["state"] == "pending"))

    def validate_state(self, records: dict) -> None:
        if self.path.exists():
            if read_observed_plan(self.path) != self.plan:
                raise RetryBlockedError("Observed recovery plan changed")
        elif read_json_object(Path(self.plan["circuit_path"])) != self.plan["circuit_before"]:
            raise RetryBlockedError("Observed recovery circuit changed before publication")
        for entry in self.plan["entries"]:
            outcome_path, chain_path = Path(entry["outcome_path"]), Path(entry["chain_path"])
            outcome, chain = read_json_object(outcome_path), read_json_object(chain_path)
            target = entry["targets"]
            if (outcome not in (entry["before_outcome"], {**target["outcome"], "commit_status": "pending"}, target["outcome"])
                    or chain not in (entry["before_chain"], target["chain"])
                    or records.get(entry["before_outcome"]["variant_id"]) not in (entry["before_index"], target["index"])):
                raise RetryBlockedError(f"Observed recovery publication state conflict: {outcome_path}")
            if outcome == entry["before_outcome"] and outcome_path.read_bytes() != base64.b64decode(entry["before_outcome_bytes"]):
                raise RetryBlockedError("Observed recovery outcome preimage changed")
            if chain == entry["before_chain"] and chain_path.read_bytes() != base64.b64decode(entry["before_chain_bytes"]):
                raise RetryBlockedError("Observed recovery chain preimage changed")
            if journal_path(outcome_path).exists():
                journal = read_journal(outcome_path)
            else:
                journal = None
            intent = outcome_path.parent / "acoustic_continuation/record.json"
            if intent.exists() and (journal is None or journal["state"] != "complete"
                                    or outcome != target["outcome"] or chain != target["chain"]):
                raise RetryBlockedError("Observed acoustic intent appeared before its result was resolved")

    def for_variant(self, variant_id: str, records: dict):
        for position, entry in enumerate(self.plan["entries"]):
            if entry["before_outcome"]["variant_id"] == variant_id:
                return self.candidate(position, records)
        return None

    def preflight(self, config, layout, *, lock, stop=None):
        from acoustic_orchestrator.pipeline.acoustic_transactions import inspect_transaction, _require_lock
        from acoustic_orchestrator.pipeline.output_index import load_variant_index
        from acoustic_orchestrator.pipeline.static_plan import inspect_static_plan
        from acoustic_orchestrator.pipeline.render_pipeline import _preflight_render_artifacts

        _require_lock(layout, lock)
        self.validate_state(load_variant_index(layout["render_index_path"]))
        acoustic = inspect_transaction(config, layout,
                                        deferred_observed_attempts={e["attempt_id"] for e in self.plan["entries"]})
        view = acoustic.view(config, layout) if acoustic is not None else None
        plan = inspect_static_plan(config, layout, view=view)
        return _preflight_render_artifacts(config, [item.manifest for item in plan.manifests],
                                            allow_repairs=True, stop=stop, acoustic=acoustic, view=view, observed_recovery=self)

    def publish(self, config, layout, *, lock, stop=None) -> list[str]:
        from acoustic_orchestrator.pipeline.output_index import load_variant_index, upsert_variant

        state_path = self.path.with_name("state.json")
        if self.path.exists():
            read_observed_plan(self.path)
            if state_path.exists() and read_json_object(state_path)["state"] == "complete":
                return []
        def check():
            if stop is not None:
                stop.checkpoint()
            return self.preflight(config, layout, lock=lock, stop=stop)

        check()
        self.prepare(lock=lock)
        state = {"schema_version": 1, "state": "pending", "plan_sha256": self.plan["plan_sha256"]}
        if not state_path.exists():
            atomic_write(state_path, json_bytes(state))
        confirmed = []
        for position, entry in enumerate(self.plan["entries"]):
            check()
            candidate = self.candidate(position, load_variant_index(layout["render_index_path"]))
            journal = candidate.prepare(layout["render_index_path"])
            if journal["state"] == "complete":
                confirmed.append(entry["attempt_id"])
                continue
            check()
            outcome_path, chain_path = Path(entry["outcome_path"]), Path(entry["chain_path"])
            target = entry["targets"]
            if read_json_object(outcome_path) == entry["before_outcome"]:
                atomic_write(outcome_path, json_bytes({**target["outcome"], "commit_status": "pending"}))
            check()
            records = load_variant_index(layout["render_index_path"])
            if records.get(entry["before_outcome"]["variant_id"]) != target["index"]:
                upsert_variant(layout["render_index_path"], records, target["index"])
            check()
            circuit_path = Path(self.plan["circuit_path"])
            current = read_json_object(circuit_path)
            prefix = entry["circuit_after"]["resolutions"]
            if current["resolutions"][:len(prefix)] != prefix:
                atomic_write(circuit_path, json_bytes(entry["circuit_after"]))
            check()
            if read_json_object(outcome_path) != target["outcome"]:
                atomic_write(outcome_path, json_bytes(target["outcome"]))
            check()
            if read_json_object(chain_path) != target["chain"]:
                atomic_write(chain_path, json_bytes(target["chain"]))
            check()
            atomic_write(journal_path(outcome_path), json_bytes({**journal, "state": "complete"}))
            confirmed.append(entry["attempt_id"])
        check()
        from acoustic_orchestrator.pipeline.acoustic_transactions import record_observed_intent
        for entry in self.plan["entries"]:
            if entry["resolution"]["classification"] == "acoustic_rejection":
                check()
                record_observed_intent(layout, entry, lock=lock)
        check()
        atomic_write(state_path, json_bytes({**state, "state": "complete"}))
        for entry in self.plan["entries"]:
            verify_repaired_outcome(read_json_object(Path(entry["outcome_path"])), Path(entry["outcome_path"]))
        return confirmed

    def prepare(self, *, lock) -> None:
        expected = Path(self.plan["index_path"]).parent / "static_writer.lock"
        if lock.path != expected or read_json_object(expected).get("token") != lock.token:
            raise RetryBlockedError("Observed recovery requires the batch writer lock")
        if self.path.exists():
            if read_observed_plan(self.path) != self.plan:
                raise RetryBlockedError("Observed recovery plan changed")
            return
        current = read_json_object(Path(self.plan["circuit_path"]))
        if current != self.plan["circuit_before"]:
            raise RetryBlockedError("Observed recovery circuit changed before planning")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json_bytes(self.plan))
        read_observed_plan(self.path)

    def candidate(self, position: int, records: dict):
        from acoustic_orchestrator.pipeline.persistence_recovery import _inspect
        entry = self.plan["entries"][position]
        original_records = dict(records)
        variant_id = entry["before_outcome"]["variant_id"]
        if entry["before_index"] is None:
            original_records.pop(variant_id, None)
        else:
            original_records[variant_id] = entry["before_index"]
        candidate = _inspect(entry["paths"], entry["expected"], original_records, Path(entry["chain_path"]),
                             entry["before_chain"], Path(entry["outcome_path"]), entry["before_outcome"],
                             metadata_required=entry["metadata_required"], canonical=entry["canonical"],
                             evaluated_at=entry["targets"]["outcome"]["evaluated_at"])
        return bind_entry(candidate, self.path, self.plan, position)


def inspect_observed_recovery(layout: dict, candidates=None, *, config=None) -> ObservedRecovery | None:
    root = layout["run_root"].resolve()
    directory = root / "metadata/observed_recoveries"
    active = []
    for path in directory.glob("*/plan.json"):
        plan = read_observed_plan(path)
        state_path = path.with_name("state.json")
        state = read_json_object(state_path) if state_path.exists() else None
        if state is None or state["state"] != "complete":
            active.append(ObservedRecovery(path, plan))
    if len(active) > 1:
        raise RetryBlockedError("Multiple active observed recovery plans")
    if active:
        return active[0]
    candidates = [c for c in (candidates or []) if c.action == "evaluate_observed_attempt"]
    if not candidates:
        return None
    candidates.sort(key=lambda c: (datetime.fromisoformat(c.before_outcome["finished_at"]), c.outcome["attempt_id"]))
    if len({c.outcome["attempt_id"] for c in candidates}) != len(candidates):
        raise RetryBlockedError("Duplicate observed recovery candidate")
    circuit_path = layout["render_index_path"].parent / "matlab_process_circuit.json"
    circuit = read_json_object(circuit_path)
    if (circuit.get("schema_version") != 1 or circuit.get("open") is not False
            or type(circuit.get("limit")) is not int or circuit["limit"] < 1
            or len({r["attempt_id"] for r in circuit["resolutions"]}) != len(circuit["resolutions"])):
        raise RetryBlockedError("Observed recovery circuit is open, invalid or duplicated")
    identifier = uuid4().hex
    path = directory / identifier / "plan.json"
    plan = {"schema_version": 1, "action": "evaluate_observed_batch", "plan_id": identifier,
            "root": root.as_posix(), "index_path": layout["render_index_path"].resolve().as_posix(),
            "circuit_path": circuit_path.resolve().as_posix(), "circuit_before": deepcopy(circuit),
            "circuit_before_bytes": base64.b64encode(circuit_path.read_bytes()).decode("ascii"),
            "order": "administrative_recovery_finished_at_then_uuid", "entries": []}
    for candidate in candidates:
        resolution = candidate.resolution
        matches = [r for r in circuit["resolutions"] if r["attempt_id"] == resolution["attempt_id"]]
        if matches and matches != [resolution]:
            raise RetryBlockedError("Observed resolution contradicts the existing circuit")
        if not matches:
            if resolution["classification"] == "accepted":
                circuit["consecutive_failures"] = 0
            elif resolution["process_failed"]:
                circuit["consecutive_failures"] += 1
            circuit["open"] = circuit["open"] or circuit["consecutive_failures"] >= circuit["limit"]
            circuit["resolutions"].append(resolution)
        if candidate.resource_inventory is not None:
            resource_path = journal_path(candidate.outcome_path).parent / "raven_resources.json"
            candidate.outcome.update(raven_resources_path=resource_path.resolve().as_posix(),
                                     raven_resources_sha256=hashlib.sha256(json_bytes(candidate.resource_inventory)).hexdigest(),
                                     raven_isolation="verified_private")
        if circuit["open"]:
            candidate.outcome["retry_decision"] = "circuit_open"
            candidate.chain.update(state="circuit_open", not_before=None)
            candidate.chain["attempts"][-1]["retry_decision"] = "circuit_open"
        entry = {
            "attempt_id": candidate.outcome["attempt_id"], "outcome_path": candidate.outcome_path.resolve().as_posix(),
            "chain_path": candidate.chain_path.resolve().as_posix(), "before_outcome": candidate.before_outcome,
            "before_chain": candidate.before_chain, "before_index": candidate.before_index,
            "before_outcome_bytes": base64.b64encode(candidate.outcome_path.read_bytes()).decode("ascii"),
            "before_chain_bytes": base64.b64encode(candidate.chain_path.read_bytes()).decode("ascii"),
            "paths": candidate.paths, "expected": candidate.expected,
            "metadata_required": candidate.metadata_required, "canonical": candidate.canonical,
            "resolution": resolution, "resource_inventory": candidate.resource_inventory,
            "circuit_after": deepcopy(circuit),
            "targets": {"outcome": candidate.outcome, "chain": candidate.chain, "index": candidate.index}}
        if resolution["classification"] == "acoustic_rejection":
            if config is None:
                raise RetryBlockedError("Acoustic observed evaluation requires its validated experiment configuration")
            from acoustic_orchestrator.pipeline.render_pipeline import _build_planned_variants_from_manifest, prepare_scene_source_assets
            scene_path = layout["scene_manifest_dir"] / f"{candidate.outcome['scene_id']}.json"
            scene = read_json_object(scene_path)
            entry["acoustic_context"] = {
                "manifest_path": scene_path.resolve().as_posix(), "scene": scene,
                "scene_index": scene["reverberation_sampling"]["scene_index"],
                "prepared_source_paths": prepare_scene_source_assets(scene, layout["prepared_audio_dir"] / scene["scene_id"], create=False),
                "variants": [v["variant_paths"] for v in _build_planned_variants_from_manifest(config, scene, layout["runtime_manifest_dir"])]}
        plan["entries"].append(entry)
    plan["plan_sha256"] = observed_plan_digest(plan)
    return ObservedRecovery(path, plan)
