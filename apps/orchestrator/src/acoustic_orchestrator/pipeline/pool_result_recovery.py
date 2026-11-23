"""Explicit operator reconciliation of a pool attempt whose result could not be read.

The pool job ran: MATLAB published ``results/<attempt_id>.json`` and the attempt's
completion receipt, but the orchestrator could not read the result (for example a
Windows lock) and recorded the attempt without observed termination. That outcome
blocks every later resume. The published pool result is the termination evidence:
with exit code 0, a valid completion receipt, no failure report and valid render
artifacts, the attempt is reconciled to an accepted, observed exit 0. Inspection is
the default; applying keeps exact preimages and a journal that the retry chain
verifies on every resume. No renderer is launched and no retry budget is renewed.
"""

from copy import deepcopy
from datetime import UTC, datetime
import getpass
import hashlib
import json
from pathlib import Path
import re
import socket
from typing import Any
from uuid import uuid4

from acoustic_orchestrator.pipeline.interruption_recovery import (
    ProcessProbe, _encoded, _exclusive, active_render_writers,
)
from acoustic_orchestrator.pipeline.output_index import load_variant_index, upsert_variant
from acoustic_orchestrator.pipeline.process_retries import RetryBlockedError, RetryChain, _save
from acoustic_orchestrator.pipeline.render_attempt import inspect_completion, read_json_object, sha256_file
from acoustic_orchestrator.pipeline.render_failure import inspect_failure
from acoustic_orchestrator.pipeline.render_validation import validate_render
from acoustic_orchestrator.pipeline.static_plan import static_writer_lock

ACTION = "reconcile_unread_pool_result"
JOURNAL_DIR = "pool_result_reconciliation"
_UUID = re.compile(r"[0-9a-f]{32}")


def _paths(root: Path, chain_id: str, attempt_id: str) -> tuple[Path, Path, Path]:
    if any(_UUID.fullmatch(value) is None for value in (chain_id, attempt_id)):
        raise RetryBlockedError("Cadena e intento deben ser UUID hexadecimales")
    chain = root / "metadata" / "process_chains" / chain_id / "chain.json"
    outcome = root / "metadata" / "render_attempts" / attempt_id / "outcome.json"
    journal = outcome.parent / JOURNAL_DIR / "record.json"
    if any(not path.resolve().is_relative_to(root) for path in (chain, outcome, journal)):
        raise RetryBlockedError("Las rutas de recuperación deben pertenecer al lote")
    return chain, outcome, journal


def _index_path(root: Path) -> Path:
    return root / "metadata" / "indexes" / "render_index.jsonl"


def _pool_result_path(root: Path, outcome: dict) -> Path:
    """The result the pool published; the session is searched when the outcome never recorded it."""
    name = f"{outcome['attempt_id']}.json"
    session = outcome.get("pool_session_id")
    if session is not None:
        if not isinstance(session, str) or _UUID.fullmatch(session) is None:
            raise RetryBlockedError("El intento registra una sesión de pool inválida")
        return root / "metadata" / "matlab_pool" / session / "results" / name
    found = [path for path in (root / "metadata" / "matlab_pool").glob(f"*/results/{name}")
             if _UUID.fullmatch(path.parent.parent.name)]
    if len(found) != 1:
        raise RetryBlockedError(f"Se esperaba exactamente un resultado publicado por el pool; hay {len(found)}")
    return found[0]


def _validate_identity(root: Path, chain_path: Path, chain: dict, outcome_path: Path, outcome: dict) -> None:
    """Exact pre-reconciliation shape: an unread pool result, nothing else."""
    RetryChain(chain_path, chain)
    if not chain["attempts"]:
        raise RetryBlockedError("La cadena no tiene ningún intento")
    latest = chain["attempts"][-1]
    status = outcome.get("process_status")
    if (chain["state"] != "not_retryable" or chain.get("superseded_by")
            or chain.get("chain_id") != chain_path.parent.name
            or latest.get("state") != "resolved" or latest.get("retry_decision") != "not_retryable"
            or status not in {"launch_failed", "termination_unobserved"}
            or latest.get("classification") != status or outcome.get("retry_classification") != status
            or outcome.get("retry_decision") != "not_retryable"
            or outcome.get("matlab_runner") != "pool"
            or outcome.get("exit_code") is not None or outcome.get("termination_observed") is not False
            or outcome.get("decision") != "abort"):
        raise RetryBlockedError("El intento no es un resultado del pool sin leer reconciliable")
    runtime = Path(chain["manifest_path"])
    snapshot = outcome_path.parent / "input_manifest.json"
    if (Path(latest["outcome_path"]).resolve() != outcome_path
            or outcome.get("schema_version") != 1 or outcome.get("attempt_id") != latest["attempt_id"]
            or outcome.get("chain_id") != chain["chain_id"]
            or outcome.get("attempt_ordinal") != latest["ordinal"]
            or outcome.get("retry_policy") != chain["policy"]
            or outcome.get("commit_status") != "committed" or outcome.get("persistence_error")
            or outcome.get("expected_artifacts") != chain["artifacts"]
            or outcome.get("manifest_sha256") != chain["input_sha256"]
            or outcome.get("input_sha256") != chain["input_sha256"]
            or Path(outcome["manifest_snapshot_path"]).resolve() != snapshot
            or not runtime.is_absolute() or not runtime.resolve().is_relative_to(root)
            or Path(outcome["manifest_path"]).resolve() != runtime.resolve()
            or outcome.get("variant_id") != runtime.stem):
        raise RetryBlockedError("El intento y su cadena no tienen una identidad coherente")
    if sha256_file(snapshot) != chain["input_sha256"]:
        raise RetryBlockedError("El snapshot del intento fue alterado")


def _read_pool_result(path: Path, attempt_id: str) -> tuple[dict, bytes]:
    try:
        data = path.read_bytes()
        result = json.loads(data.decode("utf-8"))
    except FileNotFoundError as exc:
        raise RetryBlockedError(f"No existe el resultado publicado por el pool: {path}") from exc
    except (OSError, ValueError) as exc:
        raise RetryBlockedError(f"El resultado del pool sigue sin poder leerse: {exc}") from exc
    if (not isinstance(result, dict) or result.get("attempt_id") != attempt_id
            or type(result.get("exit_code")) is not int):
        raise RetryBlockedError(f"Resultado del pool inválido para {attempt_id}: {path}")
    if result["exit_code"] != 0:
        raise RetryBlockedError(f"El pool informó exit_code={result['exit_code']}; no hay render que aceptar")
    worker = result.get("worker_id")
    if worker is not None and not isinstance(worker, str):
        raise RetryBlockedError("worker_id del resultado del pool inválido")
    return result, data


def _validate_evidence(root: Path, chain: dict, outcome: dict, row: dict | None) -> dict:
    """Functional evidence that the render finished: receipt, no failure, valid artifacts."""
    if row is None or row.get("attempt_id") != outcome["attempt_id"]:
        raise RetryBlockedError("El índice de renders no referencia este intento")
    if inspect_failure(outcome)["status"] != "missing":
        raise RetryBlockedError("Existe un informe de fallo del intento; requiere revisión independiente")
    if inspect_completion(outcome)["status"] != "valid":
        raise RetryBlockedError("El recibo de finalización falta o es inválido")
    snapshot_path = Path(outcome["manifest_snapshot_path"])
    paths = {"variant_id": outcome["variant_id"], "runtime_manifest_path": chain["manifest_path"],
             "scene_id": outcome["scene_id"], "output_type": outcome["output_type"],
             "rendered_wav_path": chain["artifacts"][0]["path"],
             "render_metadata_path": chain["artifacts"][1]["path"]}
    if outcome["output_type"] == "circular_array":
        paths["circular_array"] = read_json_object(snapshot_path)["receiver"]["circular_array"]
    validation = validate_render(paths, metadata_required=bool(row.get("metadata_required", True)),
                                 manifest_path=snapshot_path, profile="normal")
    if (not validation.artifacts_ok or validation.validity != "valid"
            or validation.acoustic_rejection is not None):
        raise RetryBlockedError(f"Las salidas no son un render válido aceptable: {validation.as_record()['issues']}")
    return validation.as_record()


def _project(audit: dict, marker: dict) -> tuple[dict, dict, dict]:
    chain, outcome = deepcopy(audit["chain_before"]), deepcopy(audit["outcome_before"])
    row = deepcopy(audit["index_row_before"])
    validation = audit["validation"]
    outcome.update(process_status="succeeded", exit_code=0, termination_observed=True, error=None,
                   pool_session_id=Path(audit["pool_result_path"]).parent.parent.name,
                   pool_worker_id=audit["pool_result"].get("worker_id"),
                   render_validity=validation["validity"], validation=validation,
                   decision="continue", decision_issues=[], recovery_reason=None,
                   retry_classification="accepted", retry_decision="not_retryable",
                   pool_result_reconciliation=marker)
    chain["attempts"][-1].update(classification="accepted", pool_result_reconciliation=marker)
    row.update(process_status="succeeded", exit_code=0, decision="continue", recovery_reason=None,
               render_validity=validation["validity"], status="completed",
               validation={"ok": True, "issues": []})
    return chain, outcome, row


def _load_audit(root: Path, chain_path: Path, outcome_path: Path, journal: Path) -> tuple[dict, dict, dict, dict]:
    audit = read_json_object(journal)
    if (audit.get("schema_version") != 1 or audit.get("action") != ACTION
            or audit.get("confirmed_stopped") is not True or audit.get("active_writers") != []
            or not isinstance(audit.get("reason"), str) or not audit["reason"].strip()
            or not isinstance(audit.get("operator"), str) or not audit["operator"].strip()
            or Path(audit.get("chain_path", "")) != chain_path or Path(audit.get("outcome_path", "")) != outcome_path
            or not isinstance(audit.get("validation"), dict) or audit["validation"].get("validity") != "valid"
            or not isinstance(audit.get("index_row_before"), dict)):
        raise RetryBlockedError("Registro de reconciliación del pool inválido")
    for name in ("chain", "outcome"):
        preimage = journal.parent / f"{name}.before.json"
        if (preimage.is_symlink() or sha256_file(preimage) != audit[f"{name}_before_sha256"]
                or read_json_object(preimage) != audit[f"{name}_before"]):
            raise RetryBlockedError("La evidencia anterior a la reconciliación fue alterada")
    _validate_identity(root, chain_path, audit["chain_before"], outcome_path, audit["outcome_before"])
    result_path = _pool_result_path(root, audit["outcome_before"])
    result, data = _read_pool_result(result_path, audit["outcome_before"]["attempt_id"])
    if (Path(audit.get("pool_result_path", "")) != result_path or result != audit.get("pool_result")
            or hashlib.sha256(data).hexdigest() != audit.get("pool_result_sha256")):
        raise RetryBlockedError("El resultado del pool cambió después de la reconciliación")
    marker = {"id": audit["reconciliation_id"], "path": journal.as_posix(), "sha256": sha256_file(journal)}
    chain_after, outcome_after, row_after = _project(audit, marker)
    return audit, chain_after, outcome_after, row_after


def verify_pool_result_resolution(chain_path: Path, chain: dict, outcome: dict) -> None:
    """Verify that a reconciled outcome is exactly the journaled projection."""
    root = chain_path.resolve().parents[3]
    latest = chain["attempts"][-1]
    expected_chain, outcome_path, journal = _paths(root, chain["chain_id"], latest["attempt_id"])
    _, projected_chain, projected_outcome, _ = _load_audit(root, expected_chain, outcome_path, journal)
    if (outcome != projected_outcome or latest != projected_chain["attempts"][-1]
            or any(chain.get(key) != projected_chain.get(key) for key in (
                "chain_id", "state", "manifest_path", "input_sha256", "policy", "artifacts",
                "preexisting_artifacts"))):
        raise RetryBlockedError("La cadena y el intento no acreditan la misma reconciliación del pool")


def _inspect(root: Path, chain_id: str, expected_attempt: str | None) -> dict[str, Any]:
    if _UUID.fullmatch(chain_id) is None:
        raise RetryBlockedError("chain_id debe ser un UUID hexadecimal")
    chain_path = root / "metadata" / "process_chains" / chain_id / "chain.json"
    chain = read_json_object(chain_path)
    RetryChain(chain_path, chain)
    if not chain["attempts"]:
        raise RetryBlockedError("La cadena no tiene ningún intento")
    attempt_id = chain["attempts"][-1]["attempt_id"]
    if expected_attempt is not None and attempt_id != expected_attempt:
        raise RetryBlockedError("El intento actual cambió; inspeccione la cadena de nuevo")
    chain_path, outcome_path, journal = _paths(root, chain_id, attempt_id)
    outcome = read_json_object(outcome_path)
    if sha256_file(Path(chain["manifest_path"])) != chain["input_sha256"]:
        raise RetryBlockedError("El runtime actual difiere de la entrada del intento")
    row = load_variant_index(_index_path(root)).get(outcome.get("variant_id"))
    if journal.exists():
        audit, chain_after, outcome_after, row_after = _load_audit(root, chain_path, outcome_path, journal)
        for path, before_hash, after in ((chain_path, audit["chain_before_sha256"], chain_after),
                                         (outcome_path, audit["outcome_before_sha256"], outcome_after)):
            if sha256_file(path) not in {before_hash, hashlib.sha256(_encoded(after)).hexdigest()}:
                raise RetryBlockedError(f"Publicación de reconciliación ambigua: {path}")
        if row is None or row.get("attempt_id") != outcome_after["attempt_id"]:
            raise RetryBlockedError("El índice de renders ya no referencia el intento reconciliado")
        return {"chain": chain_after, "outcome": outcome_after, "row": row_after, "audit": audit,
                "pool_result": audit["pool_result"]}
    _validate_identity(root, chain_path, chain, outcome_path, outcome)
    result_path = _pool_result_path(root, outcome)
    result, data = _read_pool_result(result_path, attempt_id)
    validation = _validate_evidence(root, chain, outcome, row)
    return {"chain": chain, "outcome": outcome, "row": row, "audit": None, "pool_result": result,
            "pool_result_path": result_path, "pool_result_bytes": data, "validation": validation}


def reconcile_pool_result(
    run_root: str | Path, chain_id: str, *, apply: bool = False, confirm_stopped: bool = False,
    expected_attempt: str | None = None, reason: str = "", process_probe: ProcessProbe | None = None,
) -> dict[str, Any]:
    """Inspect by default; explicitly accept only the pinned attempt with an unread exit-0 result."""
    root = Path(run_root).resolve()
    if not root.is_dir():
        raise RetryBlockedError("El directorio de ejecución no existe")
    if apply and (not confirm_stopped or not reason.strip() or expected_attempt is None):
        raise RetryBlockedError("Aplicar requiere --confirm-stopped, --attempt y --reason explícitos")

    def inspect_and_check() -> dict[str, Any]:
        try:
            found = _inspect(root, chain_id, expected_attempt)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise RetryBlockedError(f"No se pudo verificar la evidencia del intento: {exc}") from exc
        try:
            active = (process_probe or active_render_writers)()
        except Exception as exc:
            raise RetryBlockedError(f"No se pudo verificar la ausencia de escritores: {exc}") from exc
        if not isinstance(active, list):
            raise RetryBlockedError("La inspección de procesos no devolvió un inventario válido")
        if active:
            raise RetryBlockedError(f"Hay procesos escritores activos o no verificables: {active}")
        circuit = root / "metadata" / "indexes" / "matlab_process_circuit.json"
        if circuit.exists() and read_json_object(circuit).get("open"):
            raise RetryBlockedError("El circuito del lote está abierto; la reconciliación no lo reinicia")
        return found

    if not apply:
        if (root / "metadata" / "indexes" / "static_writer.lock").exists():
            raise RetryBlockedError("Existe static_writer.lock; no se retira automáticamente")
        found = inspect_and_check()
    else:
        with static_writer_lock(root):
            found = inspect_and_check()
            outcome = found["outcome"]
            chain_path, outcome_path, journal = _paths(root, chain_id, outcome["attempt_id"])
            if found["audit"] is None:
                chain_bytes, outcome_bytes = chain_path.read_bytes(), outcome_path.read_bytes()
                now = datetime.now(UTC)
                audit = {"schema_version": 1, "action": ACTION, "reconciliation_id": uuid4().hex,
                         "chain_path": chain_path.as_posix(), "outcome_path": outcome_path.as_posix(),
                         "chain_before": found["chain"], "outcome_before": outcome,
                         "chain_before_sha256": hashlib.sha256(chain_bytes).hexdigest(),
                         "outcome_before_sha256": hashlib.sha256(outcome_bytes).hexdigest(),
                         "index_row_before": found["row"],
                         "pool_result_path": found["pool_result_path"].as_posix(),
                         "pool_result": found["pool_result"],
                         "pool_result_sha256": hashlib.sha256(found["pool_result_bytes"]).hexdigest(),
                         "validation": found["validation"],
                         "recorded_at": now.isoformat(), "operator": getpass.getuser(),
                         "host": socket.gethostname(), "reason": reason.strip(),
                         "confirmed_stopped": True, "active_writers": []}
                journal.parent.mkdir(parents=True, exist_ok=True)
                _exclusive(journal.parent / "chain.before.json", chain_bytes)
                _exclusive(journal.parent / "outcome.before.json", outcome_bytes)
                _save(journal, audit)  # Durable intent precedes any state change.
                audit, chain, outcome, row = _load_audit(root, chain_path, outcome_path, journal)
                found.update(audit=audit, chain=chain, outcome=outcome, row=row)
            chain, outcome, row = found["chain"], found["outcome"], found["row"]
            # Rerunning after a torn publication completes only this same intent.
            if outcome_path.read_bytes() != _encoded(outcome):
                _save(outcome_path, outcome)
            if chain_path.read_bytes() != _encoded(chain):
                _save(chain_path, chain)
            records = load_variant_index(_index_path(root))
            if records.get(row["variant_id"]) != row:
                upsert_variant(_index_path(root), records, row)
            verify_pool_result_resolution(chain_path, chain, outcome)
    outcome = found["outcome"]
    return {"applied": apply, "chain_id": chain_id, "attempt_id": outcome["attempt_id"],
            "variant_id": outcome["variant_id"], "scene_id": outcome["scene_id"],
            "pool_session_id": Path(found.get("pool_result_path") or found["audit"]["pool_result_path"]).parent.parent.name,
            "pool_exit_code": found["pool_result"]["exit_code"],
            "worker_id": found["pool_result"].get("worker_id"), "state": found["chain"]["state"],
            "reconciliation_path": (Path(outcome["manifest_snapshot_path"]).parent / JOURNAL_DIR
                                    / "record.json").as_posix() if found["audit"] else None}
