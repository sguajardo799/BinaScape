"""[pra][Linux] Example configuration and MATLAB-free end-to-end ``render-static`` (tasks 9.1, 9.2)."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pra_support import require_pyroomacoustics, write_material_files, write_tone_wav

REPOSITORY = Path(__file__).resolve().parents[3]
EXAMPLE = REPOSITORY / "configs" / "experiments" / "circular_array_pyroomacoustics.yml"


def example_copy(root: Path) -> Path:
    """The example YAML unchanged, in a workspace mirroring the repository layout with fixture assets."""
    config = root / "configs" / "experiments" / EXAMPLE.name
    config.parent.mkdir(parents=True)
    shutil.copy(EXAMPLE, config)
    (root / "configs" / "hearing").mkdir(parents=True)
    shutil.copy(REPOSITORY / "configs" / "hearing" / "example_profile.yaml", root / "configs" / "hearing")
    speech = root / "assets" / "events" / "train" / "speech"
    write_tone_wav(speech / "speech_00.wav", seconds=0.4, sample_rate_hz=16000, frequency_hz=350.0)
    write_tone_wav(speech / "speech_01.wav", seconds=0.3, sample_rate_hz=44100, frequency_hz=520.0, channels=2)
    write_material_files(root / "assets" / "materials")
    return config


def test_example_configuration_validates(tmp_path: Path) -> None:
    require_pyroomacoustics()
    from acoustic_orchestrator.config.loader import load_config
    from acoustic_orchestrator.config.validator import validate_config

    config = load_config(example_copy(tmp_path))
    validate_config(config)
    assert config.render.backend == "pyroomacoustics" and config.raven is None
    assert config.receiver_outputs.circular_array.num_microphones == 3
    assert config.receiver_outputs.circular_array.radius_m == 0.05
    assert config.background_noise.enabled and config.background_noise.strategies[0].type == "colored"
    assert [shape.type for shape in config.room_sampling.geometry.shape_mix] == ["shoebox"]
    assert "raven:" not in EXAMPLE.read_text(encoding="utf-8")


def test_example_is_tracked_not_ignored() -> None:
    if shutil.which("git") is None or not (REPOSITORY / ".git").exists():
        pytest.skip("git checkout required")
    ignored = subprocess.run(["git", "check-ignore", "-q", EXAMPLE.as_posix()], cwd=REPOSITORY)
    assert ignored.returncode == 1


def _cli() -> list[str]:
    script = Path(sys.executable).with_name("acoustic-orchestrator")
    return [script.as_posix()] if script.is_file() else [sys.executable, "-m", "acoustic_orchestrator.cli"]


def test_render_static_end_to_end_without_matlab(tmp_path: Path) -> None:
    require_pyroomacoustics()
    from acoustic_orchestrator.config.loader import load_config
    from acoustic_orchestrator.pipeline.output_index import load_variant_index
    from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
    from acoustic_orchestrator.pipeline.render_attempt import inspect_completion, read_json_object

    config_path = example_copy(tmp_path)
    path_dirs = [Path(sys.executable).parent.as_posix(), "/usr/bin", "/bin"]
    environment = {**os.environ, "PATH": os.pathsep.join(path_dirs)}
    environment.pop("VIRTUAL_ENV", None)
    assert shutil.which("matlab", path=environment["PATH"]) is None
    command = [*_cli(), "render-static", config_path.as_posix(), "--no-progress"]
    first = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=1800)
    assert first.returncode == 0, first.stdout + first.stderr

    layout = resolve_artifact_layout(load_config(config_path).outputs, "circular_array_pyroomacoustics")
    [record] = load_variant_index(layout["render_index_path"]).values()
    assert record["status"] == "completed" and record["validation"]["ok"] is True
    outcome = read_json_object(Path(record["attempt_outcome_path"]).with_name("outcome.json"))
    assert inspect_completion(outcome) == {"status": "valid", "issues": []}
    import soundfile

    info = soundfile.info(record["rendered_wav_path"])
    assert (info.channels, info.samplerate, info.subtype, info.frames) == (3, 44100, "PCM_16", 88200)
    sidecar = read_json_object(Path(record["render_metadata_path"]))
    assert sidecar["summary"]["render_backend"] == "Pyroomacoustics"
    assert sidecar["backend"]["name"] == "pyroomacoustics" and sidecar["backend"]["simulation"]["method"] == "ism"
    assert sidecar["summary"]["room"]["reverberation"]["status"] == "estimated"
    assert sidecar["summary"]["background_noise"]["applied"] is True
    attempts = sorted(p.parent.name for p in layout["render_attempts_dir"].glob("*/outcome.json"))

    second = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=600)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "invocaciones=0" in second.stdout
    assert sorted(p.parent.name for p in layout["render_attempts_dir"].glob("*/outcome.json")) == attempts
    (tmp_path / "e2e_run_root.txt").write_text(layout["run_root"].as_posix(), encoding="utf-8")
