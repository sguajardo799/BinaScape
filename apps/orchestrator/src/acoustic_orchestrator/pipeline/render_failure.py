"""Strict functional input-failure evidence; never consult console or diagnostics."""

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict

from acoustic_orchestrator.pipeline.render_attempt import read_json_object, sha256_file


class FailureValidation(TypedDict):
    status: Literal["missing", "valid", "invalid"]
    issues: list[str]
    category: Literal["invalid_input"] | None


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


def inspect_failure(expected: Mapping[str, Any]) -> FailureValidation:
    """Verify schema 1 against an independently prepared attempt record.

    An old record may omit failure_path: infer it alongside completion.json.
    Missing evidence proves no cause. A present completion (even malformed) and
    a failure report are contradictory and must block recovery/retry; callers
    must check this result before scheduling or accepting a failed attempt.
    Only a valid result exposes a trusted category.
    """
    issues: list[str] = []
    try:
        path_value = expected.get("failure_path")
        if path_value is None:
            path_value = Path(expected["completion_path"]).with_name("failure.json")
        path = Path(path_value)
        if not path.is_absolute():
            raise ValueError("failure_path must be absolute")
        try:
            path.stat()
        except FileNotFoundError:
            return {"status": "missing", "issues": ["failure_missing"], "category": None}

        report = read_json_object(path)
        if not _finite(report):
            raise ValueError("non-finite JSON value")
        required = {"schema_version", "attempt_id", "variant_id", "manifest_sha256", "category", "error"}
        if not required <= report.keys() or report.keys() - required - {"scene_id", "output_type"}:
            issues.append("failure_fields_invalid")
        if type(report.get("schema_version")) is not int or report["schema_version"] != 1:
            issues.append("failure_schema_unsupported")
        for field in ("attempt_id", "variant_id", "scene_id", "output_type"):
            if field in ("scene_id", "output_type") and field not in report:
                continue
            value = report.get(field)
            if not isinstance(value, str) or not value.strip() or value != expected.get(field):
                issues.append(f"failure_{field}_mismatch")
        if not isinstance(report.get("attempt_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", report["attempt_id"]):
            issues.append("failure_attempt_id_invalid")
        digest = report.get("manifest_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != expected.get("manifest_sha256"):
            issues.append("failure_manifest_hash_mismatch")
        if sha256_file(Path(expected["manifest_snapshot_path"])) != expected["manifest_sha256"]:
            issues.append("failure_snapshot_hash_mismatch")
        if report.get("category") != "invalid_input":
            issues.append("failure_category_invalid")
        error = report.get("error")
        if (not isinstance(error, dict) or set(error) != {"identifier", "message"}
                or not isinstance(error.get("identifier"), str)
                or not isinstance(error.get("message"), str) or not error["message"].strip()):
            issues.append("failure_error_invalid")
        completion = expected.get("completion_path")
        if completion is not None:
            try:
                Path(completion).stat()
            except FileNotFoundError:
                pass
            else:
                issues.append("failure_completion_contradiction")
        if expected.get("exit_code") == 0 or expected.get("decision") in {"continue", "continue_with_warning", "retry_acoustic"}:
            issues.append("failure_process_contradiction")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        issues.append(f"failure_unreadable: {exc}")
    return {"status": "invalid" if issues else "valid", "issues": issues,
            "category": None if issues else "invalid_input"}
