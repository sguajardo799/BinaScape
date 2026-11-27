"""Functional acceptance policy, never derived from best-effort diagnostics."""

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from acoustic_orchestrator.pipeline.matlab_runner import MatlabProcessResult
from acoustic_orchestrator.pipeline.render_attempt import CompletionValidation, inspect_completion
from acoustic_orchestrator.pipeline.render_validation import RenderValidation, validate_render
from acoustic_orchestrator.pipeline.render_failure import inspect_failure


RenderDecision = Literal["continue", "continue_with_warning", "retry_acoustic", "abort"]


class RenderValidationError(RuntimeError):
    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("Render not accepted: " + "; ".join(issues))


@dataclass
class RenderAcceptance:
    process: MatlabProcessResult
    validation: RenderValidation
    completion: CompletionValidation
    decision: RenderDecision
    error: Exception | None
    issues: list[str]

    @property
    def recovered(self) -> bool:
        return self.decision == "continue_with_warning"

    def as_record(self) -> dict[str, Any]:
        return {
            "attempt_id": self.process.attempt.attempt_id,
            "attempt_outcome_path": self.process.attempt.outcome_path.as_posix(),
            "completion_path": self.process.attempt.completion_path.as_posix(),
            "process_status": self.process.process_status,
            "exit_code": self.process.exit_code,
            "error": None if self.process.error is None else {
                "type": type(self.process.error).__name__, "message": str(self.process.error),
            },
            "termination_observed": self.process.termination_observed,
            "render_validity": self.validation.validity,
            "validation": self.validation.as_record(),
            "completion_validation": self.completion,
            "decision": self.decision,
            "decision_issues": self.issues,
            "recovery_reason": "post_completion" if self.recovered else None,
        }


def evaluate_render(
    process: MatlabProcessResult, paths: Mapping[str, Any], *, metadata_required: bool, canonical: bool = False,
) -> RenderAcceptance:
    """Combine independent process, completion and render evidence without saving it."""
    completion = inspect_completion(process.attempt.record)
    failure = inspect_failure(process.attempt.record)
    validation = validate_render(
        paths, metadata_required=metadata_required,
        profile="normal" if process.exit_code == 0 else "recovery", canonical=canonical,
        manifest_path=process.attempt.snapshot_path,
    )
    issues = list(validation.as_record()["issues"])
    decision: RenderDecision = "abort"
    if failure["status"] != "missing":
        # A failure report never accredits completion. A valid deterministic
        # rejection and an invalid/contradictory report both fail closed.
        issues.extend(failure["issues"] or ["functional_input_rejected"])
    elif not process.termination_observed or process.exit_code is None:
        issues.append("process_termination_not_observed")
    elif completion["status"] == "invalid":
        issues.extend(completion["issues"])
    elif process.exit_code != 0 and completion["status"] != "valid":
        issues.extend(completion["issues"])
    elif process.error is not None and not (
        process.exit_code != 0 and isinstance(process.error, subprocess.CalledProcessError)
        and process.error.returncode == process.exit_code
    ):
        issues.append("process_functional_error_contradiction")
    elif not validation.artifacts_ok:
        pass
    elif validation.acoustic_rejection is not None:
        decision = "retry_acoustic"
    elif validation.validity == "valid":
        decision = "continue" if process.exit_code == 0 else "continue_with_warning"
    error = (process.error or RenderValidationError(issues or ["render_acceptance_unverifiable"])) if decision == "abort" else None
    return RenderAcceptance(process, validation, completion, decision, error, issues)


def persist_acceptance(acceptance: RenderAcceptance, publish_index: Callable[[], None]) -> None:
    """Commit both functional outcome and index before allowing continuation.

    A crash between publications leaves pending evidence, never an implicit
    success. Resume must require the matching committed outcome and index.
    """
    attempt = acceptance.process.attempt
    original_error = acceptance.error or acceptance.process.error
    evidence = acceptance.as_record()
    attempt.record.update(evidence, commit_status="pending", decision_recorded_at=datetime.now(UTC).isoformat())
    if acceptance.recovered:
        attempt.record["warning"] = {
            "code": "matlab_process_failed_after_completion",
            "message": "Proceso MATLAB fallido; render validado y finalización acreditada. Causa de cierre no confirmada.",
        }
    try:
        attempt.save()
        publish_index()
        attempt.record["commit_status"] = "committed"
        attempt.save()
    except Exception as storage_error:
        acceptance.decision = "abort"
        acceptance.error = original_error or storage_error
        acceptance.issues.append("acceptance_persistence_failed")
        attempt.record.update(
            decision="abort", commit_status="failed", recovery_reason=None,
            decision_issues=acceptance.issues,
            persistence_error={"type": type(storage_error).__name__, "message": str(storage_error)},
        )
        attempt.record.pop("warning", None)
        try:
            attempt.save()
        except Exception as secondary:
            acceptance.error.add_note(f"Failure evidence also could not be persisted: {secondary}")
        acceptance.error.add_note(f"Functional acceptance was not committed: {storage_error}")
        if original_error is not None:
            raise original_error from storage_error
        raise
