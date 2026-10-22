import json
from pathlib import Path

import pytest

from acoustic_orchestrator.pipeline.render_attempt import sha256_file
from acoustic_orchestrator.pipeline.render_failure import inspect_failure


@pytest.fixture
def evidence(tmp_path: Path):
    snapshot = tmp_path / "input_manifest.json"
    snapshot.write_bytes(b'{"invalid":true}')
    expected = {
        "attempt_id": "a" * 32, "variant_id": "scene__hrtf", "scene_id": "scene",
        "output_type": "binaural_hrtf", "manifest_sha256": sha256_file(snapshot),
        "manifest_snapshot_path": str(snapshot), "completion_path": str(tmp_path / "completion.json"),
    }
    report = {key: expected[key] for key in ("attempt_id", "variant_id", "manifest_sha256")}
    report.update(schema_version=1, category="invalid_input", error={"identifier": "", "message": "invalid input"})
    return expected, report, tmp_path / "failure.json"


def test_absent_and_valid_report(evidence):
    expected, report, path = evidence
    assert inspect_failure(expected)["status"] == "missing"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert inspect_failure(expected) == {"status": "valid", "issues": [], "category": "invalid_input"}
    for field in ("scene_id", "output_type"):
        report[field] = expected[field]
    path.write_text(json.dumps(report), encoding="utf-8")
    assert inspect_failure(expected)["status"] == "valid"


@pytest.mark.parametrize(("field", "value"), [
    ("attempt_id", "b" * 32), ("attempt_id", "not-an-id"), ("variant_id", "foreign"),
    ("manifest_sha256", "0" * 64), ("scene_id", "foreign"), ("output_type", "foreign"),
    ("schema_version", 2), ("schema_version", True), ("category", "simulation"),
    ("error", {}), ("error", {"identifier": 2, "message": "bad"}),
    ("error", {"identifier": "", "message": ""}), ("artifacts", []),
    ("completion", True), ("diagnostic", {"number": float("inf")}),
])
def test_foreign_or_malformed_report(evidence, field, value):
    expected, report, path = evidence
    report[field] = value
    path.write_text(json.dumps(report), encoding="utf-8")
    result = inspect_failure(expected)
    assert result["status"] == "invalid"
    assert result["issues"]
    assert result["category"] is None


@pytest.mark.parametrize("text", ["{", "[]", '{"a":1,"a":2}', '{"x":NaN}', '{"x":1e999}',
                                      '{"error":{"message":"one","message":"two"}}'])
def test_strict_json(evidence, text):
    expected, _, path = evidence
    path.write_text(text, encoding="utf-8")
    assert inspect_failure(expected)["status"] == "invalid"


def test_snapshot_tampering(evidence):
    expected, report, path = evidence
    path.write_text(json.dumps(report), encoding="utf-8")
    Path(expected["manifest_snapshot_path"]).write_bytes(b"changed")
    assert "failure_snapshot_hash_mismatch" in inspect_failure(expected)["issues"]


@pytest.mark.parametrize("completion", ["{}", "not even JSON"])
def test_failure_and_completion_always_contradict(evidence, completion):
    expected, report, path = evidence
    path.write_text(json.dumps(report), encoding="utf-8")
    Path(expected["completion_path"]).write_text(completion, encoding="utf-8")
    result = inspect_failure(expected)
    assert result["status"] == "invalid"
    assert "failure_completion_contradiction" in result["issues"]


def test_explicit_path_and_no_diagnostic_dependence(evidence):
    expected, report, path = evidence
    expected["failure_path"] = str(path.with_name("explicit.json"))
    Path(expected["failure_path"]).write_text(json.dumps(report), encoding="utf-8")
    expected["diagnostics"] = {"untrusted_console": "success"}
    assert inspect_failure(expected)["status"] == "valid"
    expected["exit_code"] = 0
    assert "failure_process_contradiction" in inspect_failure(expected)["issues"]


def test_unreadable_report_and_missing_snapshot(evidence):
    expected, report, path = evidence
    path.mkdir()
    assert inspect_failure(expected)["status"] == "invalid"
    path.rmdir()
    path.write_text(json.dumps(report), encoding="utf-8")
    Path(expected["manifest_snapshot_path"]).unlink()
    assert inspect_failure(expected)["status"] == "invalid"
