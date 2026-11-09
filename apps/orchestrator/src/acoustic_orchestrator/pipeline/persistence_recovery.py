"""Repair acceptance publication, never launch or infer a process result.

Inspection is read-only. Publication requires the caller's static writer lock
and a successful whole-batch preflight. Protected render files are never written.
"""

from copy import deepcopy
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import subprocess
from typing import Any

from acoustic_orchestrator.pipeline.matlab_runner import MatlabProcessResult
from acoustic_orchestrator.pipeline.output_index import inspect_variant
from acoustic_orchestrator.pipeline.process_retries import RetryBlockedError, classify_attempt, inspect_chain, retry_delay
from acoustic_orchestrator.pipeline.raven_resources import describe_resource_inventory, is_private_runtime, verify_resource_inventory
from acoustic_orchestrator.config.models import MatlabProcessRetryConfig
from acoustic_orchestrator.pipeline.render_acceptance import evaluate_render
from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt, read_json_object, sha256_file
from acoustic_orchestrator.pipeline.render_history import load_render_history
from acoustic_orchestrator.pipeline.render_validation import equivalent_render_inputs
from acoustic_orchestrator.pipeline.persistence_journal import (
    atomic_write, journal_path, json_bytes, plan_digest, read_journal, read_observed_plan,
    verify_repaired_outcome, verify_circuit_prefix,
)


@dataclass
class PersistenceRepair:
    outcome_path: Path
    chain_path: Path
    before_outcome: dict[str, Any]
    before_chain: dict[str, Any]
    before_index: dict[str, Any] | None
    outcome: dict[str, Any]
    chain: dict[str, Any]
    index: dict[str, Any]
    paths: dict
    expected: dict
    metadata_required: bool
    canonical: bool
    action: str = "repair_acceptance"
    circuit_path: Path | None = None
    before_circuit: dict | None = None
    resolution: dict | None = None
    resource_inventory: dict | None = None
    recovery_plan_path: Path | None = None
    recovery_position: int | None = None

    def prepare(self, index_path: Path) -> dict:
        """Publish preimages and intent before any active state is changed."""
        batch = None
        if self.action == "evaluate_observed_attempt":
            if self.recovery_plan_path is None or self.recovery_position is None:
                raise RetryBlockedError("Observed evaluation requires a batch recovery plan before publication")
            batch = read_observed_plan(self.recovery_plan_path)
            entry = batch["entries"][self.recovery_position]
            if (entry["attempt_id"] != self.outcome["attempt_id"]
                    or entry["targets"] != {"outcome": self.outcome, "chain": self.chain, "index": self.index}):
                raise RetryBlockedError("Observed evaluation does not match its recovery batch")
        path = journal_path(self.outcome_path)
        if path.exists():
            journal = read_journal(self.outcome_path)
            if journal["index_path"] != index_path.resolve().as_posix():
                raise RetryBlockedError("Repair index path conflict")
            return journal
        if (read_json_object(self.outcome_path) != self.before_outcome
                or read_json_object(self.chain_path) != self.before_chain):
            raise RetryBlockedError("Repair source changed before journal publication")
        from acoustic_orchestrator.pipeline.output_index import load_variant_index
        if load_variant_index(index_path).get(self.outcome["variant_id"]) != self.before_index:
            raise RetryBlockedError("Repair index changed before journal publication")
        if batch is None and self.circuit_path is not None and read_json_object(self.circuit_path) != self.before_circuit:
            raise RetryBlockedError("Repair circuit changed before journal publication")
        path.parent.mkdir(exist_ok=True)
        preimages = {"outcome.before.json": self.outcome_path.read_bytes(),
                     "chain.before.json": self.chain_path.read_bytes(),
                       "index.before.json": json_bytes({"record": self.before_index})}
        if batch is not None:
            if "before_outcome_bytes" in entry:
                preimages["outcome.before.json"] = base64.b64decode(entry["before_outcome_bytes"], validate=True)
                preimages["chain.before.json"] = base64.b64decode(entry["before_chain_bytes"], validate=True)
        if self.circuit_path is not None:
            preimages["circuit.before.json"] = (base64.b64decode(batch["circuit_before_bytes"], validate=True)
                                                if batch is not None else self.circuit_path.read_bytes())
        for filename, data in preimages.items():
            destination = path.parent / filename
            if destination.exists():
                if destination.read_bytes() != data:
                    raise RetryBlockedError(f"Repair preimage conflict: {destination}")
            else:
                atomic_write(destination, data)
        if batch is not None and self.resource_inventory is not None:
            destination = path.parent / "raven_resources.json"
            data = json_bytes(self.resource_inventory)
            if destination.exists():
                if read_json_object(destination) != self.resource_inventory:
                    raise RetryBlockedError("Observed resource inventory changed")
            else:
                atomic_write(destination, data)
        journal = {"schema_version": 1, "state": "pending", "attempt_id": self.outcome["attempt_id"],
                   "chain_id": self.outcome["chain_id"], "outcome_path": self.outcome_path.resolve().as_posix(),
                   "chain_path": self.chain_path.resolve().as_posix(), "index_path": index_path.resolve().as_posix(),
                   "created_at": datetime.now(UTC).isoformat(),
                    "original_error": self.before_outcome.get("persistence_error"),
                   "preimages": {name: sha256_file(path.parent / name) for name in preimages},
                    "targets": {"outcome": self.outcome, "chain": self.chain, "index": self.index}}
        if self.action == "finalize_chain":
            journal.update(schema_version=2, action=self.action, reason="accepted_chain_not_finalized",
                           circuit_path=self.circuit_path.resolve().as_posix(),
                           circuit_resolution=verify_circuit_prefix(
                               self.before_circuit, self.before_circuit, self.outcome["attempt_id"]))
        elif batch is not None:
            journal.update(schema_version=3, action=self.action, reason="observed_evaluation_pending",
                           circuit_path=self.circuit_path.resolve().as_posix(),
                           recovery_plan_path=self.recovery_plan_path.resolve().as_posix(),
                           recovery_plan_sha256=batch["plan_sha256"], recovery_position=self.recovery_position)
        journal["plan_sha256"] = plan_digest(journal)
        atomic_write(path, json_bytes(journal))
        return read_journal(self.outcome_path)

    def recheck(self, index_path: Path) -> None:
        """Revalidate protected evidence and allowed transitions before each write."""
        from acoustic_orchestrator.pipeline.output_index import load_variant_index
        current = inspect_persistence_repair(self.paths, self.expected, load_variant_index(index_path),
                                             self.chain_path.parent.parent,
                                             metadata_required=self.metadata_required, canonical=self.canonical)
        if current is None or (current.outcome, current.chain, current.index) != (self.outcome, self.chain, self.index):
            raise RetryBlockedError("Repair changed during publication")

    def publish(self, index_path: Path) -> bool:
        """Complete this repair idempotently; the batch writer lock is required."""
        from acoustic_orchestrator.pipeline.output_index import load_variant_index, upsert_variant
        try:
            journal = self.prepare(index_path)
            if journal["state"] == "complete":
                verify_repaired_outcome(read_json_object(self.outcome_path), self.outcome_path)
                return False
            self.recheck(index_path)
            pending = {**self.outcome, "commit_status": "pending"}
            # Never move a committed target backwards after an interrupted repair.
            if read_json_object(self.outcome_path) == self.before_outcome:
                atomic_write(self.outcome_path, json_bytes(pending))
            self.recheck(index_path)
            records = load_variant_index(index_path)
            if records.get(self.outcome["variant_id"]) != self.index:
                upsert_variant(index_path, records, self.index)
            self.recheck(index_path)
            if read_json_object(self.outcome_path) != self.outcome:
                atomic_write(self.outcome_path, json_bytes(self.outcome))
            self.recheck(index_path)
            if read_json_object(self.chain_path) != self.chain:
                atomic_write(self.chain_path, json_bytes(self.chain))
            self.recheck(index_path)
            atomic_write(journal_path(self.outcome_path), json_bytes({**journal, "state": "complete"}))
            verify_repaired_outcome(read_json_object(self.outcome_path), self.outcome_path)
            return True
        except (OSError, ValueError, RuntimeError) as exc:
            exc.render_context = self.context
            raise

    @property
    def context(self) -> dict[str, str]:
        return {**{key: self.outcome[key] for key in ("scene_id", "variant_id", "attempt_id", "chain_id")},
                "evidence": self.outcome_path.as_posix()}


def inspect_persistence_repair(paths: dict, expected_manifest: dict, records: dict,
                               chains_dir: Path, *, metadata_required: bool,
                               canonical: bool) -> PersistenceRepair | None:
    """Return a strictly verified candidate, or None for an unrelated chain."""
    runtime = Path(paths["runtime_manifest_path"])
    chain = inspect_chain(runtime, chains_dir)
    if chain is None or not chain.record["attempts"]:
        return None
    latest = chain.record["attempts"][-1]
    outcome_path = Path(latest["outcome_path"])
    expected_outcome = chains_dir.parent / "render_attempts" / latest["attempt_id"] / "outcome.json"
    if outcome_path.resolve() != expected_outcome.resolve():
        raise RetryBlockedError(f"Repair outcome is outside its attempt directory: {outcome_path}")
    source: dict = {}
    try:
        source = read_json_object(outcome_path)
        journal = read_journal(outcome_path) if journal_path(outcome_path).exists() else None
        if (source.get("retry_classification") != "storage_failure" and journal is None
                and chain.record["state"] != "running"):
            return None
        if journal is not None:
            if journal["state"] == "complete":
                verify_repaired_outcome(source, outcome_path)
                return None
            original = read_json_object(journal_path(outcome_path).parent / "outcome.before.json")
            original_chain = read_json_object(journal_path(outcome_path).parent / "chain.before.json")
            original_index = read_json_object(journal_path(outcome_path).parent / "index.before.json")["record"]
            target = journal["targets"]
            if (journal["chain_path"] != chain.path.resolve().as_posix()
                    or source not in (original, {**target["outcome"], "commit_status": "pending"}, target["outcome"])
                    or chain.record not in (original_chain, target["chain"])
                    or records.get(paths["variant_id"]) not in (original_index, target["index"])):
                raise RetryBlockedError(f"Repair publication state conflict: {outcome_path}")
            original_records = {**records}
            if original_index is None:
                original_records.pop(paths["variant_id"], None)
            else:
                original_records[paths["variant_id"]] = original_index
            repair = _inspect(paths, expected_manifest, original_records, chain.path, original_chain, outcome_path,
                               original, metadata_required=metadata_required, canonical=canonical)
            if journal["schema_version"] == 2:
                before_circuit = read_json_object(journal_path(outcome_path).parent / "circuit.before.json")
                verify_circuit_prefix(before_circuit, repair.before_circuit, original["attempt_id"])
                repair.before_circuit = before_circuit
            # Only the timestamp of a freshly recomputed index may differ.
            repair.index["updated_at"] = target["index"]["updated_at"]
            if {"outcome": repair.outcome, "chain": repair.chain, "index": repair.index} != target:
                raise RetryBlockedError(f"Repair targets contradict verified evidence: {outcome_path}")
            return repair
        return _inspect(paths, expected_manifest, records, chain.path, chain.record, outcome_path,
                        source, metadata_required=metadata_required, canonical=canonical)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        exc.render_context = {"scene_id": paths["scene_id"], "variant_id": paths["variant_id"],
                              "attempt_id": latest["attempt_id"], "chain_id": chain.record["chain_id"],
                              "evidence": outcome_path.as_posix()}
        if isinstance(exc, (KeyError, TypeError)):
            malformed = RetryBlockedError(f"Malformed repair evidence: {outcome_path}")
            malformed.render_context = exc.render_context
            raise malformed from exc
        raise


def _inspect(paths: dict, expected: dict, records: dict, chain_path: Path, chain: dict,
             outcome_path: Path, source: dict, *, metadata_required: bool, canonical: bool,
             evaluated_at: str | None = None) -> PersistenceRepair:
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise RetryBlockedError(f"Reparación de persistencia bloqueada: {message}: {outcome_path}")

    latest = chain["attempts"][-1]
    directory = outcome_path.parent
    runtime = Path(paths["runtime_manifest_path"])
    digest = sha256_file(runtime)
    action = _repair_action(chain, source, require)
    require(type(source.get("schema_version")) is int and source["schema_version"] == 1
            and source.get("attempt_id") == directory.name == latest["attempt_id"]
            and source.get("chain_id") == chain["chain_id"] == chain_path.parent.name
            and type(source.get("attempt_ordinal")) is int
            and source.get("attempt_ordinal") == latest["ordinal"]
            and source.get("retry_policy") == chain["policy"]
            and source.get("expected_artifacts") == chain["artifacts"]
             and source.get("manifest_path") == runtime.resolve().as_posix(), "identidad de intento/cadena incompatible")
    require(source.get("previous_attempt_id") == (chain["attempts"][-2]["attempt_id"]
                                                  if len(chain["attempts"]) > 1 else None),
            "predecesor incompatible")
    require(source.get("scene_id") == paths["scene_id"] and source.get("variant_id") == paths["variant_id"]
            and source.get("output_type") == paths["output_type"], "identidad de variante incompatible")
    for name, filename in (("manifest_snapshot_path", "input_manifest.json"), ("completion_path", "completion.json"),
                           ("failure_path", "failure.json")):
        require(Path(source[name]).resolve() == (directory / filename).resolve(), "ruta de evidencia ajena")
    require(source.get("manifest_sha256") == source.get("input_sha256") == chain["input_sha256"] == digest
            == sha256_file(directory / "input_manifest.json"), "huella de manifiesto incompatible")
    snapshot = read_json_object(directory / "input_manifest.json")
    require(chain.get("render_seed") == snapshot["render"].get("seed"), "semilla incompatible")
    require(equivalent_render_inputs(expected, snapshot) and equivalent_render_inputs(expected, read_json_object(runtime)),
            "entradas funcionales incompatibles")
    expected_artifacts = [{"kind": kind, "path": paths[field]} for kind, field in
                          (("wav", "rendered_wav_path"), ("metadata", "render_metadata_path"))]
    require(source["expected_artifacts"] == expected_artifacts, "salidas ajenas")
    if action != "evaluate_observed_attempt":
        require(source.get("validation", {}).get("ok") is True, "validación previa no aceptada")
    code = source.get("exit_code")
    require(source.get("termination_observed") is True and type(code) is int, "terminación no observada")
    stored_error = source.get("error")
    require((code == 0 and source.get("process_status") == "succeeded" and stored_error is None)
            or (code != 0 and source.get("process_status") == "failed" and isinstance(stored_error, dict)
                and stored_error.get("type") == "CalledProcessError" and bool(stored_error.get("message"))),
            "resultado de proceso contradictorio")
    existing = records.get(paths["variant_id"])
    require(not existing or existing.get("attempt_id") in (None, source["attempt_id"]), "índice apunta a otro intento")
    require(not existing or all(existing.get(key) == paths[key] for key in (
        "scene_id", "variant_id", "output_type", "runtime_manifest_path", "rendered_wav_path", "render_metadata_path"
    )), "índice contradice rutas o identidad")
    history = load_render_history(directory.parent)
    require(not history.issues, "historial ilegible")
    prior = history.records.get(paths["variant_id"], [])
    require(bool(prior) and not any(item.get("_unresolved") for item in prior), "historial no resuelto")
    timestamps = [datetime.fromisoformat(item["started_at"]) for item in prior]
    require(all(item.tzinfo is not None for item in timestamps), "horario de intento no verificable")
    require(timestamps.count(max(timestamps)) == 1, "último intento ambiguo")
    newest = max(prior, key=lambda item: datetime.fromisoformat(item["started_at"]))
    require(newest["attempt_id"] == source["attempt_id"], "existe un intento posterior")
    attempt = RenderAttempt.from_record(outcome_path, deepcopy(source))
    resources = None
    if is_private_runtime(snapshot):
        if source.get("raven_resources_path"):
            verify_resource_inventory(source, snapshot)
        elif action == "evaluate_observed_attempt":
            resources = describe_resource_inventory(attempt)
        else:
            require(False, "inventario privado no acreditado")
    error = subprocess.CalledProcessError(code, source.get("command") or "render") if code else None
    process = MatlabProcessResult(attempt, code, error, True, source["process_status"])
    acceptance = evaluate_render(process, paths, metadata_required=metadata_required, canonical=canonical)
    if action == "evaluate_observed_attempt":
        finished = datetime.fromisoformat(source["finished_at"])
        require(finished.tzinfo is not None and finished >= datetime.fromisoformat(source["started_at"]),
                "fin de proceso no verificable")
        # Classification normally follows a committed acceptance. Project that
        # state in memory only; pending disk state is not a storage failure.
        attempt.record.update(acceptance.as_record(), commit_status="committed")
        classification = classify_attempt(acceptance)
        require(classification not in {"storage_failure", "integrity_conflict", "termination_unobserved", "launch_failed"},
                "evidencia observada contradictoria")
    else:
        require(acceptance.completion["status"] == "valid"
                and acceptance.decision in {"continue", "continue_with_warning"}, "constancia o validación funcional rechazada")
        classification = "accepted"
    target = {**deepcopy(source), **acceptance.as_record(), "error": stored_error, "schema_version": 2,
              "persistence_repair_path": journal_path(outcome_path).resolve().as_posix(),
               "commit_status": "committed", "retry_classification": "accepted", "retry_decision": "not_retryable"}
    if action == "evaluate_observed_attempt":
        target.update(schema_version=3, retry_classification=classification,
                      evaluated_at=evaluated_at or datetime.now(UTC).isoformat())
        target["retry_decision"] = ("scheduled" if classification == "unknown_process_failure"
                                    and latest["ordinal"] < chain["policy"]["max_attempts"] else
                                    "exhausted" if classification == "unknown_process_failure" else "not_retryable")
    target.pop("persistence_error", None)
    if acceptance.recovered and action != "finalize_chain":
        target["warning"] = {"code": "matlab_process_failed_after_completion",
                             "message": "Proceso fallido; finalización acreditada. Causa de cierre no confirmada."}
    target_chain = deepcopy(chain)
    target_chain["attempts"][-1]["classification"] = classification
    if action == "finalize_chain":
        require(source.get("decision") == acceptance.decision
                and source.get("recovery_reason") == acceptance.as_record().get("recovery_reason"),
                "aceptación previa contradictoria")
        target_chain["attempts"][-1].update(
            state="resolved", retry_decision="not_retryable", archive_path=(directory / "archive.json").as_posix())
        target_chain.update(state="not_retryable", not_before=None)
    elif action == "evaluate_observed_attempt":
        decision = target["retry_decision"]
        target_chain["attempts"][-1].update(
            state="resolved", retry_decision=decision, archive_path=(directory / "archive.json").as_posix())
        policy = MatlabProcessRetryConfig.model_validate(chain["policy"])
        target_chain.update(state=decision, not_before=datetime.fromisoformat(target["evaluated_at"]).timestamp()
                            + retry_delay(policy, latest["ordinal"]) if decision == "scheduled" else None)
    index = inspect_variant(paths, metadata_required=metadata_required, attempted=True,
                            render_validation=acceptance.validation)
    if classification == "accepted":
        require(index["status"] == "completed" and index["validation"]["ok"], "índice funcional inválido")
    for name in ("attempt_id", "attempt_outcome_path", "completion_path", "process_status", "exit_code",
                 "decision", "recovery_reason"):
        index[name] = target[name]
    repair = PersistenceRepair(outcome_path, chain_path, deepcopy(source), deepcopy(chain), deepcopy(existing),
                             target, target_chain, index, deepcopy(paths), deepcopy(expected), metadata_required, canonical,
                              action)
    repair.resource_inventory = resources
    if action == "evaluate_observed_attempt":
        repair.resolution = {"attempt_id": source["attempt_id"], "classification": classification,
                             "process_failed": source["process_status"] == "failed" and not acceptance.recovered}
    if action == "finalize_chain":
        repair.circuit_path = chain_path.parent.parent.parent / "indexes" / "matlab_process_circuit.json"
        repair.before_circuit = read_json_object(repair.circuit_path)
        require(repair.before_circuit.get("open") is False, "circuito abierto")
        verify_circuit_prefix(repair.before_circuit, repair.before_circuit, source["attempt_id"])
    return repair


def _repair_action(chain: dict, source: dict, require) -> str:
    """Action-specific eligibility; neither branch relaxes common evidence checks."""
    latest = chain["attempts"][-1]
    if chain["state"] == "running":
        require(latest.get("state") == "running"
                and latest.get("classification") is None and latest.get("retry_decision") is None,
                "intento running contradictorio")
        if (source.get("commit_status") == "pending" and source.get("decision") == "pending"
                and "retry_classification" not in source and "retry_decision" not in source
                and not source.get("persistence_error")):
            return "evaluate_observed_attempt"
        require(source.get("commit_status") == "committed"
                and source.get("decision") in {"continue", "continue_with_warning"}
                and not source.get("persistence_error")
                and (("retry_classification" not in source and "retry_decision" not in source)
                     or (source.get("retry_classification") == "accepted"
                         and source.get("retry_decision") == "not_retryable")),
                "aceptación no confirmada o clasificación contradictoria")
        return "finalize_chain"
    require(chain["state"] == "not_retryable" and latest.get("state") == "resolved"
            and latest.get("classification") == "storage_failure"
            and latest.get("retry_decision") == "not_retryable", "cadena no resuelta como fallo de almacenamiento")
    require(source.get("commit_status") == "failed" and source.get("decision") == "abort"
            and source.get("retry_decision") == "not_retryable"
            and isinstance(source.get("persistence_error"), dict)
            and source.get("decision_issues") == ["acceptance_persistence_failed"],
            "no es un fallo exclusivo de publicación")
    return "repair_acceptance"
