"""Capture RAVEN-path planning artifacts for compatibility regression tests.

The goldens under ``tests/fixtures/raven_golden/`` were captured from the
unmodified code at ``develop@eb7cb92`` (task 1.4 of
``add-pyroomacoustics-render-backend``). They pin, for the shipped example
configurations:

- the normalized static configuration used for plan identity;
- the scene and runtime manifests produced with fixture assets and a fake
  MATLAB runner;
- the exact MATLAB command list built by ``render_manifest_with_matlab``.

Machine-specific prefixes are replaced by ``<REPO>`` and ``<TMP>``.
The static input is pinned under ``inputs/`` so local experiment edits do not
change this historical compatibility boundary. Expected snapshots are unchanged.
Regenerate only on purpose: ``python tests/raven_golden.py`` (never as a side
effect of a test run).
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path, PureWindowsPath

import yaml

REPOSITORY = Path(__file__).resolve().parents[3]
GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "raven_golden"
EXAMPLES = ("circular_array_only", "static_example")

# Source event types each example samples from (audio_dir names under assets/events/train).
_EVENT_TYPES = {
    "circular_array_only": ["speech"],
    "static_example": ["speech", "keyboard_typing", "cough", "sneeze", "clock_alarm"],
}
_MATERIALS = ["bricks", "concrete", "glass", "drywall", "wood", "plaster"]


def _placeholders(value: object, replacements: list[tuple[str, str]]) -> object:
    if isinstance(value, str):
        value = value.replace("\\", "/")
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_placeholders(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _placeholders(item, replacements) for key, item in value.items()}
    return value


def example_input(name: str) -> tuple[Path, Path]:
    frozen_root = GOLDEN_DIR / "inputs"
    frozen = frozen_root / "configs" / "experiments" / f"{name}.yml"
    if frozen.is_file():
        return frozen, frozen_root
    return REPOSITORY / "configs" / "experiments" / f"{name}.yml", REPOSITORY


def normalized_example_config(name: str, *, explicit_raven_backend: bool = False, tmp_dir: Path | None = None) -> dict:
    """Normalized historical example input (no assets needed)."""
    from acoustic_orchestrator.config.loader import load_config
    from acoustic_orchestrator.pipeline.static_plan import normalized_static_config

    path, root = example_input(name)
    text = path.read_text(encoding="utf-8")
    if explicit_raven_backend:
        assert tmp_dir is not None
        # Mirror the repository layout so relative paths resolve to the same
        # placeholder-relative locations as the shipped file.
        root = tmp_dir / "repo"
        copy = root / "configs" / "experiments" / f"{name}.yml"
        copy.parent.mkdir(parents=True)
        assert text.count("\nrender:\n") == 1
        copy.write_text(text.replace("\nrender:\n", "\nrender:\n  backend: raven\n"), encoding="utf-8")
        path = copy
    config = load_config(path)
    normalized = normalized_static_config(config)
    # The original snapshots were captured on POSIX, where a Windows install
    # path is relative to the YAML directory. Canonicalize that fixture-only
    # representation on Windows as well; do not change production path resolution.
    base_rpf = yaml.safe_load(text).get("raven", {}).get("base_rpf_file")
    if base_rpf and PureWindowsPath(base_rpf).is_absolute():
        normalized["raven"]["base_rpf_file"] = root.resolve().as_posix() + "/configs/experiments/" + base_rpf
    return _placeholders(normalized, [(root.resolve().as_posix(), "<REPO>")])


def _write_wav(path: Path, *, duration_s: float, sample_rate_hz: int = 8000, frequency_hz: float = 440.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(duration_s * sample_rate_hz)
    data = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * frequency_hz * n / sample_rate_hz)))
        for n in range(frames)
    )
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate_hz)
        audio.writeframes(data)


def _material_text(absorption: float) -> str:
    values = ", ".join(f"{absorption + 0.01 * (i % 5):.4f}" for i in range(31))
    scatter = ", ".join("0.1000" for _ in range(31))
    return "\n".join(["[Material]", "name= Fixture", "notes= golden fixture", f"absorp= {values}",
                      f"scatter= {scatter}", "interpol= " + ", ".join(["1"] * 31)])


def build_example_workspace(root: Path, name: str) -> Path:
    """Copy the example config into a fixture workspace with synthetic assets.

    Only the Windows RPF path, the scene count and the Clarity submission are
    adjusted; every acoustic sampling parameter is the example's own.
    """
    workspace = root / "workspace"
    experiments = workspace / "configs" / "experiments"
    experiments.mkdir(parents=True)
    (workspace / "configs" / "hearing").mkdir(parents=True)
    shutil.copy(REPOSITORY / "configs" / "hearing" / "example_profile.yaml",
                workspace / "configs" / "hearing" / "example_profile.yaml")
    (workspace / "apps" / "clarity-backend").mkdir(parents=True)
    from render_fixtures import write_test_rpf
    write_test_rpf(workspace / "base_room.rpf")
    assets = workspace / "assets"
    (assets / "hrtf").mkdir(parents=True)
    (assets / "hrtf" / "subject01.daff").write_text("dummy", encoding="utf-8")
    (assets / "directivity").mkdir(parents=True)
    (assets / "directivity" / "Singer_2011_FWE_TLE_norm.daff").write_text("dummy", encoding="utf-8")
    for number, event_type in enumerate(_EVENT_TYPES[name]):
        for variant in range(2):
            _write_wav(assets / "events" / "train" / event_type / f"{event_type}_{variant:02d}.wav",
                       duration_s=1.0 + 0.5 * variant, frequency_hz=220.0 * (number + 1))
    for number, material in enumerate(_MATERIALS):
        (assets / "materials" / material).mkdir(parents=True)
        (assets / "materials" / material / f"{material}.mat").write_text(
            _material_text(0.05 + 0.03 * number), encoding="utf-8")

    input_path, _ = example_input(name)
    text = input_path.read_text(encoding="utf-8")
    text = text.replace("C:/ITASoftware/Raven/RavenInput/Classroom/Classroom.rpf", "../../base_room.rpf")
    text = text.replace("num_simulations: 100", "num_simulations: 2")
    text = text.replace("hearing_degradation:\n  enabled: true", "hearing_degradation:\n  enabled: false")
    config_path = experiments / f"{name}.yml"
    config_path.write_text(text, encoding="utf-8")
    return config_path


def capture_example_manifests(root: Path, name: str, monkeypatch) -> dict:
    """Scene and runtime manifests of the example with a fake MATLAB runner."""
    from acoustic_orchestrator.config.loader import load_config
    from acoustic_orchestrator.pipeline.output_paths import resolve_artifact_layout
    from acoustic_orchestrator.pipeline.render_pipeline import render_static_scenes
    from render_fixtures import simulate_matlab_process, write_render_metadata, write_render_wav

    config_path = build_example_workspace(root, name)
    config = load_config(config_path)
    layout = resolve_artifact_layout(config.outputs, config.experiment.experiment_id)

    def fake_render(manifest_path: Path, *args, **kwargs) -> None:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        write_render_wav(manifest)
        write_render_metadata(manifest)

    monkeypatch.setattr("acoustic_orchestrator.pipeline.render_pipeline.render_manifest_with_matlab",
                        simulate_matlab_process(fake_render))
    render_static_scenes(config_path)
    replacements = [(root.resolve().as_posix(), "<TMP>"), (REPOSITORY.as_posix(), "<REPO>")]
    scenes = {path.name: _placeholders(json.loads(path.read_text(encoding="utf-8")), replacements)
              for path in sorted(layout["scene_manifest_dir"].glob("*.json"))}
    # Existing goldens pin the acoustic contract, not the two technical fields
    # deliberately changed by isolation. Verify provenance before projecting it.
    from acoustic_orchestrator.pipeline.raven_resources import verify_private_runtime
    from acoustic_orchestrator.pipeline.matlab_runner import build_raven_project_name
    runtimes = {}
    for path in sorted(layout["runtime_manifest_dir"].glob("*.json")):
        runtime = json.loads(path.read_text(encoding="utf-8"))
        preparation = verify_private_runtime(runtime)
        scene = json.loads((layout["scene_manifest_dir"] / f"{runtime['scene_id']}.json").read_text())
        runtime["base_rpf_file"] = preparation.original_rpf.as_posix()
        runtime["project_name"] = build_raven_project_name(scene["project_name"], path.stem)
        runtimes[path.name] = _placeholders(runtime, replacements)
    return {"scene_manifests": scenes, "runtime_manifests": runtimes}


def capture_matlab_command(root: Path, monkeypatch) -> list[str]:
    """Exact command of ``render_manifest_with_matlab`` with ``subprocess.run`` faked."""
    from acoustic_orchestrator.pipeline import matlab_runner
    from acoustic_orchestrator.pipeline.render_attempt import RenderAttempt

    manifest = root / "runtime" / "scene_static_0001__circular_array.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "scene_id": "scene_static_0001", "render": {
            "active_output_type": "circular_array",
            "output_wav_path": (root / "out" / "scene_static_0001__circular_array.wav").as_posix(),
            "output_metadata_path": (root / "out" / "scene_static_0001__circular_array__render.json").as_posix(),
        }}), encoding="utf-8")
    seen: list[list[str]] = []

    def fake_run(command, check, **kwargs):
        seen.append(list(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(matlab_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(matlab_runner, "MATLAB_ENTRYPOINT", Path(__file__))
    from render_fixtures import isolate_test_runtime
    isolate_test_runtime(manifest)
    attempt = RenderAttempt(manifest, root / "attempts")
    matlab_runner.render_manifest_with_matlab(manifest, "matlab", diagnostics_dir=root / "diag",
                                              attempts_dir=root / "attempts", attempt=attempt)
    replacements = [(attempt.attempt_id, "<ATTEMPT_ID>"), (attempt.manifest_sha256, "<MANIFEST_SHA256>"),
                    (root.resolve().as_posix(), "<TMP>"), (REPOSITORY.as_posix(), "<REPO>")]
    return _placeholders(seen[0], replacements)


def load_golden(name: str) -> object:
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def _write_golden(name: str, value: object) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    (GOLDEN_DIR / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:  # pragma: no cover - explicit regeneration only
    import tempfile

    import pytest

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    monkeypatch = pytest.MonkeyPatch()
    try:
        for name in EXAMPLES:
            _write_golden(f"{name}.normalized_config.json", normalized_example_config(name))
            with tempfile.TemporaryDirectory() as directory:
                _write_golden(f"{name}.manifests.json", capture_example_manifests(Path(directory), name, monkeypatch))
            monkeypatch.undo()
        with tempfile.TemporaryDirectory() as directory:
            _write_golden("matlab_command.json", capture_matlab_command(Path(directory), monkeypatch))
    finally:
        monkeypatch.undo()


if __name__ == "__main__":  # pragma: no cover
    main()
