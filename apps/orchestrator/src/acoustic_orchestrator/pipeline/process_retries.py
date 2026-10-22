"""Durable, single-attempt retry primitives. The batch coordinator owns dispatch.

Call open_chain, reserve_attempt, the runner (with attempt=), persist_acceptance,
then resolve_attempt. A scheduled retry needs archive_attempt before reservation.
All methods require the batch's exclusive writer lock. Storage errors propagate;
never launch after one. No console or diagnostic text participates in policy.
"""

import math
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from acoustic_orchestrator.config.models import MatlabProcessRetryConfig
from acoustic_orchestrator.pipeline.render_acceptance import RenderAcceptance
from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt, inspect_completion, read_json_object, sha256_file
from acoustic_orchestrator.pipeline.render_failure import inspect_failure


class RetryBlockedError(RuntimeError):
    """State is terminal, incomplete, or ambiguous; operator intervention is needed."""


def _save(path: Path, record: dict[str, Any]) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, allow_nan=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def classify_attempt(acceptance: RenderAcceptance, *, failure: Mapping[str, Any] | None = None) -> str:
    """Classify already evaluated functional evidence, not the original error text.

    failure is the result of render_failure.inspect_failure, or verified preflight
    evidence: status missing/valid/invalid and category invalid_input or
    missing_dependency. Omission invokes the strict functional report verifier.
    """
    process = acceptance.process
    record = process.attempt.record
    if failure is None:
        failure = inspect_failure(record)
    if record.get("commit_status") != "committed" or record.get("persistence_error"):
        return "storage_failure"
    if failure is not None and failure.get("status") == "invalid":
        return "integrity_conflict"
    if acceptance.completion["status"] == "invalid":
        return "integrity_conflict"
    # WAV structure/dimensions may describe an interrupted write, not foreign
    # provenance. A final receipt is checked above and still vetoes truncation.
    if any(("mismatch" in issue or "contradictory" in issue) and not issue.startswith("wav_")
           for issue in acceptance.validation.invalid):
        return "integrity_conflict"
    if failure is not None and failure.get("status") == "valid":
        if acceptance.decision != "abort" or acceptance.completion["status"] == "valid" or process.exit_code == 0:
            return "integrity_conflict"
        if failure.get("category") in {"invalid_input", "missing_dependency"}:
            return "deterministic_failure"
        return "integrity_conflict"
    if not process.termination_observed or process.exit_code is None:
        return "launch_failed" if process.process_status == "launch_failed" else "termination_unobserved"
    if "process_functional_error_contradiction" in acceptance.issues:
        return "integrity_conflict"
    if acceptance.decision in {"continue", "continue_with_warning"}:
        return "accepted"
    if acceptance.decision == "retry_acoustic":
        return "acoustic_rejection"
    return "unknown_process_failure" if process.exit_code != 0 else "invalid_render"


def retry_delay(policy: MatlabProcessRetryConfig, ordinal: int) -> float:
    if type(ordinal) is not int or ordinal < 1:
        raise ValueError("ordinal must be a positive integer")
    # Avoid overflow even for a very large configured budget.
    try:
        return min(policy.max_delay_s, math.ldexp(policy.initial_delay_s, ordinal - 1))
    except OverflowError:
        return policy.max_delay_s


class RetryChain:
    def __init__(self, path: Path, record: dict[str, Any]) -> None:
        self.path = path
        self.record = record
        self.policy = MatlabProcessRetryConfig.model_validate(record["policy"])
        self._storage_failed = False
        attempts = record.get("attempts")
        if (record.get("schema_version") != 1 or not isinstance(attempts, list)
                or len(attempts) > self.policy.max_attempts
                or record.get("state") not in {"ready", "running", "scheduled", "not_retryable",
                                                "exhausted", "circuit_open"}):
            raise RetryBlockedError("invalid retry chain state or budget")
        seen = set()
        for ordinal, attempt in enumerate(attempts, 1):
            if (type(attempt.get("ordinal")) is not int or attempt["ordinal"] != ordinal
                    or not isinstance(attempt.get("attempt_id"), str) or attempt["attempt_id"] in seen):
                raise RetryBlockedError("invalid attempt ordinals or duplicate identities")
            seen.add(attempt["attempt_id"])
        if record["state"] == "scheduled":
            deadline = record.get("not_before")
            if (not attempts or isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                    or not math.isfinite(deadline)
                    or attempts[-1].get("retry_decision") != "scheduled"):
                raise RetryBlockedError("invalid scheduled chain/deadline")

    def save(self) -> None:
        try:
            _save(self.path, self.record)
        except Exception:
            self._storage_failed = True
            raise

    def verify_resolved(self) -> dict[str, Any]:
        """Verify the prior durable outcome and immutable snapshot without writes."""
        return _resolved_outcome(self)

    def previous_process_error(self) -> Exception | None:
        """Recover the nonzero process cause or confirmed interruption with links.

        This deliberately does not require a healthy snapshot/archive: callers
        need the original cause when reporting a later integrity/storage failure.
        It does require the latest outcome's matching attempt and chain identity.
        """
        if not self.record["attempts"]:
            return None
        latest = self.record["attempts"][-1]
        path = Path(latest["outcome_path"])
        outcome = read_json_object(path)
        if (outcome.get("attempt_id") != latest["attempt_id"]
                or outcome.get("chain_id") != self.record["chain_id"]):
            raise RetryBlockedError("previous error outcome identity mismatch")
        code = outcome.get("exit_code")
        if outcome.get("retry_classification") == "confirmed_interruption":
            self.verify_resolved()
            interrupted = RetryBlockedError("Intento anterior interrumpido; código de salida no observado")
            interrupted.add_note(f"MATLAB retry chain: {self.path}")
            interrupted.add_note(f"Original attempt outcome: {path}")
            return interrupted
        if type(code) is not int or code == 0 or outcome.get("termination_observed") is not True:
            return None
        error = subprocess.CalledProcessError(code, outcome.get("command") or "matlab")
        stored = outcome.get("error")
        if isinstance(stored, dict):
            error.add_note(f"Original recorded {stored.get('type', 'error')}: {stored.get('message', '')}")
        error.add_note(f"MATLAB retry chain: {self.path}")
        error.add_note(f"Original attempt outcome: {path}")
        return error

    def reserve_attempt(self, manifest_path: Path, attempts_dir: Path | None = None, *,
                        clock: Callable[[], float] = time.time,
                        sleep: Callable[[float], None] = time.sleep,
                        circuit_open: bool = False) -> RenderAttempt:
        """Persist a consumed ordinal BEFORE constructing/returning launch evidence.

        A crash during reservation deliberately blocks rather than guessing that
        the reserved invocation did not run. Only resolved scheduled chains resume.
        """
        if self._storage_failed:
            raise RetryBlockedError("chain storage failed; reload durable state before proceeding")
        if circuit_open:
            self.record["state"] = "circuit_open"
            self.save()
            raise RetryBlockedError("batch circuit is open")
        if self.record["state"] not in {"ready", "scheduled"}:
            raise RetryBlockedError(f"chain is {self.record['state']}: {self.path}")
        if sha256_file(manifest_path) != self.record["input_sha256"]:
            raise RetryBlockedError("retry input changed")
        attempts = self.record["attempts"]
        if len(attempts) >= self.policy.max_attempts:
            raise RetryBlockedError("chain budget exhausted")
        if attempts:
            previous = attempts[-1]
            journal = read_json_object(Path(previous["archive_path"]))
            if journal.get("state") != "complete" or journal.get("attempt_id") != previous["attempt_id"]:
                raise RetryBlockedError("previous archive is incomplete")
            _verify_archive(journal, self.record["artifacts"], Path(previous["archive_path"]).parent)
        for artifact in self.record["artifacts"]:
            if Path(artifact["path"]).exists():
                raise RetryBlockedError("public output path is occupied")
        remaining = max(0.0, self.record.get("not_before", 0.0) - clock())
        if remaining:
            sleep(remaining)
        attempt_id = uuid4().hex
        ordinal = len(attempts) + 1
        context = {"chain_id": self.record["chain_id"], "attempt_ordinal": ordinal,
                   "previous_attempt_id": attempts[-1]["attempt_id"] if attempts else None,
                   "input_sha256": self.record["input_sha256"], "retry_policy": self.record["policy"]}
        attempts.append({"attempt_id": attempt_id, "ordinal": ordinal, "state": "reserved"})
        self.record["state"] = "running"
        self.save()
        attempt = RenderAttempt(manifest_path, attempts_dir, attempt_id=attempt_id, chain_context=context)
        if attempt.manifest_sha256 != self.record["input_sha256"]:
            raise RetryBlockedError("input changed while reserving attempt")
        attempts[-1].update(outcome_path=attempt.outcome_path.as_posix(), state="running")
        self.save()
        return attempt

    def resolve_attempt(self, acceptance: RenderAcceptance, *, failure: Mapping[str, Any] | None = None,
                        circuit_open: bool = False, clock: Callable[[], float] = time.time) -> str:
        """Call only AFTER persist_acceptance; returns scheduling, not acceptance."""
        attempt = acceptance.process.attempt
        latest = self.record["attempts"][-1]
        if self.record["state"] != "running" or latest["attempt_id"] != attempt.attempt_id:
            raise RetryBlockedError("resolution does not match running chain attempt")
        classification = classify_attempt(acceptance, failure=failure)
        decision = ("circuit_open" if circuit_open else
                    "scheduled" if classification == "unknown_process_failure" and
                    latest["ordinal"] < self.policy.max_attempts else
                    "exhausted" if classification == "unknown_process_failure" else "not_retryable")
        attempt.record.update(retry_classification=classification, retry_decision=decision)
        attempt.save()
        latest.update(state="resolved", classification=classification, retry_decision=decision,
                      archive_path=(attempt.directory / "archive.json").as_posix())
        self.record.update(state=decision, not_before=clock() + retry_delay(self.policy, latest["ordinal"])
                           if decision == "scheduled" else None)
        self.save()
        return decision

    def archive_attempt(self) -> None:
        """Journal intent first; reconcile moves interrupted before/after commit."""
        if self.record["state"] != "scheduled":
            raise RetryBlockedError("only a scheduled resolved failure/interruption may be archived")
        latest = self.record["attempts"][-1]
        outcome = self.verify_resolved()
        confirmed_interruption = outcome.get("retry_classification") == "confirmed_interruption"
        if confirmed_interruption:
            from acoustic_orchestrator.pipeline.interruption_recovery import verify_interruption_artifacts
            verify_interruption_artifacts(self.path, self.record, outcome)
        if (outcome.get("commit_status") != "committed"
                or outcome.get("retry_decision") != "scheduled"
                or (not confirmed_interruption and (not outcome.get("termination_observed")
                                                    or outcome.get("exit_code") in {None, 0}))):
            raise RetryBlockedError("previous process is not durably resolved")
        path = Path(latest["archive_path"])
        if path.exists():
            journal = read_json_object(path)
        else:
            entries = []
            protected = {item["path"] for item in self.record["preexisting_artifacts"]}
            for index, artifact in enumerate(self.record["artifacts"]):
                source = Path(artifact["path"])
                if source.exists():
                    if artifact["path"] in protected or source.is_symlink() or not source.is_file():
                        raise RetryBlockedError("cannot archive protected or non-file output")
                    entries.append({"original_path": source.as_posix(),
                                    "archive_path": (path.parent / "archived_outputs" / f"{index}_{source.name}").as_posix(),
                                    "size_bytes": source.stat().st_size, "sha256": sha256_file(source),
                                    "state": "pending"})
            journal = {"schema_version": 1, "attempt_id": latest["attempt_id"],
                       "state": "pending", "entries": entries}
            _save(path, journal)
        if journal.get("attempt_id") != latest["attempt_id"]:
            raise RetryBlockedError("archive identity mismatch")
        _verify_archive(journal, self.record["artifacts"], path.parent, reconcile=True)
        for entry in journal["entries"]:
            source, destination = Path(entry["original_path"]), Path(entry["archive_path"])
            if source.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.rename(destination)
            entry["state"] = "archived"
            _save(path, journal)
        journal["state"] = "complete"
        _save(path, journal)
        _verify_archive(journal, self.record["artifacts"], path.parent)


def _verify_archive(journal: dict[str, Any], artifacts: list[dict[str, Any]], directory: Path, *,
                    reconcile: bool = False) -> None:
    allowed = {item["path"] for item in artifacts}
    seen: set[str] = set()
    if journal.get("schema_version") != 1 or not isinstance(journal.get("entries"), list):
        raise RetryBlockedError("invalid archive journal")
    for entry in journal["entries"]:
        source, destination = Path(entry["original_path"]), Path(entry["archive_path"])
        if (source.as_posix() not in allowed or source.as_posix() in seen or
                destination.parent != directory / "archived_outputs" or destination.is_symlink()):
            raise RetryBlockedError("archive inventory/path contradiction")
        seen.add(source.as_posix())
        if source.exists() == destination.exists():
            raise RetryBlockedError("ambiguous archive source/destination")
        actual = source if source.exists() else destination
        if actual.is_symlink() or actual.stat().st_size != entry["size_bytes"] or sha256_file(actual) != entry["sha256"]:
            raise RetryBlockedError("archive bytes changed")
        if not reconcile and (source.exists() or entry["state"] != "archived"):
            raise RetryBlockedError("archive is not committed")
    if any(Path(item["path"]).exists() and item["path"] not in seen for item in artifacts):
        raise RetryBlockedError("untracked output appeared during archive")


def _matching_chains(manifest_path: Path, chains_dir: Path) -> list[RetryChain]:
    matches = []
    for path in chains_dir.glob("*/chain.json"):
        record = read_json_object(path)
        if record.get("schema_version") != 1:
            raise RetryBlockedError(f"unsupported chain: {path}")
        if record.get("manifest_path") == manifest_path.resolve().as_posix():
            matches.append(RetryChain(path, record))
    return matches


def _resolved_outcome(chain: RetryChain) -> dict[str, Any]:
    if not chain.record["attempts"]:
        raise RetryBlockedError("predecessor has no resolved attempt")
    latest = chain.record["attempts"][-1]
    if latest.get("state") != "resolved":
        raise RetryBlockedError("previous attempt is not resolved")
    outcome = read_json_object(Path(latest["outcome_path"]))
    if (outcome.get("attempt_id") != latest["attempt_id"]
            or type(outcome.get("schema_version")) is not int or outcome["schema_version"] != 1
            or outcome.get("chain_id") != chain.record["chain_id"]
            or outcome.get("attempt_ordinal") != latest["ordinal"]
            or outcome.get("retry_policy") != chain.record["policy"]
            or outcome.get("manifest_sha256") != chain.record["input_sha256"]
            or outcome.get("input_sha256") != chain.record["input_sha256"]
            or outcome.get("expected_artifacts") != chain.record["artifacts"]
            or outcome.get("commit_status") != "committed"
            or outcome.get("persistence_error")
            or outcome.get("retry_classification") != latest.get("classification")
            or outcome.get("retry_decision") != latest.get("retry_decision")):
        raise RetryBlockedError("previous process is not durably resolved with matching identity")
    if sha256_file(Path(outcome["manifest_snapshot_path"])) != chain.record["input_sha256"]:
        raise RetryBlockedError("predecessor input snapshot changed")
    classification = outcome["retry_classification"]
    if classification == "confirmed_interruption":
        from acoustic_orchestrator.pipeline.interruption_recovery import verify_interruption_resolution
        verify_interruption_resolution(chain.path, chain.record, outcome)
    elif outcome.get("termination_observed") is not True or type(outcome.get("exit_code")) is not int:
        raise RetryBlockedError("previous process termination was not observed")
    decision = outcome.get("decision")
    if ((classification == "accepted" and decision not in {"continue", "continue_with_warning"})
            or (classification == "acoustic_rejection" and decision != "retry_acoustic")
            or (classification not in {"accepted", "acoustic_rejection"} and decision != "abort")
            or (classification == "unknown_process_failure" and outcome["exit_code"] == 0)):
        raise RetryBlockedError("predecessor classification contradicts acceptance/process evidence")
    snapshot = read_json_object(Path(outcome["manifest_snapshot_path"]))
    if (outcome.get("scene_id") != snapshot.get("scene_id")
            or outcome.get("output_type") != snapshot["render"].get("active_output_type")
            or outcome.get("variant_id") != Path(chain.record["manifest_path"]).stem):
        raise RetryBlockedError("predecessor outcome contradicts immutable input identity")
    return outcome


def inspect_chain(manifest_path: Path, chains_dir: Path, *, input_sha256: str | None = None,
                  require_scheduled: bool = False) -> RetryChain | None:
    """Read-only discovery, safe before applying a batch preflight plan.

    With require_scheduled=True, only a same-input chain with a committed,
    observed nonzero latest process authorizes its own partial public outputs.
    None grants no such permission. Ambiguous/contradictory evidence raises.
    The caller must still archive/reconcile before reservation, and acquire its
    writer lock and revalidate before writes. No files or mtimes are changed.
    """
    matches = _matching_chains(manifest_path, chains_dir)
    active = [chain for chain in matches if not chain.record.get("superseded_by")]
    if len(active) > 1 or (matches and not active):
        raise RetryBlockedError("ambiguous retry chain ancestry")
    if not active:
        return None
    chain = active[0]
    digest = input_sha256 if input_sha256 is not None else sha256_file(manifest_path)
    if chain.record["input_sha256"] != digest:
        return None
    if require_scheduled:
        if chain.record["state"] != "scheduled":
            return None
        outcome = _resolved_outcome(chain)
        if (outcome["exit_code"] == 0 or outcome.get("decision") != "abort"
                or outcome.get("retry_decision") != "scheduled"
                or outcome.get("retry_classification") not in {"unknown_process_failure", "confirmed_interruption"}
                or len(chain.record["attempts"]) >= chain.policy.max_attempts):
            raise RetryBlockedError("scheduled retry has contradictory process evidence")
        if inspect_failure(outcome)["status"] != "missing":
            raise RetryBlockedError("scheduled retry has deterministic or contradictory failure evidence")
        if inspect_completion(outcome)["status"] != "missing":
            raise RetryBlockedError("scheduled retry has unexpected completion evidence")
        protected = {item["path"] for item in chain.record["preexisting_artifacts"]}
        if any(item["path"] in protected and Path(item["path"]).exists()
               for item in chain.record["artifacts"]):
            raise RetryBlockedError("scheduled retry cannot authorize protected outputs")
        journal_path = Path(chain.record["attempts"][-1]["archive_path"])
        if journal_path.exists():
            journal = read_json_object(journal_path)
            if journal.get("attempt_id") != outcome["attempt_id"]:
                raise RetryBlockedError("archive identity mismatch")
            _verify_archive(journal, chain.record["artifacts"], journal_path.parent, reconcile=True)
    return chain


def preflight_chain(manifest_path: Path, chains_dir: Path, *, input_sha256: str,
                    reusable: bool, overwrite_existing: bool, force_reset: bool) -> RetryChain | None:
    """Read-only batch gate, BEFORE applying any manifest/runtime/index writes.

    The supplied hash comes from the expected runtime built in memory; no runtime
    file is required. Only a returned scheduled same-input chain owns partial
    outputs. None permits no moving/overwriting by itself: the caller must still
    enforce public-output protection. Explicit force can bypass terminal evidence
    integrity, but never a running/unobserved process or incompatible pending work.
    """
    matches = _matching_chains(manifest_path, chains_dir)
    if not matches:
        return None
    by_id = {chain.record["chain_id"]: chain for chain in matches}
    if len(by_id) != len(matches):
        raise RetryBlockedError("duplicate retry chain identity")
    active = [chain for chain in matches if not chain.record.get("superseded_by")]
    if len(active) != 1:
        raise RetryBlockedError("ambiguous retry chain ancestry")
    for candidate in matches:
        record = candidate.record
        if any(identifier not in by_id or by_id[identifier].record.get("superseded_by") != record["chain_id"]
               for identifier in record.get("supersedes", [])):
            raise RetryBlockedError("retry chain predecessor links disagree")
        if record["state"] == "running":
            raise RetryBlockedError("retry chain has unobserved process termination")
        if record["attempts"]:
            latest = record["attempts"][-1]
            if latest.get("state") != "resolved":
                raise RetryBlockedError("retry chain has a reserved/unobserved attempt")
            # Even force cannot reinterpret an unobserved invocation as stopped.
            outcome = read_json_object(Path(latest["outcome_path"]))
            if outcome.get("termination_observed") is not True or type(outcome.get("exit_code")) is not int:
                if outcome.get("retry_classification") != "confirmed_interruption":
                    raise RetryBlockedError("retry chain process termination was not observed")
                candidate.verify_resolved()
        visited = set()
        current = candidate
        while current.record.get("superseded_by"):
            identifier = current.record["chain_id"]
            successor_id = current.record["superseded_by"]
            if identifier in visited or successor_id not in by_id:
                raise RetryBlockedError("broken or cyclic retry chain ancestry")
            visited.add(identifier)
            successor = by_id[successor_id]
            if identifier not in successor.record.get("supersedes", []):
                raise RetryBlockedError("retry chain ancestry links disagree")
            current = successor
        if current.record["chain_id"] != active[0].record["chain_id"]:
            raise RetryBlockedError("disconnected retry chain ancestry")
    chain = active[0]
    same_input = chain.record["input_sha256"] == input_sha256
    state = chain.record["state"]
    if state in {"ready", "scheduled"}:
        if not same_input:
            raise RetryBlockedError("cannot replace a pending acoustic realization")
        if state == "scheduled":
            return inspect_chain(manifest_path, chains_dir, input_sha256=input_sha256, require_scheduled=True)
        if chain.record["attempts"]:
            raise RetryBlockedError("ready chain unexpectedly contains consumed attempts")
        return None
    if force_reset:
        return None
    outcome = chain.verify_resolved()
    accepted = (outcome.get("retry_classification") == "accepted"
                and outcome.get("decision") in {"continue", "continue_with_warning"})
    acoustic_rejection = (outcome.get("retry_classification") == "acoustic_rejection"
                          and outcome.get("decision") == "retry_acoustic")
    if same_input:
        if reusable and accepted:
            return None
        absent_outputs = not any(Path(item["path"]).exists() for item in chain.record["artifacts"])
        if state == "not_retryable" and accepted and (overwrite_existing or absent_outputs):
            return None
        raise RetryBlockedError(f"same-input retry chain is terminal ({state}): {chain.path}")
    if state == "not_retryable" and (accepted or acoustic_rejection):
        return None
    if overwrite_existing:
        return None
    raise RetryBlockedError("incompatible realization requires authorized overwrite")


def open_chain(manifest_path: Path, chains_dir: Path, policy: MatlabProcessRetryConfig, *,
               resume_if_possible: bool = True, overwrite_existing: bool = False) -> RetryChain:
    """Discover by variant and immutable bytes; force reset preserves old chains.

    Legacy outcomes have no chain and are not interpreted as a consumed budget.
    Existing outputs still block here: the caller must validate reuse or explicitly
    handle authorized overwrite before starting a chain. We never remove them.
    """
    manifest_path = manifest_path.resolve()
    digest = sha256_file(manifest_path)
    matches = _matching_chains(manifest_path, chains_dir)
    active = [chain for chain in matches if not chain.record.get("superseded_by")]
    if len(active) > 1 or (matches and not active):
        raise RetryBlockedError("ambiguous retry chain ancestry")
    force = not resume_if_possible and overwrite_existing
    if force and any(chain.record.get("state") == "running" for chain in matches):
        raise RetryBlockedError("cannot reset a chain with unobserved process termination")
    if matches and not force:
        predecessor = active[0]
        if predecessor.record["input_sha256"] == digest:
            # Authorized replacement of a formerly accepted, now unusable
            # render is not a renewal of an exhausted failure budget.
            replacing_accepted = (
                (overwrite_existing or not any(Path(item["path"]).exists() for item in predecessor.record["artifacts"]))
                and predecessor.record["state"] == "not_retryable"
                and _resolved_outcome(predecessor).get("retry_classification") == "accepted"
            )
            if not replacing_accepted:
                return predecessor
        if predecessor.record["state"] in {"ready", "running", "scheduled"}:
            raise RetryBlockedError("cannot replace a pending acoustic realization")
        outcome = _resolved_outcome(predecessor)
        acoustic_transition = (predecessor.record["state"] == "not_retryable"
                              and outcome.get("retry_classification") in {"accepted", "acoustic_rejection"}
                              and outcome.get("decision") in {"continue", "continue_with_warning", "retry_acoustic"})
        if not acoustic_transition and not overwrite_existing:
            raise RetryBlockedError("incompatible realization requires authorized overwrite")
    payload = read_json_object(manifest_path)
    artifacts = [{"kind": kind, "path": Path(payload["render"][field]).resolve().as_posix()}
                 for kind, field in (("wav", "output_wav_path"), ("metadata", "output_metadata_path"))]
    preexisting = [{**item, "size_bytes": Path(item["path"]).stat().st_size,
                    "sha256": sha256_file(Path(item["path"]))}
                   for item in artifacts if Path(item["path"]).exists()]
    if preexisting:
        raise RetryBlockedError("preexisting outputs require reuse or explicit overwrite handling")
    chain_id = uuid4().hex
    chain = RetryChain(chains_dir / chain_id / "chain.json", {
        "schema_version": 1, "chain_id": chain_id, "manifest_path": manifest_path.as_posix(),
        "input_sha256": digest, "render_seed": payload["render"].get("seed"),
        "policy": policy.model_dump(), "artifacts": artifacts, "preexisting_artifacts": preexisting,
        "state": "ready", "attempts": [], "not_before": 0.0,
        "supersedes": [previous.record["chain_id"] for previous in active],
    })
    chain.save()
    for previous in active:
        previous.record["superseded_by"] = chain_id
        previous.save()
    return chain


class BatchCircuit:
    """Coordinator-only durable resolution order and latched global cutoff."""

    def __init__(self, path: Path, max_consecutive_failures: int, *, force_reset: bool = False) -> None:
        MatlabProcessRetryConfig(max_consecutive_failures=max_consecutive_failures)
        self.path = path
        if path.exists() and not force_reset:
            self.record = read_json_object(path)
            if self.record.get("schema_version") != 1:
                raise RetryBlockedError("unsupported batch circuit")
        else:
            if path.exists():
                path.rename(path.with_name(f"{path.stem}.{uuid4().hex}.json"))
            self.record = {"schema_version": 1, "limit": max_consecutive_failures,
                           "consecutive_failures": 0, "open": False, "resolutions": []}
            _save(path, self.record)

    @property
    def is_open(self) -> bool:
        return bool(self.record["open"])

    def resolve(self, attempt_id: str, classification: str, *, process_failed: bool) -> bool:
        if any(item["attempt_id"] == attempt_id for item in self.record["resolutions"]):
            raise RetryBlockedError("attempt already counted by circuit")
        if classification == "accepted":
            self.record["consecutive_failures"] = 0
        elif process_failed:
            self.record["consecutive_failures"] += 1
        if self.record["consecutive_failures"] >= self.record["limit"]:
            self.record["open"] = True
        self.record["resolutions"].append({"attempt_id": attempt_id, "classification": classification,
                                           "process_failed": process_failed})
        try:
            _save(self.path, self.record)
        except Exception:
            self.record["open"] = True
            raise
        return self.is_open
