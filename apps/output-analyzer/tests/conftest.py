from __future__ import annotations

import json

import pytest


@pytest.fixture
def sample_run(tmp_path):
    run = tmp_path / "sim_test"
    render_dir = run / "outputs" / "render" / "binaural_hrtf"
    scene_dir = run / "manifests" / "scene"
    degraded_dir = run / "outputs" / "degraded" / "binaural_hrtf" / "mild_loss"
    render_dir.mkdir(parents=True)
    scene_dir.mkdir(parents=True)
    degraded_dir.mkdir(parents=True)

    scene = {
        "scene_id": "scene_static_0001",
        "job_id": "render_job_static_0001",
        "scene_type": "static",
        "room": {"dimensions_m": [8.0, 6.0, 3.0]},
        "receiver": {
            "position_m": [4.0, 1.5, 3.0],
            "orientation_deg": {"yaw": 30.0, "pitch": 0.0, "roll": 0.0},
            "hrtfs": [{"hrtf_id": "binaural_hrtf", "hrtf_path": "D:/assets/hrtf/id25_HRTF.v17.ir.daff"}],
        },
        "sources": [
            {
                "source_id": "src_0001",
                "event_type": "speech",
                "audio_path": "D:/assets/events/speech/example.wav",
                "position_m": [2.0, 1.5, 2.0],
                "gain_db": -1.0,
                "start_time_s": 0.1,
                "directivity_path": "D:/assets/directivity/singer.daff",
            },
            {
                "source_id": "src_0002",
                "event_type": "traffic",
                "audio_path": "D:/assets/events/traffic/example.wav",
                "position_m": [6.0, 1.0, 5.0],
                "gain_db": -2.0,
                "start_time_s": 0.2,
            },
        ],
    }
    render = {
        "scene_id": "scene_static_0001",
        "job_id": "render_job_static_0001",
        "scene_type": "static",
        "sample_rate_hz": 44100,
        "hrtf_id": "binaural_hrtf",
        "hrtf_path": "D:/assets/hrtf/id25_HRTF.v17.ir.daff",
        "output_wav_path": "D:/outputs/sim_test/outputs/render/binaural_hrtf/scene_static_0001__binaural_hrtf.wav",
        "summary": {
            "n_sources": 2,
            "room": {"reverberation": {"mean_t30_s": 0.42}},
            "background_noise": {"applied": True},
            "sources": scene["sources"],
        },
    }
    degraded = {
        "scene_id": "scene_static_0001",
        "variant_id": "scene_static_0001__binaural_hrtf",
        "job_id": "clarity__scene_static_0001__binaural_hrtf__mild_loss",
        "output_type": "binaural_hrtf",
        "hearing_profile_id": "mild_loss",
        "status": "completed",
        "input_render_metadata_path": "D:/outputs/sim_test/outputs/render/binaural_hrtf/scene_static_0001__binaural_hrtf__render.json",
        "input_wav_path": "D:/outputs/sim_test/outputs/render/binaural_hrtf/scene_static_0001__binaural_hrtf.wav",
        "output_wav_path": "D:/outputs/sim_test/outputs/degraded/binaural_hrtf/mild_loss/scene_static_0001__binaural_hrtf.wav",
        "processor": {"name": "msbg", "package": "pyclarity", "package_version": "0.8.0", "sample_rate_hz": 44100, "channels": 2},
        "degradation_applied": {
            "left": {"loss_db_by_band": {"250": 10.0, "500": 15.0, "1000": 20.0}},
            "right": {"loss_db_by_band": {"250": 15.0, "500": 20.0, "1000": 25.0}},
        },
    }

    (scene_dir / "scene_static_0001.json").write_text(json.dumps(scene), encoding="utf-8")
    (render_dir / "scene_static_0001__binaural_hrtf__render.json").write_text(json.dumps(render), encoding="utf-8")
    (degraded_dir / "scene_static_0001__binaural_hrtf.json").write_text(json.dumps(degraded), encoding="utf-8")
    return run
