import wave
import json
import yaml
from copy import deepcopy
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


def test_prepare_grouped_audio_does_not_mutate_provenance(tmp_path: Path) -> None:
    original = tmp_path / "ID02/long.wav"
    original.parent.mkdir()
    _write_wav(original, duration_s=2.0)
    manifest = {
        "render": {"target_duration_s": 1.0},
        "sources": [{"source_id": "src_0001", "audio_path": original.as_posix(),
                     "identity_id": "ID02", "audio_relpath": "ID02/long.wav"}],
    }
    before = deepcopy(manifest)
    rewrites = prepare_scene_source_assets(manifest, tmp_path / "prepared")
    assert manifest == before
    assert Path(rewrites["src_0001"]).name == "src_0001__trim_1.wav"
    assert _wav_duration_s(original) == 2.0


def test_all_receiver_manifests_preserve_trimmed_provenance(tmp_path: Path) -> None:
    from acoustic_orchestrator.config.loader import load_config
    from acoustic_orchestrator.pipeline.matlab_runner import receiver_variants, write_single_receiver_render_manifest
    from acoustic_orchestrator.pipeline.render_pipeline import generate_static_manifests
    from test_generate_manifests import _build_workspace, _write_config

    workspace = _build_workspace(tmp_path)
    root = workspace / "assets/events/speech"
    (root / "speech_01.wav").unlink()
    (root / "ID02").mkdir()
    _write_wav(root / "ID02/long.wav", duration_s=2.0)
    config_path = _write_config(workspace, "all_provenance", num_simulations=1, total_duration_s=1.0)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["receiver_outputs"]["circular_array"] = {"enabled": True, "num_microphones": 3, "radius_m": 0.05}
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = load_config(config_path)
    scene_path = generate_static_manifests(config_path)[0]
    saved_text = scene_path.read_text(encoding="utf-8")
    scene = json.loads(saved_text)
    before = deepcopy(scene)
    rewrites = prepare_scene_source_assets(scene, tmp_path / "prepared")
    variants = receiver_variants(scene)
    assert len(variants) == 4
    for variant in variants:
        output_type = variant.get("output_type", variant.get("hrtf_id"))
        output = getattr(config.receiver_outputs, output_type)
        path = write_single_receiver_render_manifest(scene, variant, tmp_path / "runtime", config.outputs, output, rewrites)
        runtime = json.loads(path.read_text(encoding="utf-8"))
        for original, source in zip(scene["sources"], runtime["sources"], strict=True):
            expected = {**original, "audio_path": rewrites.get(original["source_id"], original["audio_path"])}
            assert source == expected
    assert scene == before
    assert scene_path.read_text(encoding="utf-8") == saved_text


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
