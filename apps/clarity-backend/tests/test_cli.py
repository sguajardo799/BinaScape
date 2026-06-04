import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from clarity_backend import msbg
from clarity_backend.cli import run_manifest


def test_run_manifest_writes_success_metadata_with_resolved_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = tmp_path / "clarity_jobs.jsonl"
    input_wav_path = tmp_path / "input.wav"
    render_metadata_path = tmp_path / "render.json"
    hearing_profiles_path = tmp_path / "hearing_profiles.yaml"

    _write_stereo_wav(input_wav_path)
    render_metadata_path.write_text('{"render":"ok"}', encoding="utf-8")
    hearing_profiles_path.write_text(_hearing_profiles_yaml(), encoding="utf-8")
    monkeypatch.setattr(msbg, "process_wav", _fake_process_wav)

    job = _build_job(tmp_path, input_wav_path, render_metadata_path, hearing_profiles_path)
    manifest_path.write_text(json.dumps(job), encoding="utf-8")

    run_manifest(manifest_path)

    metadata = _read_json(Path(job["expected_output_metadata_path"]))
    assert metadata == {
        "schema_version": "1.0",
        "job_id": job["job_id"],
        "variant_id": job["variant_id"],
        "scene_id": job["scene_id"],
        "output_type": job["output_type"],
        "hearing_profile_id": job["hearing_profile_id"],
        "hearing_profiles_path": hearing_profiles_path.resolve().as_posix(),
        "input_wav_path": input_wav_path.resolve().as_posix(),
        "input_render_metadata_path": render_metadata_path.resolve().as_posix(),
        "output_wav_path": Path(job["expected_output_wav_path"]).resolve().as_posix(),
        "status": "completed",
        "processor": {
            "name": "msbg",
            "package": "pyclarity",
            "package_version": "test",
            "sample_rate_hz": 44100,
            "channels": 2,
        },
        "degradation_applied": {
            "left": {
                "loss_db_by_band": {
                    "250": 10.0,
                    "500": 12.0,
                    "1000": 15.0,
                    "2000": 18.0,
                    "4000": 21.0,
                    "8000": 24.0,
                },
                "audiogram_frequencies_hz": [250, 500, 1000, 2000, 4000, 8000],
            },
            "right": {
                "loss_db_by_band": {
                    "250": 12.0,
                    "500": 14.0,
                    "1000": 17.0,
                    "2000": 20.0,
                    "4000": 23.0,
                    "8000": 26.0,
                },
                "audiogram_frequencies_hz": [250, 500, 1000, 2000, 4000, 8000],
            },
        },
    }
    assert Path(job["expected_output_wav_path"]).read_bytes() == b"processed-by-msbg"


def test_run_manifest_uses_requested_profile_per_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = tmp_path / "clarity_jobs.jsonl"
    input_wav_path = tmp_path / "input.wav"
    render_metadata_path = tmp_path / "render.json"
    hearing_profiles_path = tmp_path / "hearing_profiles.yaml"

    _write_stereo_wav(input_wav_path)
    render_metadata_path.write_text('{"render":"ok"}', encoding="utf-8")
    hearing_profiles_path.write_text(_hearing_profiles_yaml(), encoding="utf-8")
    monkeypatch.setattr(msbg, "process_wav", _fake_process_wav)

    severe_job = _build_job(
        tmp_path,
        input_wav_path,
        render_metadata_path,
        hearing_profiles_path,
        hearing_profile_id="severe_loss",
        output_suffix="severe_loss",
    )
    manifest_path.write_text(json.dumps(severe_job), encoding="utf-8")

    run_manifest(manifest_path)

    metadata = _read_json(Path(severe_job["expected_output_metadata_path"]))
    assert metadata["hearing_profile_id"] == "severe_loss"
    assert metadata["degradation_applied"]["left"]["loss_db_by_band"]["4000"] == 46.0
    assert metadata["degradation_applied"]["right"]["loss_db_by_band"]["8000"] == 54.0


def test_run_manifest_honors_non_fixed_output_filenames_in_shared_profile_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "clarity_jobs.jsonl"
    render_metadata_path = tmp_path / "render.json"
    hearing_profiles_path = tmp_path / "hearing_profiles.yaml"
    first_input_wav_path = tmp_path / "scene_static_0001__binaural_hrtf.wav"
    second_input_wav_path = tmp_path / "scene_static_0002__binaural_hrtf.wav"

    _write_stereo_wav(first_input_wav_path)
    _write_stereo_wav(second_input_wav_path)
    render_metadata_path.write_text('{"render":"ok"}', encoding="utf-8")
    hearing_profiles_path.write_text(_hearing_profiles_yaml(), encoding="utf-8")
    monkeypatch.setattr(msbg, "process_wav", _fake_process_wav)

    shared_output_dir = tmp_path / "degraded" / "binaural_hrtf" / "mild_loss"
    first_job = _build_job(
        tmp_path,
        first_input_wav_path,
        render_metadata_path,
        hearing_profiles_path,
        output_dir=shared_output_dir,
        output_wav_name="scene_static_0001__binaural_hrtf.wav",
        output_metadata_name="scene_static_0001__binaural_hrtf.json",
    )
    second_job = _build_job(
        tmp_path,
        second_input_wav_path,
        render_metadata_path,
        hearing_profiles_path,
        output_dir=shared_output_dir,
        output_wav_name="scene_static_0002__binaural_hrtf.wav",
        output_metadata_name="scene_static_0002__binaural_hrtf.json",
    )
    manifest_path.write_text(
        "\n".join([json.dumps(first_job), json.dumps(second_job)]),
        encoding="utf-8",
    )

    run_manifest(manifest_path)

    assert Path(first_job["expected_output_wav_path"]).is_file()
    assert Path(second_job["expected_output_wav_path"]).is_file()
    assert Path(first_job["expected_output_metadata_path"]).is_file()
    assert Path(second_job["expected_output_metadata_path"]).is_file()
    assert Path(first_job["expected_output_wav_path"]).parent == Path(second_job["expected_output_wav_path"]).parent
    assert Path(first_job["expected_output_wav_path"]).name != Path(second_job["expected_output_wav_path"]).name
    assert _read_json(Path(first_job["expected_output_metadata_path"]))["output_wav_path"] == Path(
        first_job["expected_output_wav_path"]
    ).resolve().as_posix()
    assert _read_json(Path(second_job["expected_output_metadata_path"]))["output_wav_path"] == Path(
        second_job["expected_output_wav_path"]
    ).resolve().as_posix()


@pytest.mark.parametrize("missing_field", ["input_wav_path", "input_render_metadata_path"])
def test_run_manifest_writes_failure_metadata_for_missing_input(tmp_path: Path, missing_field: str) -> None:
    manifest_path = tmp_path / "clarity_jobs.jsonl"
    input_wav_path = tmp_path / "input.wav"
    render_metadata_path = tmp_path / "render.json"
    hearing_profiles_path = tmp_path / "hearing_profiles.yaml"

    _write_stereo_wav(input_wav_path)
    render_metadata_path.write_text('{"render":"ok"}', encoding="utf-8")
    hearing_profiles_path.write_text(_hearing_profiles_yaml(), encoding="utf-8")

    job = _build_job(tmp_path, input_wav_path, render_metadata_path, hearing_profiles_path)
    Path(job[missing_field]).unlink()
    manifest_path.write_text(json.dumps(job), encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        run_manifest(manifest_path)

    assert exc_info.value.code == 1
    metadata = _read_json(Path(job["expected_output_metadata_path"]))
    assert metadata == {
        "schema_version": "1.0",
        "job_id": job["job_id"],
        "variant_id": job["variant_id"],
        "scene_id": job["scene_id"],
        "output_type": job["output_type"],
        "hearing_profile_id": job["hearing_profile_id"],
        "hearing_profiles_path": hearing_profiles_path.resolve().as_posix(),
        "input_wav_path": input_wav_path.resolve().as_posix(),
        "input_render_metadata_path": render_metadata_path.resolve().as_posix(),
        "output_wav_path": None,
        "status": "failed",
        "error": metadata["error"],
    }
    assert metadata["error"]["type"] == "FileNotFoundError"
    assert job["job_id"] in metadata["error"]["message"]


def test_run_manifest_continues_after_profile_resolution_failure(tmp_path: Path) -> None:
    manifest_path = tmp_path / "clarity_jobs.jsonl"
    input_wav_path = tmp_path / "input.wav"
    render_metadata_path = tmp_path / "render.json"
    hearing_profiles_path = tmp_path / "hearing_profiles.yaml"

    _write_stereo_wav(input_wav_path)
    render_metadata_path.write_text('{"render":"ok"}', encoding="utf-8")
    hearing_profiles_path.write_text(_hearing_profiles_yaml(), encoding="utf-8")

    failed_job = _build_job(
        tmp_path,
        input_wav_path,
        render_metadata_path,
        hearing_profiles_path,
        hearing_profile_id="unknown_profile",
        output_suffix="unknown_profile",
    )
    successful_job = _build_job(
        tmp_path,
        input_wav_path,
        render_metadata_path,
        hearing_profiles_path,
        hearing_profile_id="mild_loss",
        output_suffix="mild_loss",
    )
    manifest_path.write_text(
        "\n".join([json.dumps(failed_job), json.dumps(successful_job)]),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        run_manifest(manifest_path)

    assert exc_info.value.code == 1

    failed_metadata = _read_json(Path(failed_job["expected_output_metadata_path"]))
    assert failed_metadata["status"] == "failed"
    assert failed_metadata["error"] == {
        "type": "ValueError",
        "message": f"No existe hearing_profile_id 'unknown_profile' en {hearing_profiles_path.resolve()}",
    }

    successful_metadata = _read_json(Path(successful_job["expected_output_metadata_path"]))
    assert successful_metadata["status"] == "completed"
    assert Path(successful_job["expected_output_wav_path"]).is_file()


def test_run_manifest_reports_unreadable_audio_and_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = tmp_path / "clarity_jobs.jsonl"
    invalid_input_wav_path = tmp_path / "invalid.wav"
    valid_input_wav_path = tmp_path / "valid.wav"
    render_metadata_path = tmp_path / "render.json"
    hearing_profiles_path = tmp_path / "hearing_profiles.yaml"

    invalid_input_wav_path.write_bytes(b"not a wav")
    _write_stereo_wav(valid_input_wav_path)
    render_metadata_path.write_text('{"render":"ok"}', encoding="utf-8")
    hearing_profiles_path.write_text(_hearing_profiles_yaml(), encoding="utf-8")
    monkeypatch.setattr(msbg, "process_wav", _passthrough_fake_process_wav)

    failed_job = _build_job(
        tmp_path,
        invalid_input_wav_path,
        render_metadata_path,
        hearing_profiles_path,
        hearing_profile_id="mild_loss",
        output_suffix="invalid_audio",
    )
    successful_job = _build_job(
        tmp_path,
        valid_input_wav_path,
        render_metadata_path,
        hearing_profiles_path,
        hearing_profile_id="mild_loss",
        output_suffix="valid_audio",
    )

    def process_wav_with_invalid_audio(input_wav_path: Path, *args, **kwargs):
        if input_wav_path == invalid_input_wav_path.resolve():
            raise ValueError(f"No se pudo leer el WAV de entrada para MSBG: {invalid_input_wav_path.resolve()}: boom")
        return _passthrough_fake_process_wav(input_wav_path, *args, **kwargs)

    monkeypatch.setattr(msbg, "process_wav", process_wav_with_invalid_audio)
    manifest_path.write_text(
        "\n".join([json.dumps(failed_job), json.dumps(successful_job)]),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        run_manifest(manifest_path)

    assert exc_info.value.code == 1
    failed_metadata = _read_json(Path(failed_job["expected_output_metadata_path"]))
    assert failed_metadata["status"] == "failed"
    assert failed_metadata["output_wav_path"] is None
    assert failed_metadata["error"]["type"] == "ValueError"
    assert "No se pudo leer el WAV de entrada para MSBG" in failed_metadata["error"]["message"]

    successful_metadata = _read_json(Path(successful_job["expected_output_metadata_path"]))
    assert successful_metadata["status"] == "completed"
    assert successful_metadata["output_wav_path"] == Path(successful_job["expected_output_wav_path"]).resolve().as_posix()


def test_build_audiograms_rejects_unsupported_band() -> None:
    with pytest.raises(ValueError, match="banda no soportada"):
        msbg.build_audiograms(
            {
                "hearing_profile_id": "bad_profile",
                "left_loss_db_by_band": {
                    "250": 10.0,
                    "500": 12.0,
                    "1000": 15.0,
                    "2000": 18.0,
                    "3000": 20.0,
                    "4000": 21.0,
                    "8000": 24.0,
                },
                "right_loss_db_by_band": {
                    "250": 10.0,
                    "500": 12.0,
                    "1000": 15.0,
                    "2000": 18.0,
                    "4000": 21.0,
                    "8000": 24.0,
                },
            }
        )


def test_run_manifest_fails_for_duplicate_hearing_profile_ids(tmp_path: Path) -> None:
    manifest_path = tmp_path / "clarity_jobs.jsonl"
    input_wav_path = tmp_path / "input.wav"
    render_metadata_path = tmp_path / "render.json"
    hearing_profiles_path = tmp_path / "hearing_profiles.yaml"

    _write_stereo_wav(input_wav_path)
    render_metadata_path.write_text('{"render":"ok"}', encoding="utf-8")
    hearing_profiles_path.write_text(
        "\n".join(
            [
                "profiles:",
                "  - hearing_profile_id: mild_loss",
                "    ears:",
                "      left:",
                "        loss_db_by_band:",
                "          250: 10",
                "          500: 12",
                "          1000: 15",
                "          2000: 18",
                "          4000: 21",
                "          8000: 24",
                "      right:",
                "        loss_db_by_band:",
                "          250: 12",
                "          500: 14",
                "          1000: 17",
                "          2000: 20",
                "          4000: 23",
                "          8000: 26",
                "  - hearing_profile_id: mild_loss",
                "    ears:",
                "      left:",
                "        loss_db_by_band:",
                "          250: 10",
                "          500: 12",
                "          1000: 15",
                "          2000: 18",
                "          4000: 21",
                "          8000: 24",
                "      right:",
                "        loss_db_by_band:",
                "          250: 12",
                "          500: 14",
                "          1000: 17",
                "          2000: 20",
                "          4000: 23",
                "          8000: 26",
            ]
        ),
        encoding="utf-8",
    )

    job = _build_job(tmp_path, input_wav_path, render_metadata_path, hearing_profiles_path)
    manifest_path.write_text(json.dumps(job), encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        run_manifest(manifest_path)

    assert exc_info.value.code == 1
    metadata = _read_json(Path(job["expected_output_metadata_path"]))
    assert metadata["status"] == "failed"
    assert metadata["error"]["type"] == "ValueError"
    assert "hearing_profile_id duplicado" in metadata["error"]["message"]


def test_build_audiograms_rejects_non_finite_loss() -> None:
    with pytest.raises(ValueError, match="valores numéricos finitos"):
        msbg.build_audiograms(
            {
                "hearing_profile_id": "bad_profile",
                "left_loss_db_by_band": {
                    "250": 10.0,
                    "500": 12.0,
                    "1000": float("nan"),
                    "2000": 18.0,
                    "4000": 21.0,
                    "8000": 24.0,
                },
                "right_loss_db_by_band": {
                    "250": 10.0,
                    "500": 12.0,
                    "1000": 15.0,
                    "2000": 18.0,
                    "4000": 21.0,
                    "8000": 24.0,
                },
            }
        )


def test_process_wav_degrades_supported_stereo_wav(tmp_path: Path) -> None:
    input_wav_path = tmp_path / "input.wav"
    output_wav_path = tmp_path / "output.wav"
    _write_stereo_wav(input_wav_path)

    left_audiogram, right_audiogram, _ = msbg.build_audiograms(
        {
            "hearing_profile_id": "mild_loss",
            "left_loss_db_by_band": {
                "250": 10.0,
                "500": 12.0,
                "1000": 15.0,
                "2000": 18.0,
                "4000": 21.0,
                "8000": 24.0,
            },
            "right_loss_db_by_band": {
                "250": 12.0,
                "500": 14.0,
                "1000": 17.0,
                "2000": 20.0,
                "4000": 23.0,
                "8000": 26.0,
            },
        }
    )

    processor = msbg.process_wav(input_wav_path, output_wav_path, left_audiogram, right_audiogram)

    processed_audio, sample_rate = sf.read(output_wav_path, always_2d=True)
    original_audio, _ = sf.read(input_wav_path, always_2d=True)
    assert output_wav_path.is_file()
    assert sample_rate == 44100
    assert processed_audio.shape == original_audio.shape
    assert processor["name"] == "msbg"
    assert processor["channels"] == 2
    assert not (processed_audio == original_audio).all()


def _build_job(
    tmp_path: Path,
    input_wav_path: Path,
    render_metadata_path: Path,
    hearing_profiles_path: Path,
    *,
    hearing_profile_id: str = "mild_loss",
    output_suffix: str | None = None,
    output_dir: Path | None = None,
    output_wav_name: str | None = None,
    output_metadata_name: str | None = None,
) -> dict[str, object]:
    suffix = output_suffix or hearing_profile_id
    resolved_output_dir = output_dir or (tmp_path / "degraded" / "binaural_hrtf" / suffix)
    resolved_output_wav_name = output_wav_name or "scene_static_0001__binaural_hrtf.wav"
    resolved_output_metadata_name = output_metadata_name or "scene_static_0001__binaural_hrtf.json"
    return {
        "schema_version": "1.0",
        "run_id": "sim_test",
        "job_id": f"clarity__scene_static_0001__binaural_hrtf__{hearing_profile_id}",
        "variant_id": "scene_static_0001__binaural_hrtf",
        "scene_id": "scene_static_0001",
        "output_type": "binaural_hrtf",
        "hearing_profile_id": hearing_profile_id,
        "hearing_profiles_path": str(hearing_profiles_path),
        "backend_invocation": {
            "mode": "uv_project",
            "entrypoint": "clarity-backend",
            "use_uv": True,
            "backend_project_path": None,
        },
        "input_wav_path": str(input_wav_path),
        "input_render_metadata_path": str(render_metadata_path),
        "output_dir": str(resolved_output_dir),
        "expected_output_wav_path": str(resolved_output_dir / resolved_output_wav_name),
        "expected_output_metadata_path": str(resolved_output_dir / resolved_output_metadata_name),
    }


def _hearing_profiles_yaml() -> str:
    return "\n".join(
        [
            "profiles:",
            "  - hearing_profile_id: mild_loss",
            "    ears:",
            "      left:",
            "        loss_db_by_band:",
            "          250: 10",
            "          500: 12",
            "          1000: 15",
            "          2000: 18",
            "          4000: 21",
            "          8000: 24",
            "      right:",
            "        loss_db_by_band:",
            "          250: 12",
            "          500: 14",
            "          1000: 17",
            "          2000: 20",
            "          4000: 23",
            "          8000: 26",
            "  - hearing_profile_id: severe_loss",
            "    ears:",
            "      left:",
            "        loss_db_by_band:",
            "          250: 35",
            "          500: 38",
            "          1000: 40",
            "          2000: 43",
            "          4000: 46",
            "          8000: 49",
            "      right:",
            "        loss_db_by_band:",
            "          250: 40",
            "          500: 42",
            "          1000: 45",
            "          2000: 48",
            "          4000: 51",
            "          8000: 54",
        ]
    )


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fake_process_wav(
    input_wav_path: Path,
    output_wav_path: Path,
    *args,
    **kwargs,
) -> dict[str, object]:
    del input_wav_path, args, kwargs
    output_wav_path.write_bytes(b"processed-by-msbg")
    return {
        "name": "msbg",
        "package": "pyclarity",
        "package_version": "test",
        "sample_rate_hz": 44100,
        "channels": 2,
    }


def _passthrough_fake_process_wav(
    input_wav_path: Path,
    output_wav_path: Path,
    *args,
    **kwargs,
) -> dict[str, object]:
    del args, kwargs
    output_wav_path.write_bytes(input_wav_path.read_bytes())
    return {
        "name": "msbg",
        "package": "pyclarity",
        "package_version": "test",
        "sample_rate_hz": 44100,
        "channels": 2,
    }


def _write_stereo_wav(path: Path) -> None:
    sample_rate = 44100
    time = np.linspace(0, 0.01, int(sample_rate * 0.01), endpoint=False)
    audio = np.column_stack((0.1 * np.sin(2 * np.pi * 440 * time), 0.1 * np.sin(2 * np.pi * 660 * time)))
    sf.write(path, audio, sample_rate, format="WAV", subtype="PCM_16")
