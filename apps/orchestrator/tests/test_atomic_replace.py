from pathlib import Path

import pytest

from acoustic_orchestrator.pipeline import atomic_replace as publication


def storage_error(code):
    error = PermissionError(13, "denied", "source.tmp", code, "destination.json")
    # Also exercise this contract on non-Windows test hosts.
    error.winerror = code
    error.add_note("original note")
    return error


@pytest.mark.parametrize("code", [5, 32, 33])
@pytest.mark.parametrize("failures", [0, 1, 2, 3, 4, 5])
def test_retry_budget_and_original_exception(monkeypatch, tmp_path, code, failures):
    source, target = tmp_path / "source.tmp", tmp_path / "destination.json"
    source.write_bytes(b"prepared once")
    original_replace = Path.replace
    error = storage_error(code)
    attributes = (type(error), error.errno, error.winerror, error.filename, error.filename2, str(error))
    calls, sleeps = [], []

    def replace(path, destination):
        calls.append((path, destination, path.read_bytes()))
        if len(calls) <= failures:
            raise error
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(publication.time, "sleep", sleeps.append)
    if failures == 5:
        with pytest.raises(PermissionError) as caught:
            publication.atomic_replace(source, target)
        assert caught.value is error
        assert attributes == (type(error), error.errno, error.winerror, error.filename, error.filename2, str(error))
        assert error.__notes__[0] == "original note"
        assert "5 attempts (0.75 s scheduled wait)" in error.__notes__[1]
        assert str(source) in error.__notes__[1] and str(target) in error.__notes__[1]
        assert source.read_bytes() == b"prepared once"
        assert not target.exists()
    else:
        publication.atomic_replace(source, target)
        assert target.read_bytes() == b"prepared once"
        assert error.__notes__ == ["original note"]
    assert sleeps == [0.05, 0.10, 0.20, 0.40][:min(failures, 4)]
    assert len(calls) == min(failures + 1, 5)
    assert all(call == (source, target, b"prepared once") for call in calls)


@pytest.mark.parametrize("error", [storage_error(112), storage_error(3), PermissionError("denied"),
                                  FileNotFoundError("missing"), KeyboardInterrupt()])
def test_other_errors_propagate_immediately(monkeypatch, tmp_path, error):
    calls, sleeps = [], []

    def replace(*args):
        calls.append(args)
        raise error

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(publication.time, "sleep", sleeps.append)
    with pytest.raises(type(error)) as caught:
        publication.atomic_replace(tmp_path / "source", tmp_path / "target")
    assert caught.value is error
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize("publisher", ["outcome", "chain", "circuit", "journal", "summary"])
@pytest.mark.parametrize("failures", [2, 5])
def test_publishers_retry_closed_temporary_without_rewriting(monkeypatch, tmp_path, publisher, failures):
    from acoustic_orchestrator.pipeline import persistence_journal, process_retries, render_pipeline
    from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt

    target = tmp_path / f"{publisher}.json"
    record = {"test": "prepared once"}
    if publisher == "outcome":
        handle = RenderAttempt.__new__(RenderAttempt)
        handle.outcome_path, handle.record = target, record
        publish = handle.save
    elif publisher in {"chain", "circuit"}:
        publish = lambda: process_retries._save(target, record)
    elif publisher == "journal":
        publish = lambda: persistence_journal.atomic_write(target, persistence_journal.json_bytes(record))
    else:
        publish = lambda: render_pipeline._publish_run_summary(target, record)
    original_replace = Path.replace
    original_open = Path.open
    calls, writes, sleeps = [], [], []

    def open_file(path, mode="r", *args, **kwargs):
        if "w" in mode:
            writes.append(path)
        return original_open(path, mode, *args, **kwargs)

    def replace(path, destination):
        # Windows itself rejects replacement of a still-open temporary.
        calls.append((path.read_bytes(), path.stat().st_mtime_ns))
        if len(calls) <= failures:
            raise storage_error(32)
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(publication.time, "sleep", sleeps.append)
    if failures == 5:
        with pytest.raises(PermissionError):
            publish()
        assert not target.exists()
    else:
        publish()
        assert target.exists()
    assert len(writes) == 1
    assert all(call == calls[0] for call in calls)
    assert len(calls) == min(failures + 1, 5)
    assert sleeps == [0.05, 0.10, 0.20, 0.40][:min(failures, 4)]


def test_index_keeps_its_own_retry_policy(monkeypatch, tmp_path):
    from acoustic_orchestrator.pipeline import output_index

    calls, sleeps = [], []

    def replace(*args):
        calls.append(args)
        raise storage_error(5)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(output_index.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError):
        output_index._replace_index(tmp_path / "source", tmp_path / "index")
    assert len(calls) == 10
    assert sleeps == [0.01] * 9
