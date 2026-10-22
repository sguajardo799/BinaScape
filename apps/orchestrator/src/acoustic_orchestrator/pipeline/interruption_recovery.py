"""Explicit operator reconciliation, never inferred from an absent PID or log.

The immutable journal and exact preimages authorize one interrupted attempt,
not a successful process. Publication is resumable and remains fail-closed until
both outcome and chain agree. No renderer, index, lock removal or budget reset.
"""

from copy import deepcopy
from datetime import UTC, datetime
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
from typing import Any, Callable
from uuid import uuid4

from acoustic_orchestrator.pipeline.process_retries import RetryBlockedError, RetryChain, _save, _verify_archive, retry_delay
from acoustic_orchestrator.pipeline.render_attempt import read_json_object, sha256_file
from acoustic_orchestrator.pipeline.render_validation import validate_render
from acoustic_orchestrator.pipeline.static_plan import static_writer_lock


ProcessProbe = Callable[[], list[dict[str, Any]]]


def active_render_writers() -> list[dict[str, Any]]:
    """Conservative local check: any MATLAB/RAVEN or rendering orchestrator blocks.

    Process absence is not evidence of a past exit code. Inspection failure blocks
    reconciliation; --confirm-stopped remains a separate operator requirement.
    """
    if os.name == "nt":
        script = (
            "Get-CimInstance Win32_Process -ErrorAction Stop | "
            "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
        )
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                check=True, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        rows = data if isinstance(data, list) else [data]
    else:
        result = subprocess.run(["ps", "-eo", "pid=,comm=,args="], check=True,
                                capture_output=True, text=True, timeout=30)
        rows = []
        for line in result.stdout.splitlines():
            fields = line.strip().split(None, 2)
            if len(fields) != 3:
                raise RetryBlockedError("No se pudo interpretar la lista de procesos")
            rows.append({"ProcessId": int(fields[0]), "Name": fields[1], "CommandLine": fields[2]})
    if not rows:
        raise RetryBlockedError("La consulta nativa devolvió un inventario de procesos vacío")
    blocked = []
    for row in rows:
        pid, name, command = row["ProcessId"], str(row["Name"]).lower(), row.get("CommandLine")
        if pid == os.getpid():
            continue
        renderer = "matlab" in name or "raven" in name
        possible_orchestrator = any(term in name for term in ("python", "acoustic", "uv"))
        if renderer or (possible_orchestrator and (
            command is None or any(term in str(command) for term in ("render-static", "generate-manifests"))
        )):
            blocked.append({"pid": pid, "name": row["Name"], "command": command})
    return blocked


def _encoded(record: dict) -> bytes:
    return (json.dumps(record, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _artifact_observations(artifacts: list[dict]) -> list[dict]:
    observed = []
    for artifact in artifacts:
        path = Path(artifact["path"])
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RetryBlockedError(f"Salida no regular: {path}")
        observed.append({**artifact, "present": path.exists(),
                         "size_bytes": path.stat().st_size if path.exists() else None,
                         "sha256": sha256_file(path) if path.exists() else None})
    return observed


def _exclusive(path: Path, data: bytes) -> None:
    if path.exists():
        if path.is_symlink() or path.read_bytes() != data:
            raise RetryBlockedError(f"La evidencia original no coincide: {path}")
        return
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _paths(root: Path, chain_id: str, attempt_id: str) -> tuple[Path, Path, Path]:
    if any(re.fullmatch(r"[0-9a-f]{32}", value) is None for value in (chain_id, attempt_id)):
        raise RetryBlockedError("Cadena e intento deben ser UUID hexadecimales")
    chain = root / "metadata" / "process_chains" / chain_id / "chain.json"
    outcome = root / "metadata" / "render_attempts" / attempt_id / "outcome.json"
    journal = outcome.parent / "interruption_reconciliation" / "record.json"
    if any(not path.resolve().is_relative_to(root) for path in (chain, outcome, journal)):
        raise RetryBlockedError("Las rutas de recuperación deben pertenecer al lote")
    return chain, outcome, journal


def _validate_original(root: Path, chain_path: Path, chain: dict, outcome_path: Path, outcome: dict) -> None:
    current = RetryChain(chain_path, chain)
    latest = chain["attempts"][-1]
    snapshot = outcome_path.parent / "input_manifest.json"
    runtime = Path(chain["manifest_path"])
    if (chain["state"] != "running" or chain.get("superseded_by")
            or chain.get("chain_id") != chain_path.parent.name
            or latest.get("state") not in {"running", "reserved"}
            or Path(latest["outcome_path"]).resolve() != outcome_path
            or outcome.get("schema_version") != 1 or outcome.get("attempt_id") != latest["attempt_id"]
            or outcome.get("chain_id") != chain["chain_id"]
            or outcome.get("attempt_ordinal") != latest["ordinal"]
            or outcome.get("retry_policy") != chain["policy"]
            or outcome.get("exit_code") is not None or outcome.get("termination_observed") not in (None, False)
            or outcome.get("process_status") not in {"running", "termination_unobserved"}
            or outcome.get("decision") not in {"pending", "abort"} or outcome.get("persistence_error")
            or chain.get("preexisting_artifacts")
            or outcome.get("expected_artifacts") != chain["artifacts"]
            or outcome.get("manifest_sha256") != chain["input_sha256"]
            or outcome.get("input_sha256") != chain["input_sha256"]
            or Path(outcome["manifest_snapshot_path"]).resolve() != snapshot
            or not runtime.is_absolute() or not runtime.resolve().is_relative_to(root)
            or Path(outcome["manifest_path"]).resolve() != runtime.resolve()
            or outcome.get("variant_id") != runtime.stem):
        raise RetryBlockedError("El intento no es una interrupción reconciliable con identidad coherente")
    if sha256_file(snapshot) != chain["input_sha256"]:
        raise RetryBlockedError("El snapshot del intento fue alterado")
    payload = read_json_object(snapshot)
    artifacts = [{"kind": kind, "path": Path(payload["render"][field]).resolve().as_posix()}
                 for kind, field in (("wav", "output_wav_path"), ("metadata", "output_metadata_path"))]
    if (artifacts != chain["artifacts"] or outcome.get("scene_id") != payload.get("scene_id")
            or outcome.get("output_type") != payload["render"].get("active_output_type")):
        raise RetryBlockedError("El inventario o la identidad contradicen el snapshot")
    if any(not Path(item["path"]).is_absolute() or not Path(item["path"]).resolve().is_relative_to(root)
           or Path(item["path"]).is_symlink() for item in artifacts):
        raise RetryBlockedError("Inventario fuera del lote o con enlaces simbólicos")
    if current.policy.max_attempts < latest["ordinal"]:
        raise RetryBlockedError("Presupuesto de la cadena incoherente")


def _project(audit: dict, marker: dict) -> tuple[dict, dict]:
    chain, outcome = deepcopy(audit["chain_before"]), deepcopy(audit["outcome_before"])
    latest = chain["attempts"][-1]
    decision = "scheduled" if len(chain["attempts"]) < RetryChain(
        Path(audit["chain_path"]), chain).policy.max_attempts else "exhausted"
    outcome.update(process_status="interrupted", termination_observed=False, exit_code=None,
                   decision="abort", commit_status="committed", render_validity="not_verifiable",
                   validation={"ok": False, "issues": ["operator_confirmed_interruption", "process_termination_not_observed"]},
                   decision_issues=["operator_confirmed_interruption", "process_termination_not_observed"],
                   recovery_reason=None, retry_classification="confirmed_interruption", retry_decision=decision,
                   interruption_recorded_at=audit["recorded_at"], interruption_reconciliation=marker)
    latest.update(state="resolved", classification="confirmed_interruption", retry_decision=decision,
                  archive_path=(Path(audit["outcome_path"]).parent / "archive.json").as_posix(),
                  interruption_reconciliation=marker)
    policy = RetryChain(Path(audit["chain_path"]), chain).policy
    chain.update(state=decision, not_before=audit["recorded_unix"] + retry_delay(policy, latest["ordinal"])
                 if decision == "scheduled" else None)
    return chain, outcome


def _load_audit(root: Path, chain_path: Path, outcome_path: Path, journal: Path) -> tuple[dict, dict, dict]:
    audit = read_json_object(journal)
    if (audit.get("schema_version") != 1 or audit.get("action") != "confirm_interrupted_attempt"
            or audit.get("confirmed_stopped") is not True or audit.get("active_writers") != []
            or not isinstance(audit.get("reason"), str) or not audit["reason"].strip()
            or not isinstance(audit.get("operator"), str) or not audit["operator"].strip()
            or not isinstance(audit.get("recorded_unix"), (int, float))
            or not math.isfinite(audit["recorded_unix"])
            or Path(audit["chain_path"]) != chain_path or Path(audit["outcome_path"]) != outcome_path):
        raise RetryBlockedError("Registro de reconciliación inválido")
    for name in ("chain", "outcome"):
        preimage = journal.parent / f"{name}.before.json"
        if (preimage.is_symlink() or sha256_file(preimage) != audit[f"{name}_before_sha256"]
                or read_json_object(preimage) != audit[f"{name}_before"]):
            raise RetryBlockedError("La evidencia anterior a la reconciliación fue alterada")
    _validate_original(root, chain_path, audit["chain_before"], outcome_path, audit["outcome_before"])
    observations = audit.get("artifacts_before")
    artifacts = audit["chain_before"]["artifacts"]
    if not isinstance(observations, list) or len(observations) != len(artifacts):
        raise RetryBlockedError("Inventario de reconciliación inválido")
    for artifact, observed in zip(artifacts, observations, strict=True):
        if (any(observed.get(key) != artifact[key] for key in ("kind", "path"))
                or type(observed.get("present")) is not bool
                or (observed["present"] and (type(observed.get("size_bytes")) is not int
                    or observed["size_bytes"] < 0 or not isinstance(observed.get("sha256"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", observed["sha256"]) is None))
                or (not observed["present"] and (observed.get("sha256") is not None or observed.get("size_bytes") is not None))):
            raise RetryBlockedError("Inventario de reconciliación contradictorio")
    marker = {"id": audit["reconciliation_id"], "path": journal.as_posix(), "sha256": sha256_file(journal)}
    chain_after, outcome_after = _project(audit, marker)
    return audit, chain_after, outcome_after


def verify_interruption_resolution(chain_path: Path, chain: dict, outcome: dict) -> None:
    """Verify explicit reconciliation, without claiming observed termination."""
    root = chain_path.resolve().parents[3]
    latest = chain["attempts"][-1]
    expected_chain, outcome_path, journal = _paths(root, chain["chain_id"], latest["attempt_id"])
    _, projected_chain, projected_outcome = _load_audit(root, expected_chain, outcome_path, journal)
    if (outcome != projected_outcome or latest != projected_chain["attempts"][-1]
            or any(chain.get(key) != projected_chain.get(key) for key in (
                "chain_id", "manifest_path", "input_sha256", "policy", "artifacts", "preexisting_artifacts"))):
        raise RetryBlockedError("La cadena y el intento no acreditan la misma reconciliación")


def verify_interruption_artifacts(chain_path: Path, chain: dict, outcome: dict) -> None:
    """Do not archive files that appeared/changed after the explicit confirmation."""
    root = chain_path.resolve().parents[3]
    _, outcome_path, journal = _paths(root, chain["chain_id"], outcome["attempt_id"])
    audit, _, _ = _load_audit(root, chain_path, outcome_path, journal)
    archive = outcome_path.parent / "archive.json"
    if archive.exists():
        archived = read_json_object(archive)
        entries = archived["entries"]
        actual = {(e["original_path"], e["size_bytes"], e["sha256"]) for e in entries}
        expected = {(e["path"], e["size_bytes"], e["sha256"]) for e in audit["artifacts_before"] if e["present"]}
        if actual != expected or len(entries) != len(expected):
            raise RetryBlockedError("El archivo de restos contradice la confirmación del operador")
        _verify_archive(archived, chain["artifacts"], outcome_path.parent, reconcile=True)
    elif _artifact_observations(chain["artifacts"]) != audit["artifacts_before"]:
        raise RetryBlockedError("Las salidas cambiaron después de confirmar la interrupción")


def _inspect(root: Path, chain_id: str, expected_attempt: str | None) -> tuple[dict, dict, dict | None]:
    if re.fullmatch(r"[0-9a-f]{32}", chain_id) is None:
        raise RetryBlockedError("chain_id debe ser un UUID hexadecimal")
    chain_path = root / "metadata" / "process_chains" / chain_id / "chain.json"
    chain = read_json_object(chain_path)
    RetryChain(chain_path, chain)
    if not chain["attempts"]:
        raise RetryBlockedError("La cadena no tiene ningún intento reservado")
    attempt_id = chain["attempts"][-1]["attempt_id"]
    if expected_attempt is not None and attempt_id != expected_attempt:
        raise RetryBlockedError("El intento actual cambió; inspeccione la cadena de nuevo")
    chain_path, outcome_path, journal = _paths(root, chain_id, attempt_id)
    outcome = read_json_object(outcome_path)
    if sha256_file(Path(chain["manifest_path"])) != chain["input_sha256"]:
        raise RetryBlockedError("El runtime actual difiere de la entrada interrumpida")
    for name in ("completion", "failure"):
        path = outcome_path.parent / f"{name}.json"
        if Path(outcome[f"{name}_path"]).resolve() != path or path.exists():
            raise RetryBlockedError("Existe evidencia funcional o una ruta contradictoria; requiere revisión independiente")
    if journal.exists():
        audit, chain_after, outcome_after = _load_audit(root, chain_path, outcome_path, journal)
        for path, before_hash, after in ((chain_path, audit["chain_before_sha256"], chain_after),
                                         (outcome_path, audit["outcome_before_sha256"], outcome_after)):
            if sha256_file(path) not in {before_hash, hashlib.sha256(_encoded(after)).hexdigest()}:
                raise RetryBlockedError(f"Publicación de reconciliación ambigua: {path}")
        verify_interruption_artifacts(chain_path, chain_after, outcome_after)
        return chain_after, outcome_after, audit
    _validate_original(root, chain_path, chain, outcome_path, outcome)
    if all(Path(item["path"]).is_file() for item in chain["artifacts"]):
        snapshot = read_json_object(Path(outcome["manifest_snapshot_path"]))
        paths = {"runtime_manifest_path": chain["manifest_path"], "scene_id": outcome["scene_id"],
                 "output_type": outcome["output_type"], "rendered_wav_path": chain["artifacts"][0]["path"],
                 "render_metadata_path": chain["artifacts"][1]["path"]}
        if outcome["output_type"] == "circular_array":
            paths["circular_array"] = snapshot["receiver"]["circular_array"]
        validation = validate_render(paths, metadata_required=True,
                                     manifest_path=Path(outcome["manifest_snapshot_path"]), profile="recovery")
        if validation.artifacts_ok:
            raise RetryBlockedError("Hay salidas funcionalmente completas; no se autoriza repetirlas con esta operación")
    return chain, outcome, None


def reconcile_interrupted_chain(
    run_root: str | Path, chain_id: str, *, apply: bool = False, confirm_stopped: bool = False,
    expected_attempt: str | None = None, reason: str = "", process_probe: ProcessProbe | None = None,
) -> dict[str, Any]:
    """Inspect by default; explicitly reconcile only the pinned interrupted attempt."""
    root = Path(run_root).resolve()
    if not root.is_dir():
        raise RetryBlockedError("El directorio de ejecución no existe")
    if apply and (not confirm_stopped or not reason.strip() or expected_attempt is None):
        raise RetryBlockedError("Aplicar requiere --confirm-stopped, --attempt y --reason explícitos")

    def inspect_and_check() -> tuple[dict, dict, dict | None]:
        try:
            values = _inspect(root, chain_id, expected_attempt)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RetryBlockedError(f"No se pudo verificar la evidencia de la interrupción: {exc}") from exc
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
        return values

    if not apply:
        if (root / "metadata" / "indexes" / "static_writer.lock").exists():
            raise RetryBlockedError("Existe static_writer.lock; no se retira automáticamente")
        chain, outcome, audit = inspect_and_check()
    else:
        with static_writer_lock(root):
            chain, outcome, audit = inspect_and_check()
            chain_path, outcome_path, journal = _paths(root, chain_id, outcome["attempt_id"])
            if audit is None:
                chain_bytes, outcome_bytes = chain_path.read_bytes(), outcome_path.read_bytes()
                now = datetime.now(UTC)
                audit = {"schema_version": 1, "action": "confirm_interrupted_attempt",
                         "reconciliation_id": uuid4().hex, "chain_path": chain_path.as_posix(),
                         "outcome_path": outcome_path.as_posix(), "chain_before": chain, "outcome_before": outcome,
                         "chain_before_sha256": hashlib.sha256(chain_bytes).hexdigest(),
                         "outcome_before_sha256": hashlib.sha256(outcome_bytes).hexdigest(),
                         "recorded_at": now.isoformat(), "recorded_unix": now.timestamp(),
                         "operator": getpass.getuser(), "host": socket.gethostname(), "reason": reason.strip(),
                         "confirmed_stopped": True, "active_writers": [],
                         "artifacts_before": _artifact_observations(chain["artifacts"])}
                journal.parent.mkdir(parents=True, exist_ok=True)
                _exclusive(journal.parent / "chain.before.json", chain_bytes)
                _exclusive(journal.parent / "outcome.before.json", outcome_bytes)
                _save(journal, audit)  # Durable intent precedes either state change.
                _, chain, outcome = _load_audit(root, chain_path, outcome_path, journal)
            # Rerunning after a torn publication completes only this same intent.
            if outcome_path.read_bytes() != _encoded(outcome):
                _save(outcome_path, outcome)
            if chain_path.read_bytes() != _encoded(chain):
                _save(chain_path, chain)
            verify_interruption_resolution(chain_path, chain, outcome)
    consumed = len(chain["attempts"])
    return {"applied": apply, "chain_id": chain_id, "attempt_id": outcome["attempt_id"],
            "scene_id": outcome["scene_id"], "state": chain["state"], "consumed_attempts": consumed,
            "next_state": "scheduled" if consumed < RetryChain(Path(chain["manifest_path"]), chain).policy.max_attempts else "exhausted",
            "remaining_attempts": max(0, RetryChain(Path(chain["manifest_path"]), chain).policy.max_attempts - consumed),
            "exit_code": outcome["exit_code"], "termination_observed": outcome.get("termination_observed", False),
            "reconciliation_path": (Path(outcome["manifest_snapshot_path"]).parent /
                                     "interruption_reconciliation" / "record.json").as_posix() if audit else None}
