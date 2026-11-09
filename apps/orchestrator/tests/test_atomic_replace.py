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
