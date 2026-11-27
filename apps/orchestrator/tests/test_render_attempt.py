import hashlib
import json
import subprocess
import re
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest

from acoustic_orchestrator.pipeline.render_attempt import (
    RenderAttempt, inspect_completion, read_json_object, sha256_file,
)
from acoustic_orchestrator.pipeline.matlab_diagnostics import MatlabDiagnostics
from acoustic_orchestrator.pipeline.matlab_runner import render_manifest_with_matlab


def write_runtime(tmp_path: Path) -> Path:
    path = tmp_path / "scene__binaural_hrtf.json"
    path.write_text(json.dumps({
        "schema_version": "3.0", "scene_id": "scene",
        "render": {"active_output_type": "binaural_hrtf",
                   "output_wav_path": (tmp_path / "out.wav").as_posix(),
                   "output_metadata_path": (tmp_path / "out.json").as_posix()},
    }, indent=2) + "\n", encoding="utf-8")
    return path


def test_snapshot_preserves_exact_bytes_and_previous_attempts(tmp_path):
    runtime = write_runtime(tmp_path)
    original = runtime.read_bytes()
    first = RenderAttempt(runtime)
    runtime.write_bytes(original + b" \r\n")
    second = RenderAttempt(runtime)
    assert first.directory != second.directory
    assert UUID(first.attempt_id) and UUID(second.attempt_id)
    assert first.snapshot_path.read_bytes() == original
    assert second.snapshot_path.read_bytes() == original + b" \r\n"
    assert first.manifest_sha256 == hashlib.sha256(original).hexdigest()
    assert sha256_file(first.snapshot_path) == first.manifest_sha256
    assert first.record["manifest_sha256"] != second.record["manifest_sha256"]
    assert read_json_object(first.snapshot_path)["scene_id"] == "scene"
    assert not first.completion_path.exists()
    initial = read_json_object(first.outcome_path)
    assert initial["process_status"] == "running"
    assert initial["exit_code"] is None
    assert initial["finished_at"] is None
    assert initial["decision"] == "pending"
    assert initial["validation"]["ok"] is False
    assert initial["manifest_snapshot_path"] == first.snapshot_path.as_posix()
    assert initial["variant_id"] == "scene__binaural_hrtf"


def test_explicit_storage_and_atomic_outcome(tmp_path, monkeypatch):
    runtime = write_runtime(tmp_path)
    root = tmp_path / "run" / "metadata" / "render_attempts"
    calls = []
    replace = Path.replace

    def observe(source, target):
        assert source.parent == target.parent
        assert source.name == "outcome.json.tmp"
        assert read_json_object(source)["attempt_id"] == source.parent.name
        calls.append(target)
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", observe)
    attempt = RenderAttempt(runtime, root)
    assert attempt.directory.parent == root
    assert calls == [attempt.outcome_path]
    assert not attempt.outcome_path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize("failure", ["directory", "snapshot", "outcome"])
def test_preparation_failure_is_not_suppressed(tmp_path, monkeypatch, failure):
    runtime = write_runtime(tmp_path)
    root = tmp_path / "attempts"
    old = RenderAttempt(runtime, root)
    previous = old.outcome_path.read_bytes()
    original_open = Path.open
    original_mkdir = Path.mkdir

    def open_file(path, *args, **kwargs):
        if path.is_relative_to(root) and path.parent != old.directory:
            if (failure == "snapshot" and path.name == "input_manifest.json"
                    or failure == "outcome" and path.name == "outcome.json.tmp"):
                raise PermissionError("functional storage denied")
        return original_open(path, *args, **kwargs)

    def mkdir(path, *args, **kwargs):
        if failure == "directory" and path.is_relative_to(root) and path != old.directory:
            raise PermissionError("functional storage denied")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(PermissionError, match="functional storage denied"):
        RenderAttempt(runtime, root)
    assert old.outcome_path.read_bytes() == previous


def test_parallel_attempts_keep_snapshots_and_state_isolated(tmp_path):
    runtime = write_runtime(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as executor:
        attempts = list(executor.map(lambda _: RenderAttempt(runtime), range(6)))
    assert len({attempt.attempt_id for attempt in attempts}) == 6
    for attempt in attempts:
        assert attempt.snapshot_path.read_bytes() == runtime.read_bytes()
        assert read_json_object(attempt.outcome_path)["attempt_id"] == attempt.attempt_id


def test_sha256_streams_multiple_blocks(tmp_path):
    path = tmp_path / "large"
    content = bytes(range(256)) * 9000
    path.write_bytes(content)
    assert sha256_file(path) == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("content", [b"[]", b'{"scene_id":NaN}', b'{"scene_id":"a","scene_id":"b"}'])
def test_invalid_manifest_never_prepares_an_attempt(tmp_path, content):
    runtime = tmp_path / "invalid.json"
    runtime.write_bytes(content)
    with pytest.raises(ValueError):
        RenderAttempt(runtime)
    assert not (tmp_path / "render_attempts").exists()


def write_completion(attempt):
    artifacts = []
    for item in attempt.expected_artifacts:
        path = Path(item["path"])
        path.write_bytes(b"test artifact bytes: " + item["kind"].encode())
        artifacts.append({**item, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    payload = {key: attempt.record[key] for key in (
        "attempt_id", "scene_id", "variant_id", "output_type", "manifest_sha256",
    )}
    payload.update(schema_version=1, artifacts=artifacts)
    attempt.completion_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_complete_receipt_checks_integrity_not_audio_validity(tmp_path):
    attempt = RenderAttempt(write_runtime(tmp_path))
    write_completion(attempt)
    # These test artifacts are intentionally not valid audio/metadata: the
    # separate render validator, not this integrity reader, must reject them.
    assert inspect_completion(attempt.record) == {"status": "valid", "issues": []}


@pytest.mark.parametrize("field,value,reason", [
    ("schema_version", 2, "completion_schema_unsupported"),
    ("schema_version", True, "completion_schema_unsupported"),
    ("attempt_id", "other", "completion_attempt_id_mismatch"),
    ("scene_id", "other", "completion_scene_id_mismatch"),
    ("variant_id", "other", "completion_variant_id_mismatch"),
    ("output_type", "other", "completion_output_type_mismatch"),
    ("manifest_sha256", "0" * 64, "completion_manifest_hash_mismatch"),
    ("manifest_sha256", 5, "completion_manifest_hash_mismatch"),
    ("artifacts", {}, "completion_artifacts_invalid"),
    ("artifacts", [], "completion_artifacts_invalid"),
])
def test_receipt_identity_types_and_schema(tmp_path, field, value, reason):
    attempt = RenderAttempt(write_runtime(tmp_path))
    payload = write_completion(attempt)
    payload[field] = value
    attempt.completion_path.write_text(json.dumps(payload), encoding="utf-8")
    result = inspect_completion(attempt.record)
    assert result["status"] == "invalid"
    assert reason in result["issues"]


@pytest.mark.parametrize("case,reason", [
    ("missing", "completion_artifact_inventory_mismatch"),
    ("duplicate", "completion_artifact_duplicate"),
    ("extra", "completion_artifact_unexpected"),
    ("relative", "completion_artifact_invalid"),
    ("kind", "completion_artifact_unexpected"),
    ("path", "completion_artifact_unexpected"),
    ("size", "completion_artifact_size_mismatch"),
    ("bool_size", "completion_artifact_fields_invalid"),
    ("negative_size", "completion_artifact_fields_invalid"),
    ("hash", "completion_artifact_hash_mismatch"),
    ("invalid_hash", "completion_artifact_fields_invalid"),
    ("non_object", "completion_artifact_invalid"),
])
def test_receipt_requires_exact_inventory(tmp_path, case, reason):
    attempt = RenderAttempt(write_runtime(tmp_path))
    payload = write_completion(attempt)
    items = payload["artifacts"]
    if case == "missing":
        items.pop()
    elif case == "duplicate":
        items.append(dict(items[0]))
    elif case == "extra":
        items.append({**items[0], "path": (tmp_path / "extra.wav").as_posix()})
    elif case == "non_object":
        items[0] = []
    else:
        field, value = {
            "relative": ("path", "out.wav"), "kind": ("kind", "unexpected"),
            "path": ("path", (tmp_path / "unexpected.wav").as_posix()),
            "size": ("size_bytes", 999), "bool_size": ("size_bytes", True),
            "negative_size": ("size_bytes", -1), "hash": ("sha256", "0" * 64),
            "invalid_hash": ("sha256", "not-a-sha256"),
        }[case]
        items[0][field] = value
    attempt.completion_path.write_text(json.dumps(payload), encoding="utf-8")
    result = inspect_completion(attempt.record)
    assert result["status"] == "invalid"
    assert any(issue.startswith(reason) for issue in result["issues"])


@pytest.mark.parametrize("target", ["snapshot", "output_same_size", "output_larger", "output_removed"])
def test_files_cannot_change_after_receipt(tmp_path, target):
    attempt = RenderAttempt(write_runtime(tmp_path))
    write_completion(attempt)
    path = attempt.snapshot_path if target == "snapshot" else Path(attempt.expected_artifacts[0]["path"])
    if target == "output_removed":
        path.unlink()
    elif target == "output_same_size":
        path.write_bytes(b"x" * path.stat().st_size)
    else:
        path.write_bytes(path.read_bytes() + b" ")
    result = inspect_completion(attempt.record)
    assert result["status"] == "invalid"
    assert any("snapshot_hash_mismatch" in issue if target == "snapshot" else "artifact_" in issue
               for issue in result["issues"])


@pytest.mark.parametrize("content", [b"[]", b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b"\xff", b'{"broken":'])
def test_corrupt_or_ambiguous_receipt_is_not_evidence(tmp_path, content):
    attempt = RenderAttempt(write_runtime(tmp_path))
    attempt.completion_path.write_bytes(content)
    result = inspect_completion(attempt.record)
    assert result["status"] == "invalid"
    assert any(issue.startswith("completion_unreadable") for issue in result["issues"])


def test_temporary_receipt_never_counts_as_published(tmp_path):
    attempt = RenderAttempt(write_runtime(tmp_path))
    write_completion(attempt)
    attempt.completion_path.rename(attempt.completion_path.with_suffix(".json.tmp"))
    assert inspect_completion(attempt.record) == {"status": "missing", "issues": ["completion_missing"]}


def test_receipt_cannot_open_unexpected_artifact_paths(tmp_path, monkeypatch):
    attempt = RenderAttempt(write_runtime(tmp_path))
    payload = write_completion(attempt)
    untrusted = tmp_path / "untrusted.wav"
    payload["artifacts"][0]["path"] = untrusted.as_posix()
    attempt.completion_path.write_text(json.dumps(payload), encoding="utf-8")
    original = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path != untrusted
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert inspect_completion(attempt.record)["status"] == "invalid"


def test_receipt_for_previous_attempt_is_rejected_even_with_same_outputs(tmp_path):
    runtime = write_runtime(tmp_path)
    old = RenderAttempt(runtime)
    write_completion(old)
    current = RenderAttempt(runtime)
    current.completion_path.write_bytes(old.completion_path.read_bytes())
    assert "completion_attempt_id_mismatch" in inspect_completion(current.record)["issues"]


@pytest.mark.parametrize("failure", ["directory", "events", "summary"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_diagnostics_share_identity_without_owning_functional_state(tmp_path, monkeypatch, failure, exit_code):
    attempt = RenderAttempt(write_runtime(tmp_path))
    write_completion(attempt)
    # Pretend the future functional runner has observed this process result.
    attempt.record.update(process_status="succeeded" if exit_code == 0 else "failed", exit_code=exit_code)
    attempt.save()
    functional_files = {path: path.read_bytes() for path in attempt.directory.iterdir()}
    root = tmp_path / "diagnostics"

    def fail(*args, **kwargs):
        raise PermissionError("diagnostic storage unavailable")

    if failure == "directory":
        original_mkdir = Path.mkdir

        def mkdir(path, *args, **kwargs):
            if path.is_relative_to(root):
                fail()
            return original_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", mkdir)
    elif failure == "events":
        monkeypatch.setattr(MatlabDiagnostics, "read_events", fail)
    else:
        monkeypatch.setattr(MatlabDiagnostics, "save", fail)

    with pytest.warns(RuntimeWarning):
        diagnostics = MatlabDiagnostics(
            attempt.snapshot_path, root, attempt_id=attempt.attempt_id, variant_id=attempt.variant_id,
        )
        console = diagnostics.begin(["matlab", "-batch", "test"])
        diagnostics.close_console(console)
        error = subprocess.CalledProcessError(exit_code, "matlab") if exit_code else None
        diagnostics.finish(exit_code, error)
    assert diagnostics.attempt_id == attempt.attempt_id
    assert diagnostics.record["variant_id"] == attempt.variant_id
    assert diagnostics.record["process_status"] == attempt.record["process_status"]
    assert diagnostics.record["exit_code"] == exit_code
    assert diagnostics.directory.parent == root
    assert all(path.read_bytes() == content for path, content in functional_files.items())
    assert inspect_completion(attempt.record)["status"] == "valid"


def test_documented_receipt_matches_contract_fixture(tmp_path):
    docs = (Path(__file__).resolve().parents[3] / "docs" / "architecture" / "contracts.md").read_text(encoding="utf-8")
    section = docs.split("## Constancia funcional de render estático", 1)[1].split("## Geometría", 1)[0]
    example = json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])
    attempt = RenderAttempt(write_runtime(tmp_path))
    fixture = write_completion(attempt)
    assert example.keys() == fixture.keys()
    assert example["schema_version"] == fixture["schema_version"]
    assert [item["kind"] for item in example["artifacts"]] == [item["kind"] for item in fixture["artifacts"]]
    assert all(item.keys() == fixture["artifacts"][0].keys() for item in example["artifacts"])
    assert "no declaran que un WAV sea válido" in section
    assert inspect_completion(attempt.record)["status"] == "valid"


@pytest.mark.parametrize("exit_code", [0, 1, 7])
def test_runner_passes_immutable_snapshot_and_functional_context(tmp_path, monkeypatch, exit_code):
    runtime = write_runtime(tmp_path)
    original = runtime.read_bytes()
    functional = tmp_path / "functional"
    diagnostics = tmp_path / "diagnostics"

    def run(command, **kwargs):
        snapshot_match = re.search(r"run_raven_static_render\('((?:[^']|'')*)'", command[2])
        snapshot = Path(snapshot_match[1].replace("''", "'"))
        assert snapshot.read_bytes() == original
        assert snapshot.parent.parent == functional
        initial = read_json_object(snapshot.parent / "outcome.json")
        assert initial["process_status"] == "running"
        assert initial["command"] == command
        assert initial["attempt_id"] in command[2]
        assert initial["manifest_sha256"] in command[2]
        assert initial["completion_path"] in command[2]
        assert runtime != snapshot
        runtime.write_bytes(original + b"\n")
        return SimpleNamespace(returncode=exit_code)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    result = render_manifest_with_matlab(runtime, attempts_dir=functional, diagnostics_dir=diagnostics)
    assert result.exit_code == exit_code
    assert result.termination_observed
    assert result.process_status == ("succeeded" if exit_code == 0 else "failed")
    assert (result.error is None) == (exit_code == 0)
    outcome = read_json_object(result.attempt.outcome_path)
    assert outcome["process_status"] == result.process_status
    assert outcome["exit_code"] == exit_code
    assert outcome["duration_s"] >= 0
    assert outcome["decision"] == "pending"  # The runner never authorizes acceptance.
    assert result.attempt.snapshot_path.read_bytes() == original
    diagnostic = read_json_object(diagnostics / result.attempt.attempt_id / "invocation.json")
    assert Path(diagnostic["manifest_snapshot_path"]).read_bytes() == original


def test_runner_does_not_launch_if_functional_preparation_fails(tmp_path, monkeypatch):
    runtime = write_runtime(tmp_path)
    functional = tmp_path / "functional"
    functional.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(OSError):
        render_manifest_with_matlab(runtime, attempts_dir=functional)


def test_runner_does_not_invent_termination_for_execution_exception(tmp_path, monkeypatch):
    runtime = write_runtime(tmp_path)
    original_error = RuntimeError("execution result unavailable")

    def run(*args, **kwargs):
        raise original_error

    monkeypatch.setattr("acoustic_orchestrator.pipeline.matlab_runner.subprocess.run", run)
    result = render_manifest_with_matlab(runtime)
    assert result.error is original_error
    assert result.exit_code is None
    assert result.process_status == "termination_unobserved"
    assert not result.termination_observed
    diagnostic = read_json_object(tmp_path / "matlab_diagnostics" / result.attempt.attempt_id / "invocation.json")
    assert diagnostic["process_status"] == "termination_unobserved"
    assert diagnostic["failure_location"] == "indeterminate"


@pytest.mark.parametrize("size,spelling", [(24, "2.4E+1"), (1411244, "1.411244E+6")])
def test_matlab_integral_exponent_artifact_size_is_exactly_verified(tmp_path, size, spelling):
    attempt = RenderAttempt(write_runtime(tmp_path))
    payload = write_completion(attempt)
    wav = Path(attempt.expected_artifacts[0]["path"])
    wav.write_bytes(b"x" * size)
    payload["artifacts"][0].update(size_bytes=size, sha256=sha256_file(wav))
    text = json.dumps(payload).replace(f'"size_bytes": {size}', f'"size_bytes": {spelling}', 1)
    attempt.completion_path.write_text(text, encoding="utf-8")
    decoded = read_json_object(attempt.completion_path)
    assert type(decoded["artifacts"][0]["size_bytes"]) is int
    assert decoded["artifacts"][0]["size_bytes"] == size
    assert inspect_completion(attempt.record)["status"] == "valid"


@pytest.mark.parametrize("spelling", ["2.40000000000000001E+1", "9.007199254740993E+15"])
def test_fractional_or_unsafe_exponent_sizes_are_not_rounded_into_integers(tmp_path, spelling):
    attempt = RenderAttempt(write_runtime(tmp_path))
    payload = write_completion(attempt)
    original_size = payload["artifacts"][0]["size_bytes"]
    text = json.dumps(payload).replace(f'"size_bytes": {original_size}', f'"size_bytes": {spelling}', 1)
    attempt.completion_path.write_text(text, encoding="utf-8")
    result = inspect_completion(attempt.record)
    assert result["status"] == "invalid"
    assert any(issue.startswith("completion_artifact_fields_invalid") for issue in result["issues"])
