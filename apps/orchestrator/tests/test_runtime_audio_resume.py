from pathlib import Path
import os
import wave

import pytest

from acoustic_orchestrator.pipeline import runtime_audio


def _wav(path: Path, *, channels: int = 2, rate: int = 8000, frames: int = 24000, value: int = 1) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setparams((channels, 2, rate, frames, "NONE", "not compressed"))
        wav.writeframes(value.to_bytes(2, "little") * channels * frames)


def _scene(path: Path, duration: float | None = 1.0) -> dict:
    return {
        "render": {"target_duration_s": duration},
        "sources": [{"source_id": "src_0001", "audio_path": str(path)}],
    }


def _snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def test_valid_asset_is_read_only_in_both_modes(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _wav(source)
    scene = _scene(source)
    directory = tmp_path / "prepared"
    rewrites = runtime_audio.prepare_scene_source_assets(scene, directory)
    prepared = Path(rewrites["src_0001"])
    os.utime(prepared, ns=(1_600_000_000_000_000_000,) * 2)
    before = _snapshot(prepared)
    for create in (False, True, True):
        assert runtime_audio.prepare_scene_source_assets(scene, directory, create=create) == rewrites
        assert _snapshot(prepared) == before
    report = runtime_audio.inspect_scene_source_assets(scene, directory)
    assert report.rewrites == rewrites
    assert report.missing_paths == ()


def test_missing_resolution_does_not_create_directories_or_files(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _wav(source)
    directory = tmp_path / "not-created" / "scene"
    scene = _scene(source)
    report = runtime_audio.inspect_scene_source_assets(scene, directory)
    expected = (directory / "src_0001__trim_1.wav").resolve().as_posix()
    assert report.rewrites == {"src_0001": expected}
    assert report.missing_paths == (expected,)
    assert runtime_audio.prepare_scene_source_assets(scene, directory, create=False) == report.rewrites
    assert not directory.parent.exists()


def test_missing_creation_writes_exact_expected_pcm(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _wav(source)
    rewrites = runtime_audio.prepare_scene_source_assets(_scene(source), tmp_path / "prepared")
    prepared = Path(rewrites["src_0001"])
    with wave.open(str(prepared), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (2, 2, 8000, 8000)
        assert wav.readframes(8000) == b"\x01\x00" * 2 * 8000
    assert list(prepared.parent.iterdir()) == [prepared]


@pytest.mark.parametrize("damage", ["invalid", "payload", "dimensions", "truncated"])
@pytest.mark.parametrize("create", [False, True])
def test_corrupt_asset_conflicts_without_replacement(tmp_path: Path, damage: str, create: bool) -> None:
    source = tmp_path / "source.wav"
    _wav(source)
    scene = _scene(source)
    directory = tmp_path / "prepared"
    prepared = Path(runtime_audio.prepare_scene_source_assets(scene, directory)["src_0001"])
    if damage == "invalid":
        prepared.write_bytes(b"not a WAV")
    elif damage == "payload":
        _wav(prepared, frames=8000, value=2)
    elif damage == "dimensions":
        _wav(prepared, channels=1, frames=8000)
    else:
        prepared.write_bytes(prepared.read_bytes()[:-8])
    before = _snapshot(prepared)
    with pytest.raises(runtime_audio.PreparedAudioConflict, match="Prepared audio conflict") as error:
        runtime_audio.prepare_scene_source_assets(scene, directory, create=create)
    assert str(prepared) in str(error.value)
    assert _snapshot(prepared) == before


def test_source_changed_at_same_path_conflicts(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _wav(source)
    scene = _scene(source)
    directory = tmp_path / "prepared"
    prepared = Path(runtime_audio.prepare_scene_source_assets(scene, directory)["src_0001"])
    before = _snapshot(prepared)
    _wav(source, value=7)
    with pytest.raises(runtime_audio.PreparedAudioConflict, match="PCM payload"):
        runtime_audio.inspect_scene_source_assets(scene, directory)
    assert _snapshot(prepared) == before


@pytest.mark.parametrize("duration", [None, 3.0, 4.0])
def test_no_trim_needs_no_prepared_asset(tmp_path: Path, duration: float | None) -> None:
    source = tmp_path / "source.wav"
    _wav(source)
    before = _snapshot(source)
    directory = tmp_path / "prepared"
    for create in (False, True):
        assert runtime_audio.prepare_scene_source_assets(_scene(source, duration), directory, create=create) == {}
    assert not directory.exists()
    assert _snapshot(source) == before


def test_late_scene_conflict_prevents_earlier_missing_creation(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _wav(source)
    scene = _scene(source)
    directory = tmp_path / "prepared"
    prepared = Path(runtime_audio.prepare_scene_source_assets(scene, directory)["src_0001"])
    prepared.write_bytes(b"corrupt")
    scene["sources"].insert(0, {"source_id": "missing", "audio_path": str(source)})
    before = _snapshot(prepared)
    with pytest.raises(runtime_audio.PreparedAudioConflict):
        runtime_audio.prepare_scene_source_assets(scene, directory)
    assert list(directory.iterdir()) == [prepared]
    assert _snapshot(prepared) == before


def test_concurrent_incompatible_creation_is_not_clobbered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.wav"
    _wav(source)
    directory = tmp_path / "prepared"
    link = os.link
    snapshots = []

    def competing_link(src: Path, dst: Path) -> None:
        _wav(dst, frames=8000, value=8)
        snapshots.append(_snapshot(dst))
        link(src, dst)

    monkeypatch.setattr(runtime_audio.os, "link", competing_link)
    with pytest.raises(runtime_audio.PreparedAudioConflict):
        runtime_audio.prepare_scene_source_assets(_scene(source), directory)
    prepared = directory / "src_0001__trim_1.wav"
    assert _snapshot(prepared) == snapshots[0]
    assert list(directory.iterdir()) == [prepared]


def test_pcm_reads_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.wav"
    _wav(source, frames=80000)
    original = wave.Wave_read.readframes
    requests = []

    def readframes(self: wave.Wave_read, count: int) -> bytes:
        requests.append(count)
        assert 0 <= count <= runtime_audio._CHUNK_FRAMES
        return original(self, count)

    monkeypatch.setattr(wave.Wave_read, "readframes", readframes)
    scene = _scene(source, 8.0)
    runtime_audio.prepare_scene_source_assets(scene, tmp_path / "prepared")
    runtime_audio.inspect_scene_source_assets(scene, tmp_path / "prepared")
    assert len(requests) > 3
