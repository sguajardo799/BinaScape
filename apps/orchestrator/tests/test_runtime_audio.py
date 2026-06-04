import wave
from pathlib import Path

from acoustic_orchestrator.pipeline.runtime_audio import prepare_scene_source_assets, prepare_trimmed_wav_copy


def test_prepare_trimmed_wav_copy_trims_overlong_source(tmp_path: Path) -> None:
    audio_path = tmp_path / "long.wav"
    _write_wav(audio_path, duration_s=2.0)

    prepared_path = prepare_trimmed_wav_copy(
        source_id="source_01",
        audio_path=audio_path,
        total_duration_s=1.0,
        prepared_audio_dir=tmp_path / "prepared",
    )

    assert prepared_path is not None
    assert Path(prepared_path).name == "source_01__trim_1.wav"
    assert _wav_duration_s(Path(prepared_path)) == 1.0


def test_prepare_trimmed_wav_copy_leaves_short_source_unchanged(tmp_path: Path) -> None:
    audio_path = tmp_path / "short.wav"
    _write_wav(audio_path, duration_s=0.5)

    prepared_path = prepare_trimmed_wav_copy(
        source_id="source_01",
        audio_path=audio_path,
        total_duration_s=1.0,
        prepared_audio_dir=tmp_path / "prepared",
    )

    assert prepared_path is None
    assert not (tmp_path / "prepared").exists()


def test_prepare_scene_source_assets_uses_deterministic_paths(tmp_path: Path) -> None:
    long_audio = tmp_path / "long.wav"
    short_audio = tmp_path / "short.wav"
    _write_wav(long_audio, duration_s=2.0)
    _write_wav(short_audio, duration_s=0.5)
    scene_manifest = {
        "render": {"target_duration_s": 1.25},
        "sources": [
            {"source_id": "source_01", "audio_path": long_audio.as_posix()},
            {"source_id": "source_02", "audio_path": short_audio.as_posix()},
        ],
    }

    first_rewrites = prepare_scene_source_assets(scene_manifest, tmp_path / "prepared")
    second_rewrites = prepare_scene_source_assets(scene_manifest, tmp_path / "prepared")

    assert first_rewrites == second_rewrites == {
        "source_01": (tmp_path / "prepared" / "source_01__trim_1p25.wav").resolve().as_posix()
    }
    assert _wav_duration_s(Path(first_rewrites["source_01"])) == 1.25


def _write_wav(path: Path, *, duration_s: float, sample_rate_hz: int = 8000) -> None:
    total_frames = int(duration_s * sample_rate_hz)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate_hz)
        wav_file.writeframes(b"\x00\x00" * total_frames)


def _wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getnframes() / wav_file.getframerate()
