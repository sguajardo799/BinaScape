"""Durable repair evidence shared by readers, without orchestration imports."""

import hashlib
import base64
import json
import os
from datetime import datetime
import math
from pathlib import Path
from typing import Any

from acoustic_orchestrator.pipeline.render_attempt import _json_number, read_json_object, sha256_file
from acoustic_orchestrator.pipeline.atomic_replace import atomic_replace


def json_bytes(record: Any) -> bytes:
    return (json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    atomic_replace(temporary, path)


def journal_path(outcome_path: Path) -> Path:
    return outcome_path.parent / "persistence_repair" / "record.json"


def plan_digest(journal: dict) -> str:
    plan = {key: value for key, value in journal.items() if key not in {"state", "plan_sha256"}}
    if journal.get("schema_version") == 3:
        plan = json.loads(json_bytes(plan), parse_float=_json_number)
    return hashlib.sha256(json_bytes(plan)).hexdigest()


def observed_plan_digest(plan: dict) -> str:
    payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
    payload = json.loads(json_bytes(payload), parse_float=_json_number)
    return hashlib.sha256(json_bytes(payload)).hexdigest()


def read_observed_plan(path: Path) -> dict:
    plan = read_json_object(path)
    root = path.resolve().parents[3]
    if (plan.get("schema_version") != 1 or plan.get("action") != "evaluate_observed_batch"
            or plan.get("plan_id") != path.parent.name or plan.get("root") != root.as_posix()
            or path.parent.parent.name != "observed_recoveries" or path.parent.parent.parent.name != "metadata"
            or plan.get("plan_sha256") != observed_plan_digest(plan)
            or plan.get("circuit_path") != (root / "metadata/indexes/matlab_process_circuit.json").as_posix()
            or plan.get("index_path") != (root / "metadata/indexes/render_index.jsonl").as_posix()
            or not isinstance(plan.get("entries"), list) or not plan["entries"]):
        raise ValueError(f"Invalid observed recovery plan: {path}")
    circuit = plan["circuit_before"]
    if (circuit.get("schema_version") != 1 or circuit.get("open") is not False
            or type(circuit.get("limit")) is not int or circuit["limit"] < 1
            or len({r["attempt_id"] for r in circuit["resolutions"]}) != len(circuit["resolutions"])
            or json.loads(base64.b64decode(plan["circuit_before_bytes"], validate=True)) != circuit):
        raise ValueError(f"Observed recovery circuit preimage conflict: {path}")
    prefixes = [circuit]
    identifiers = set()
    for entry in plan["entries"]:
        identifier = entry["attempt_id"]
        if (identifier in identifiers or entry["outcome_path"] != (root / "metadata/render_attempts" / identifier / "outcome.json").as_posix()
                or entry["chain_path"] != (root / "metadata/process_chains" / entry["before_outcome"]["chain_id"] / "chain.json").as_posix()):
            raise ValueError(f"Observed recovery participant conflict: {path}")
        identifiers.add(identifier)
        projected = json.loads(json.dumps(circuit))
        resolution = entry["resolution"]
        matches = [r for r in projected["resolutions"] if r["attempt_id"] == identifier]
        if matches:
            if matches != [resolution]:
                raise ValueError(f"Observed recovery circuit contradiction: {path}")
        else:
            if resolution["classification"] == "accepted":
                projected["consecutive_failures"] = 0
            elif resolution["process_failed"]:
                projected["consecutive_failures"] += 1
            projected["open"] = projected["open"] or projected["consecutive_failures"] >= projected["limit"]
            projected["resolutions"].append(resolution)
        if projected != entry["circuit_after"]:
            raise ValueError(f"Observed recovery circuit projection conflict: {path}")
        circuit = projected
        prefixes.append(circuit)
    current = read_json_object(Path(plan["circuit_path"]))
    if len({r["attempt_id"] for r in current["resolutions"]}) != len(current["resolutions"]):
        raise ValueError(f"Observed recovery duplicate circuit resolution: {path}")
    state_path = path.with_name("state.json")
    state = read_json_object(state_path) if state_path.exists() else None
    if state is not None and (state.get("schema_version") != 1 or state.get("plan_sha256") != plan["plan_sha256"]
                              or state.get("state") not in {"pending", "complete"}):
        raise ValueError(f"Observed recovery state conflict: {state_path}")
    if state is not None and state["state"] == "complete":
        if (current.get("schema_version") != circuit.get("schema_version") or current.get("limit") != circuit.get("limit")
                or current.get("resolutions", [])[:len(circuit["resolutions"])] != circuit["resolutions"]):
            raise ValueError(f"Observed recovery circuit prefix changed: {path}")
    elif current not in prefixes:
        raise ValueError(f"Observed recovery circuit is outside its plan: {path}")
    return plan


def verify_circuit_prefix(before: dict, current: dict, attempt_id: str) -> int:
    """Allow append-only progress, never revision/recounting of captured history."""
    prefix = before.get("resolutions")
    resolutions = current.get("resolutions")
    if (before.get("schema_version") != 1 or current.get("schema_version") != 1
            or not isinstance(prefix, list) or not isinstance(resolutions, list)
            or current.get("limit") != before.get("limit")
            or resolutions[:len(prefix)] != prefix):
        raise ValueError("Circuit history prefix changed")
    matches = [i for i, item in enumerate(prefix) if item.get("attempt_id") == attempt_id]
    if (len(matches) != 1 or prefix[matches[0]].get("classification") != "accepted"
            or prefix[matches[0]].get("process_failed") is not False):
        raise ValueError("Circuit requires one accepted resolution for the attempt")
    identifiers = [item["attempt_id"] for item in resolutions]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Circuit has duplicate attempt resolutions")
    return matches[0]


def read_journal(outcome_path: Path) -> dict:
    try:
        return _read_journal(outcome_path)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(f"Malformed persistence repair journal: {journal_path(outcome_path)}") from exc


def _read_journal(outcome_path: Path) -> dict:
    path = journal_path(outcome_path)
    journal = read_json_object(path)
    version = journal.get("schema_version")
    if (type(version) is not int or version not in (1, 2, 3)
            or (version == 1 and "action" in journal)
            or (version == 2 and journal.get("action") != "finalize_chain")
            or (version == 3 and journal.get("action") != "evaluate_observed_attempt")
            or journal.get("state") not in {"pending", "complete"}
            or journal.get("attempt_id") != outcome_path.parent.name
            or journal.get("outcome_path") != outcome_path.resolve().as_posix()
            or journal.get("plan_sha256") != plan_digest(journal)):
        raise ValueError(f"Invalid persistence repair journal: {path}")
    filenames = ["outcome.before.json", "chain.before.json", "index.before.json"]
    if version in (2, 3):
        filenames.append("circuit.before.json")
    if set(journal["preimages"]) != set(filenames):
        raise ValueError(f"Persistence repair preimage inventory conflict: {path}")
    for filename in filenames:
        if sha256_file(path.parent / filename) != journal["preimages"][filename]:
            raise ValueError(f"Persistence repair preimage changed: {path.parent / filename}")
    before = read_json_object(path.parent / "outcome.before.json")
    target = journal["targets"]["outcome"]
    if (before.get("schema_version") != 1 or target.get("schema_version") != (3 if version == 3 else 2)
            or target.get("persistence_repair_path") != path.resolve().as_posix()
            or before.get("attempt_id") != journal["attempt_id"]
            or before.get("chain_id") != journal.get("chain_id")
            or target.get("commit_status") != "committed"
            or (version != 3 and target.get("retry_classification") != "accepted")):
        raise ValueError(f"Persistence repair identity conflict: {path}")
    # Publication cannot revise facts about the observed process or its inputs.
    for key in ("attempt_id", "chain_id", "attempt_ordinal", "previous_attempt_id", "retry_policy",
                "scene_id", "variant_id", "output_type", "manifest_path", "manifest_snapshot_path",
                "manifest_sha256", "input_sha256", "expected_artifacts", "command", "exit_code", "error",
                "process_status", "termination_observed", "started_at", "finished_at", "duration_s",
                "completion_path", "failure_path", "raven_resources_path"):
        if version == 3 and key == "raven_resources_path" and before.get(key) is None:
            continue
        if before.get(key) != target.get(key):
            raise ValueError(f"Persistence repair changed process evidence ({key}): {path}")
    if version == 2:
        chain = read_json_object(path.parent / "chain.before.json")
        expected_chain = json.loads(json.dumps(chain))
        expected_chain["attempts"][-1].update(
            state="resolved", classification="accepted", retry_decision="not_retryable",
            archive_path=(outcome_path.parent / "archive.json").as_posix())
        expected_chain.update(state="not_retryable", not_before=None)
        if (before.get("commit_status") != "committed" or before.get("termination_observed") is not True
                or before.get("decision") not in {"continue", "continue_with_warning"}
                or chain.get("state") != "running" or chain["attempts"][-1].get("state") != "running"
                or journal["targets"]["chain"] != expected_chain
                or journal.get("original_error") != before.get("persistence_error")
                or journal.get("reason") != "accepted_chain_not_finalized"
                or target.get("warning") != before.get("warning")
                or target.get("retry_decision") != "not_retryable"):
            raise ValueError(f"Invalid chain finalization plan: {path}")
        circuit_path = Path(journal["chain_path"]).parent.parent.parent / "indexes" / "matlab_process_circuit.json"
        if journal.get("circuit_path") != circuit_path.resolve().as_posix():
            raise ValueError(f"Finalization circuit path conflict: {path}")
        circuit = read_json_object(path.parent / "circuit.before.json")
        position = verify_circuit_prefix(circuit, read_json_object(circuit_path), journal["attempt_id"])
        if circuit.get("open") is not False or journal.get("circuit_resolution") != position:
            raise ValueError(f"Finalization circuit resolution conflict: {path}")
    elif version == 3:
        _verify_observed_journal(journal, before, target, path)
    return journal


def _verify_observed_journal(journal: dict, before: dict, target: dict, path: Path) -> None:
    plan_path = Path(journal["recovery_plan_path"])
    plan = read_observed_plan(plan_path)
    entry = plan["entries"][journal["recovery_position"]]
    chain = read_json_object(path.parent / "chain.before.json")
    if (journal["recovery_plan_sha256"] != plan["plan_sha256"] or entry["attempt_id"] != before["attempt_id"]
            or entry["before_outcome"] != before or entry["before_chain"] != chain
            or entry["before_index"] != read_json_object(path.parent / "index.before.json")["record"]
            or entry["targets"] != journal["targets"] or journal["chain_path"] != entry["chain_path"]
            or journal["index_path"] != plan["index_path"] or journal["circuit_path"] != plan["circuit_path"]
            or read_json_object(path.parent / "circuit.before.json") != plan["circuit_before"]
            or (path.parent / "circuit.before.json").read_bytes() != base64.b64decode(plan["circuit_before_bytes"], validate=True)
            or before.get("decision") != "pending" or before.get("commit_status") != "pending"
            or before.get("termination_observed") is not True or type(before.get("exit_code")) is not int
            or before.get("retry_classification") is not None or before.get("retry_decision") is not None
            or journal.get("original_error") is not None or before.get("persistence_error")
            or chain.get("state") != "running" or chain["attempts"][-1].get("state") != "running"):
        raise ValueError(f"Invalid observed evaluation journal: {path}")
    evaluated = datetime.fromisoformat(target["evaluated_at"])
    finished = datetime.fromisoformat(before["finished_at"])
    if evaluated.tzinfo is None or finished.tzinfo is None or evaluated < finished:
        raise ValueError(f"Invalid observed evaluation timestamp: {path}")
    classification = target["retry_classification"]
    decision = target["retry_decision"]
    expected_decision = ("circuit_open" if entry["circuit_after"]["open"] else
                         "scheduled" if classification == "unknown_process_failure" and len(chain["attempts"]) < chain["policy"]["max_attempts"] else
                         "exhausted" if classification == "unknown_process_failure" else "not_retryable")
    allowed = {"accepted": {"continue", "continue_with_warning"}, "unknown_process_failure": {"abort"},
               "acoustic_rejection": {"retry_acoustic"}, "deterministic_failure": {"abort"}, "invalid_render": {"abort"}}
    if classification not in allowed or target["decision"] not in allowed[classification] or decision != expected_decision:
        raise ValueError(f"Observed evaluation classification conflict: {path}")
    if entry["resolution"] != {"attempt_id": before["attempt_id"], "classification": classification,
                               "process_failed": before["process_status"] == "failed" and target["decision"] != "continue_with_warning"}:
        raise ValueError(f"Observed evaluation circuit resolution conflict: {path}")
    expected_chain = json.loads(json.dumps(chain))
    expected_chain["attempts"][-1].update(state="resolved", classification=classification, retry_decision=decision,
                                         archive_path=(path.parent.parent / "archive.json").as_posix())
    not_before = None
    if decision == "scheduled":
        policy = chain["policy"]
        try:
            delay = min(policy["max_delay_s"], math.ldexp(policy["initial_delay_s"], len(chain["attempts"]) - 1))
        except OverflowError:
            delay = policy["max_delay_s"]
        not_before = evaluated.timestamp() + delay
    expected_chain.update(state=decision, not_before=not_before)
    if journal["targets"]["chain"] != expected_chain:
        raise ValueError(f"Observed evaluation chain projection conflict: {path}")
    resources = entry.get("resource_inventory")
    if resources is not None:
        resource_path = path.parent / "raven_resources.json"
        if (read_json_object(resource_path) != resources or target.get("raven_resources_path") != resource_path.as_posix()
                or target.get("raven_resources_sha256") != sha256_file(resource_path)):
            raise ValueError(f"Observed resource evidence conflict: {path}")


def verify_repaired_outcome(record: dict, outcome_path: Path) -> None:
    """A v2 record is never trusted without its completed, linked journal.

    The journal also gates v1 preimages while a repair is being published.
    Older readers reject v2 by schema even if an index was already published.
    """
    path = journal_path(outcome_path)
    if record.get("schema_version") == 1 and not path.exists():
        return
    if record.get("schema_version") not in (2, 3):
        raise ValueError(f"Unconfirmed persistence repair: {path}")
    journal = read_journal(outcome_path)
    current = {key: value for key, value in record.items() if not key.startswith("_")}
    if journal["state"] != "complete" or current != journal["targets"]["outcome"]:
        raise ValueError(f"Unconfirmed persistence repair outcome: {path}")
    if journal["schema_version"] == 3:
        state = read_json_object(Path(journal["recovery_plan_path"]).with_name("state.json"))
        if state["state"] != "complete":
            raise ValueError(f"Unconfirmed observed recovery batch: {path}")
    chain = read_json_object(Path(journal["chain_path"]))
    # A later explicit new chain may add ancestry links, never rewrite history.
    observed = {key: value for key, value in chain.items() if key != "superseded_by"}
    expected = {key: value for key, value in journal["targets"]["chain"].items() if key != "superseded_by"}
    if observed != expected:
        raise ValueError(f"Unconfirmed persistence repair chain: {path}")
