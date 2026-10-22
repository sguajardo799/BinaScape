from pathlib import Path
import hashlib
import json
import random
import struct
import wave

import pytest

from acoustic_orchestrator.config.loader import load_config
from acoustic_orchestrator.experiment import sampler
from acoustic_orchestrator.pipeline.render_pipeline import generate_static_manifests
from test_generate_manifests import _build_workspace, _write_config, _write_test_wav
from acoustic_orchestrator.source_audio import list_source_wavs


def test_source_discovery_is_bounded_and_sorted(tmp_path: Path) -> None:
    for name in ["z.wav", "A.WAV", "ID02/clip.wav", "ID01/clip.WaV", "ID01/session/deep.wav"]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        _write_test_wav(tmp_path / name, duration_s=0.1)
    (tmp_path / "labels.json").write_text("{}", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    expected = sorted(tmp_path / name for name in ["z.wav", "A.WAV", "ID02/clip.wav", "ID01/clip.WaV"])
    assert list_source_wavs(tmp_path) == expected
    assert list_source_wavs(tmp_path) == sampler._list_files(tmp_path)


def test_source_pool_accepts_twenty_identity_folders(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    root = workspace / "assets/events/speech"
    (root / "speech_01.wav").unlink()
    for index in range(20):
        folder = root / f"ID{index:02d}"
        folder.mkdir()
        _write_test_wav(folder / "clip.wav", duration_s=0.1)
    config_path = _write_config(workspace, "twenty_ids", num_simulations=1)
    speech = load_config(config_path).source_sampling.source_types[0]
    pool = sampler._build_audio_pools([speech])[speech.event_type]
    assert pool["all_files"] == list_source_wavs(root)
    assert len(pool["all_files"]) == 20
    assert len(generate_static_manifests(config_path)) == 1


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


def test_same_identity_does_not_constrain_selection_or_pool_reuse(tmp_path: Path) -> None:
    workspace = _build_workspace(tmp_path)
    root = workspace / "assets/events/speech"
    (root / "speech_01.wav").unlink()
    (root / "ID01").mkdir()
    for name in ["one.wav", "two.wav"]:
        _write_test_wav(root / "ID01" / name, duration_s=1.0)
    config_path = _write_config(
        workspace, "same_identity", num_simulations=1, min_sources=3, max_sources=3,
        speech_min_count=3, speech_max_count=3, clapping_max_count=0,
    )
    speech = load_config(config_path).source_sampling.source_types[0]
    pool = sampler._build_audio_pools([speech])
    rng = random.Random(123)
    draws = [sampler._draw_audio_path(speech, pool, rng) for _ in range(3)]
    assert len(set(draws[:2])) == 2
    assert draws[2] in draws[:2]
    manifest = json.loads(generate_static_manifests(config_path)[0].read_text(encoding="utf-8"))
    assert [source["identity_id"] for source in manifest["sources"]] == ["ID01"] * 3
    assert len({source["source_id"] for source in manifest["sources"]}) == 3
    assert len({source["audio_path"] for source in manifest["sources"][:2]}) == 2


def test_flat_sampling_matches_pre_identity_baseline(tmp_path: Path) -> None:
    # Captured before adding identity fields; covers all sampled scene properties.
    workspace = _build_workspace(tmp_path, speech_wav_names=["z.wav", "A.WAV", "clip_ID13.wav"])
    # Keep the historical input of this sampling-only baseline. Render tests
    # now default to the backend's supported 44100 Hz instead.
    paths = generate_static_manifests(_write_config(workspace, "identity_baseline", render_sample_rate_hz=48000))
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    for payload in payloads:
        for source in payload["sources"]:
            assert source.pop("identity_id") == ""
            assert source.pop("audio_relpath") == Path(source["audio_path"]).name
    normalized = json.dumps(payloads, sort_keys=True).replace(workspace.as_posix(), "<workspace>")
    assert hashlib.sha256(normalized.encode()).hexdigest() == (
        "cd375b2ee710049b4df505af115d2af8d8ed9bb7563454aca8d3f569641880f3"
    )
