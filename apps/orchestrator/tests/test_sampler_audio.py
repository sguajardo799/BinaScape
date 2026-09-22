from pathlib import Path
import struct
import wave

import pytest

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.experiment import sampler
from acoustic_orchestrator.pipeline.render_pipeline import generate_static_manifests
from test_generate_manifests import _build_workspace, _write_config, _write_test_wav


def test_source_pool_ignores_sidecars_and_accepts_uppercase_wav(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path, speech_wav_names=["speech.WAV"])
    (workspace / "assets/events/speech/labels.json").write_text("{}", encoding="utf-8")
    config_path = _write_config(workspace, "audio_pool", num_simulations=1)
    config = load_config(config_path)
    speech = config.source_sampling.source_types[0]
    pool = sampler._build_audio_pools([speech])[speech.event_type]
    assert pool["all_files"] == [workspace / "assets/events/speech/speech.WAV"]
    assert len(generate_static_manifests(config_path)) == 1


def test_source_pool_without_wav_reports_directory(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    audio_dir = workspace / "assets/events/speech"
    (audio_dir / "speech_01.wav").unlink()
    (audio_dir / "unsupported.mp3").write_bytes(b"ID3")
    config = load_config(_write_config(workspace, "no_wav"))
    with pytest.raises(ValueError, match="WAV") as error:
        sampler._build_audio_pools([config.source_sampling.source_types[0]])
    assert str(audio_dir) in str(error.value)


@pytest.mark.parametrize("allow_offsets", [True, False])
@pytest.mark.parametrize("contents", [b"not a WAV file", b"", b"RIFF"])
def test_invalid_source_audio_reports_path_and_cause(
    tmp_path: Path, allow_offsets: bool, contents: bytes
) -> None:
    workspace = _build_workspace(tmp_path)
    config_path = _write_config(workspace, "invalid_audio", num_simulations=1, total_duration_s=3.0)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("allow_offsets: true", f"allow_offsets: {str(allow_offsets).lower()}"),
        encoding="utf-8",
    )
    audio_path = workspace / "assets/events/speech/speech_01.wav"
    audio_path.write_bytes(contents)
    with pytest.raises(ValueError, match="WAV") as error:
        generate_static_manifests(config_path)
    assert str(audio_path) in str(error.value)
    assert isinstance(error.value.__cause__, (wave.Error, EOFError))


def test_unreadable_audio_preserves_os_error(tmp_path: Path) -> None:
    audio_path = tmp_path / "missing.wav"
    with pytest.raises(ValueError) as error:
        sampler._wav_duration_s(audio_path)
    assert str(audio_path) in str(error.value)
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_unsupported_wav_encoding_reports_pcm_requirement(tmp_path: Path) -> None:
    audio_path = tmp_path / "float.wav"
    fmt = struct.pack("<HHIIHH", 3, 1, 8000, 32000, 4, 32)
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", 4) + struct.pack("<f", 0.0)
    audio_path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    with pytest.raises(ValueError, match="PCM") as error:
        sampler._wav_duration_s(audio_path)
    assert str(audio_path) in str(error.value)
    assert isinstance(error.value.__cause__, wave.Error)


def test_valid_pcm_duration_is_preserved(tmp_path: Path) -> None:
    audio_path = tmp_path / "valid.wav"
    _write_test_wav(audio_path, duration_s=0.25)
    assert sampler._wav_duration_s(audio_path) == 0.25
