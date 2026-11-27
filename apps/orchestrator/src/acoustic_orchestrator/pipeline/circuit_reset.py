"""Explicit operator reset of a latched batch circuit, never inferred from edits.

The reset only reopens dispatch: chains whose observed process failure was
resolved while the cutoff was latched return to their pre-cutoff decision
(scheduled, or exhausted without budget). Accepted chains, outputs and every
other terminal chain stay untouched. An immutable record with exact preimages
is published first and the closed circuit last, so a torn publication remains
blocked until the same record is completed. No MATLAB, no budget renewal.
"""

from copy import deepcopy
from datetime import UTC, datetime
import getpass
import hashlib
import math
from pathlib import Path
import re
import socket
from typing import Any
from uuid import uuid4

from acoustic_orchestrator.pipeline.interruption_recovery import (
    ProcessProbe,
    _encoded,
    _exclusive,
    active_render_writers,
)
from acoustic_orchestrator.pipeline.process_retries import RetryBlockedError, RetryChain, _save, retry_delay
from acoustic_orchestrator.pipeline.render_attempt import read_json_object, sha256_file
from acoustic_orchestrator.pipeline.static_plan import static_writer_lock

CIRCUIT_NAME = "matlab_process_circuit.json"
RESET_ACTION = "reset_batch_circuit"


def _locations(root: Path) -> tuple[Path, Path, Path]:
    indexes = root / "metadata" / "indexes"
    return indexes / CIRCUIT_NAME, indexes / "circuit_resets", root / "metadata" / "process_chains"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _block_unobserved(chains_dir: Path) -> None:
    for path in sorted(chains_dir.glob("*/chain.json")):
        record = read_json_object(path)
        attempts = record.get("attempts") or []
        if record.get("state") == "running" or (attempts and attempts[-1].get("state") != "resolved"):
            raise RetryBlockedError(
                f"La cadena {path.parent.name} tiene un intento sin terminación observada; "
                "reconcílielo primero con reconcile-static"
            )


def _plan(root: Path, chains_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Classify latched chains; any shape outside the contract fails closed."""
    eligible, untouched = [], []
    for path in sorted(chains_dir.glob("*/chain.json")):
        record = read_json_object(path)
        if record.get("state") != "circuit_open" or record.get("superseded_by"):
            continue
        chain = RetryChain(path, record)
        attempts = record["attempts"]
        latest = attempts[-1] if attempts else None
        if latest is None or latest.get("retry_decision") != "circuit_open":
            raise RetryBlockedError(
                f"La cadena {path.parent.name} quedó cortada sin un intento resuelto por el corte; "
                "el reinicio no la reprograma"
            )
        if not Path(latest["outcome_path"]).resolve().is_relative_to(root):
            raise RetryBlockedError(f"El outcome de la cadena {path.parent.name} no pertenece al lote: {latest['outcome_path']}")
        outcome = chain.verify_resolved()
        if latest.get("classification") == "accepted":
            untouched.append(path.as_posix())
            continue
        if (latest.get("classification") != "unknown_process_failure" or outcome.get("decision") != "abort"
                or type(outcome.get("exit_code")) is not int or outcome["exit_code"] == 0):
            raise RetryBlockedError(
                f"La cadena {path.parent.name} no es un fallo de proceso reprogramable "
                f"({latest.get('classification')})"
            )
        eligible.append({
            "chain_id": record["chain_id"], "chain_path": path.as_posix(),
            "outcome_path": Path(latest["outcome_path"]).as_posix(), "attempt_id": latest["attempt_id"],
            "consumed_attempts": len(attempts), "max_attempts": chain.policy.max_attempts,
            "next_state": "scheduled" if latest["ordinal"] < chain.policy.max_attempts else "exhausted",
            "scene_id": outcome.get("scene_id"), "variant_id": outcome.get("variant_id"),
        })
    return eligible, untouched


def _project(entry: dict[str, Any], marker: dict[str, str], recorded_unix: float) -> tuple[dict, dict]:
    chain, outcome = deepcopy(entry["chain_before"]), deepcopy(entry["outcome_before"])
    decision = entry["next_state"]
    latest = chain["attempts"][-1]
    policy = RetryChain(Path(entry["chain_path"]), chain).policy
    outcome.update(retry_decision=decision, circuit_reset=marker)
    latest.update(retry_decision=decision, circuit_reset=marker)
    chain.update(state=decision, not_before=recorded_unix + retry_delay(policy, latest["ordinal"])
                 if decision == "scheduled" else None)
    return chain, outcome


def _record_path(resets_dir: Path, reset_id: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", reset_id) is None:
        raise RetryBlockedError("reset_id debe ser un UUID hexadecimal")
    return resets_dir / reset_id / "record.json"


def _load_record(root: Path, journal: Path) -> tuple[dict, dict, list[tuple[dict, dict, dict]]]:
    """Verify the immutable record and return (record, closed circuit, projections)."""
    circuit_path, resets_dir, chains_dir = _locations(root)
    record = read_json_object(journal)
    reset_id = record.get("reset_id")
    if (record.get("schema_version") != 1 or record.get("action") != RESET_ACTION
            or not isinstance(reset_id, str) or _record_path(resets_dir, reset_id) != journal
            or record.get("confirmed_stopped") is not True or record.get("active_writers") != []
            or not isinstance(record.get("reason"), str) or not record["reason"].strip()
            or not isinstance(record.get("operator"), str) or not record["operator"].strip()
            or not isinstance(record.get("recorded_unix"), (int, float)) or isinstance(record["recorded_unix"], bool)
            or not math.isfinite(record["recorded_unix"])
            or Path(record.get("circuit_path", "")) != circuit_path
            or not isinstance(record.get("circuit_before"), dict) or record["circuit_before"].get("open") is not True
            or not isinstance(record.get("chains"), list)):
        raise RetryBlockedError(f"Registro de reinicio del corte inválido: {journal}")
    preimage = journal.parent / "circuit.before.json"
    if (preimage.is_symlink() or sha256_file(preimage) != record["circuit_before_sha256"]
            or read_json_object(preimage) != record["circuit_before"]):
        raise RetryBlockedError("La evidencia del circuito anterior al reinicio fue alterada")
    marker = {"id": reset_id, "path": journal.as_posix(), "sha256": sha256_file(journal)}
    projections = []
    for entry in record["chains"]:
        chain_path = chains_dir / entry.get("chain_id", "") / "chain.json"
        if (re.fullmatch(r"[0-9a-f]{32}", str(entry.get("chain_id"))) is None
                or Path(entry.get("chain_path", "")) != chain_path
                or entry.get("next_state") not in {"scheduled", "exhausted"}):
            raise RetryBlockedError("Registro de reinicio con cadenas incoherentes")
        for name in ("chain", "outcome"):
            before = journal.parent / f"{entry['chain_id']}.{name}.before.json"
            if (before.is_symlink() or sha256_file(before) != entry[f"{name}_before_sha256"]
                    or read_json_object(before) != entry[f"{name}_before"]):
                raise RetryBlockedError("La evidencia de cadena u outcome anterior al reinicio fue alterada")
        chain_before, outcome_before = entry["chain_before"], entry["outcome_before"]
        latest = chain_before["attempts"][-1]
        if (chain_before.get("state") != "circuit_open" or latest.get("retry_decision") != "circuit_open"
                or latest.get("classification") != "unknown_process_failure"
                or Path(latest["outcome_path"]) != Path(entry["outcome_path"])
                or not Path(entry["outcome_path"]).resolve().is_relative_to(root)
                or outcome_before.get("attempt_id") != latest["attempt_id"]
                or outcome_before.get("retry_decision") != "circuit_open"):
            raise RetryBlockedError("El registro de reinicio no describe un fallo resuelto por el corte")
        chain_after, outcome_after = _project(entry, marker, record["recorded_unix"])
        projections.append((entry, chain_after, outcome_after))
    closed = {"schema_version": 1, "limit": record["circuit_before"]["limit"], "consecutive_failures": 0,
              "open": False, "resolutions": [], "reset_id": reset_id}
    return record, closed, projections


def verify_circuit_reset_resolution(chain_path: Path, chain: dict, outcome: dict) -> None:
    """A reset-projected decision is valid only with its linked, intact record."""
    root = chain_path.resolve().parents[3]
    latest = chain["attempts"][-1]
    marker = outcome.get("circuit_reset")
    if not isinstance(marker, dict) or latest.get("circuit_reset") != marker:
        raise RetryBlockedError("La cadena y el intento no acreditan el mismo reinicio del corte")
    _, resets_dir, _ = _locations(root)
    journal = _record_path(resets_dir, str(marker.get("id")))
    if Path(str(marker.get("path"))) != journal or not journal.is_file() or sha256_file(journal) != marker.get("sha256"):
        raise RetryBlockedError("El registro de reinicio del corte no coincide con la cadena")
    _, _, projections = _load_record(root, journal)
    for entry, chain_after, outcome_after in projections:
        if entry["chain_id"] == chain["chain_id"]:
            if (outcome != outcome_after or latest != chain_after["attempts"][-1]
                    or any(chain.get(key) != chain_after.get(key) for key in (
                        "chain_id", "manifest_path", "input_sha256", "policy", "artifacts", "preexisting_artifacts"))):
                raise RetryBlockedError("La cadena no coincide con la proyección del reinicio del corte")
            return
    raise RetryBlockedError("La cadena no figura en el registro de reinicio del corte")


def _pending_record(root: Path, circuit_bytes: bytes) -> Path | None:
    """The open circuit bytes identify a started, unfinished publication."""
    _, resets_dir, _ = _locations(root)
    matches = [path for path in sorted(resets_dir.glob("*/record.json"))
               if read_json_object(path).get("circuit_before_sha256") == _sha(circuit_bytes)]
    if len(matches) > 1:
        raise RetryBlockedError("Hay varios registros de reinicio para el mismo corte")
    return matches[0] if matches else None


def _check_publication(projections: list[tuple[dict, dict, dict]]) -> None:
    for entry, chain_after, outcome_after in projections:
        for path, before, after in ((Path(entry["chain_path"]), entry["chain_before_sha256"], chain_after),
                                    (Path(entry["outcome_path"]), entry["outcome_before_sha256"], outcome_after)):
            if sha256_file(path) not in {before, _sha(_encoded(after))}:
                raise RetryBlockedError(f"Publicación de reinicio ambigua: {path}")


def _new_record(root: Path, circuit_bytes: bytes, eligible: list[dict], untouched: list[str],
                reason: str) -> Path:
    circuit_path, resets_dir, _ = _locations(root)
    reset_id = uuid4().hex
    journal = _record_path(resets_dir, reset_id)
    now = datetime.now(UTC)
    entries = []
    journal.parent.mkdir(parents=True, exist_ok=False)
    _exclusive(journal.parent / "circuit.before.json", circuit_bytes)
    for item in eligible:
        chain_bytes = Path(item["chain_path"]).read_bytes()
        outcome_bytes = Path(item["outcome_path"]).read_bytes()
        _exclusive(journal.parent / f"{item['chain_id']}.chain.before.json", chain_bytes)
        _exclusive(journal.parent / f"{item['chain_id']}.outcome.before.json", outcome_bytes)
        entries.append({"chain_id": item["chain_id"], "chain_path": item["chain_path"],
                        "outcome_path": item["outcome_path"], "next_state": item["next_state"],
                        "chain_before": read_json_object(Path(item["chain_path"])),
                        "outcome_before": read_json_object(Path(item["outcome_path"])),
                        "chain_before_sha256": _sha(chain_bytes), "outcome_before_sha256": _sha(outcome_bytes)})
    record = {"schema_version": 1, "action": RESET_ACTION, "reset_id": reset_id,
              "circuit_path": circuit_path.as_posix(), "circuit_before": read_json_object(circuit_path),
              "circuit_before_sha256": _sha(circuit_bytes),
              "circuit_archive_path": circuit_path.with_name(f"matlab_process_circuit.{reset_id}.json").as_posix(),
              "recorded_at": now.isoformat(), "recorded_unix": now.timestamp(),
              "operator": getpass.getuser(), "host": socket.gethostname(), "reason": reason.strip(),
              "confirmed_stopped": True, "active_writers": [], "chains": entries, "untouched_chains": untouched}
    _save(journal, record)  # Durable intent precedes every state change.
    return journal


def _report(root: Path, applied: bool, circuit: dict | None, eligible: list[dict], untouched: list[str],
            journal: Path | None, pending: bool) -> dict[str, Any]:
    return {"applied": applied, "run_root": root.as_posix(),
            "circuit_open": bool(circuit and circuit.get("open")),
            "limit": circuit.get("limit") if circuit else None,
            "consecutive_failures": circuit.get("consecutive_failures") if circuit else None,
            "recent_failures": [item for item in (circuit or {}).get("resolutions", [])
                                if item.get("process_failed")][-5:],
            "chains": [{key: item[key] for key in ("chain_id", "attempt_id", "consumed_attempts", "max_attempts",
                                                   "next_state", "scene_id", "variant_id") if key in item}
                       for item in eligible],
            "untouched_chains": untouched, "pending_publication": pending,
            "reset_path": journal.as_posix() if journal else None}


def _entry_summary(entry: dict) -> dict[str, Any]:
    chain = entry["chain_before"]
    policy = RetryChain(Path(entry["chain_path"]), chain).policy
    return {"chain_id": entry["chain_id"], "attempt_id": chain["attempts"][-1]["attempt_id"],
            "consumed_attempts": len(chain["attempts"]), "max_attempts": policy.max_attempts,
            "next_state": entry["next_state"], "scene_id": entry["outcome_before"].get("scene_id"),
            "variant_id": entry["outcome_before"].get("variant_id")}


def reset_batch_circuit(run_root: str | Path, *, apply: bool = False, confirm_stopped: bool = False,
                        reason: str = "", process_probe: ProcessProbe | None = None) -> dict[str, Any]:
    """Inspect by default; explicitly reset only a latched cutoff and its stopped chains."""
    root = Path(run_root).resolve()
    if not root.is_dir():
        raise RetryBlockedError("El directorio de ejecución no existe")
    if apply and (not confirm_stopped or not reason.strip()):
        raise RetryBlockedError("Aplicar requiere --confirm-stopped y --reason explícitos")
    circuit_path, _, chains_dir = _locations(root)

    def inspect_and_check() -> tuple[dict | None, list[dict], list[str], Path | None]:
        try:
            if not circuit_path.is_file():
                return None, [], [], None
            circuit = read_json_object(circuit_path)
            if circuit.get("schema_version") != 1 or type(circuit.get("open")) is not bool:
                raise RetryBlockedError(f"Circuito del lote inválido: {circuit_path}")
            if not circuit["open"]:
                return circuit, [], [], None
            _block_unobserved(chains_dir)
            journal = _pending_record(root, circuit_path.read_bytes())
            if journal is not None:
                record, _, projections = _load_record(root, journal)
                _check_publication(projections)
                eligible = [_entry_summary(entry) for entry, _, _ in projections]
                untouched = list(record.get("untouched_chains", []))
            else:
                eligible, untouched = _plan(root, chains_dir)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise RetryBlockedError(f"No se pudo verificar la evidencia del corte: {exc}") from exc
        try:
            active = (process_probe or active_render_writers)()
        except Exception as exc:
            raise RetryBlockedError(f"No se pudo verificar la ausencia de escritores: {exc}") from exc
        if not isinstance(active, list):
            raise RetryBlockedError("La inspección de procesos no devolvió un inventario válido")
        if active:
            raise RetryBlockedError(f"Hay procesos escritores activos o no verificables: {active}")
        return circuit, eligible, untouched, journal

    if not apply:
        if (root / "metadata" / "indexes" / "static_writer.lock").exists():
            raise RetryBlockedError("Existe static_writer.lock; no se retira automáticamente")
        circuit, eligible, untouched, journal = inspect_and_check()
        return _report(root, False, circuit, eligible, untouched, journal, journal is not None)

    with static_writer_lock(root):
        circuit, eligible, untouched, journal = inspect_and_check()
        if circuit is None or not circuit["open"]:
            return _report(root, False, circuit, [], [], None, False)
        circuit_bytes = circuit_path.read_bytes()
        if journal is None:
            journal = _new_record(root, circuit_bytes, eligible, untouched, reason)
        record, closed, projections = _load_record(root, journal)
        _check_publication(projections)
        # Rerunning after a torn publication completes only this same intent.
        _exclusive(Path(record["circuit_archive_path"]), circuit_bytes)
        for entry, chain_after, outcome_after in projections:
            outcome_path, chain_path = Path(entry["outcome_path"]), Path(entry["chain_path"])
            if outcome_path.read_bytes() != _encoded(outcome_after):
                _save(outcome_path, outcome_after)
            if chain_path.read_bytes() != _encoded(chain_after):
                _save(chain_path, chain_after)
            verify_circuit_reset_resolution(chain_path, chain_after, outcome_after)
        _save(circuit_path, closed)  # Closing last keeps a torn reset fail-closed.
        return _report(root, True, circuit, [_entry_summary(entry) for entry, _, _ in projections],
                       list(record.get("untouched_chains", [])), journal, False)
