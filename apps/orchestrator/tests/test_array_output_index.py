from copy import deepcopy
import json
from pathlib import Path
import wave

import pytest

from acoustic_orchestrator.config.models import CircularArrayOutputConfig
from acoustic_orchestrator.experiment.receiver_geometry import build_circular_array_descriptor
from acoustic_orchestrator.pipeline.output_index import inspect_variant, should_resume_variant, upsert_variant, load_variant_index


def _outputs(tmp_path: Path, *, metadata: bool = True) -> dict:
    descriptor = build_circular_array_descriptor(
        [2, 1.5, 2], {"yaw": 0}, CircularArrayOutputConfig(enabled=True, num_microphones=8, radius_m=0.05)
    )
    paths = {
        "variant_id": "scene__circular_array", "scene_id": "scene", "output_type": "circular_array",
        "output_subdir": "circular_array", "runtime_manifest_path": str(tmp_path / "runtime.json"),
        "rendered_wav_path": str(tmp_path / "array.wav"), "render_metadata_path": str(tmp_path / "meta.json"),
        "receiver_ir_path": None, "circular_array": descriptor, "sample_rate_hz": 44100,
    }
    runtime = {"receiver": {"hrtfs": [], "circular_array": descriptor}, "render": {"active_output_type": "circular_array"}}
    Path(paths["runtime_manifest_path"]).write_text(json.dumps(runtime), encoding="utf-8")
    _write_wav(Path(paths["rendered_wav_path"]), 8, 44100)
    if metadata:
        payload = {"scene_id": "scene", "output_type": "circular_array", "receiver": {
            "coordinate_frame": "public_xyz_y_up", "circular_array": descriptor,
        }}
        Path(paths["render_metadata_path"]).write_text(json.dumps(payload), encoding="utf-8")
    return paths


def _write_wav(path: Path, channels: int, rate: int) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(bytes(channels * 2 * 32))


@pytest.mark.parametrize("metadata", [True, False])
def test_valid_array_resumes_with_sidecar_or_runtime_provenance(tmp_path: Path, metadata: bool) -> None:
    paths = _outputs(tmp_path, metadata=metadata)
    record = inspect_variant(paths, metadata_required=metadata, attempted=True)
    assert record["status"] == "completed"
    assert record["validation"]["ok"]
    assert record["receiver_ir_path"] is None
    index = tmp_path / "index.jsonl"
    upsert_variant(index, {}, record)
    loaded = load_variant_index(index)
    assert loaded[record["variant_id"]]["receiver_ir_path"] is None
    assert should_resume_variant(loaded, paths, metadata_required=metadata)


@pytest.mark.parametrize("count,rate,issue", [
    (2, 44100, "array_wav_channel_count_mismatch"), (8, 48000, "array_wav_sample_rate_mismatch"),
])
def test_array_rejects_wrong_wav_shape(tmp_path: Path, count: int, rate: int, issue: str) -> None:
    paths = _outputs(tmp_path)
    _write_wav(Path(paths["rendered_wav_path"]), count, rate)
    record = inspect_variant(paths, metadata_required=True, attempted=True)
    assert issue in record["validation"]["issues"]
    assert record["status"] == "partial"
    assert not should_resume_variant({}, paths, metadata_required=True)


@pytest.mark.parametrize("field,value", [("radius_m", 0.1), ("yaw_deg", 90), ("num_microphones", 4)])
def test_array_rejects_stale_geometry(tmp_path: Path, field: str, value: float) -> None:
    paths = _outputs(tmp_path)
    changed = deepcopy(paths)
    changed["circular_array"][field] = value
    record = inspect_variant(changed, metadata_required=True, attempted=True)
    assert "array_metadata_geometry_mismatch" in record["validation"]["issues"]
    assert "array_runtime_geometry_mismatch" in record["validation"]["issues"]
    assert not should_resume_variant({}, changed, metadata_required=True)


def test_array_rejects_stale_microphone_order(tmp_path: Path) -> None:
    paths = _outputs(tmp_path)
    payload = json.loads(Path(paths["render_metadata_path"]).read_text(encoding="utf-8"))
    payload["receiver"]["circular_array"]["microphones"].reverse()
    Path(paths["render_metadata_path"]).write_text(json.dumps(payload), encoding="utf-8")
    assert not inspect_variant(paths, metadata_required=True, attempted=True)["validation"]["ok"]


def test_array_without_metadata_requires_matching_runtime_geometry(tmp_path: Path) -> None:
    paths = _outputs(tmp_path, metadata=False)
    Path(paths["runtime_manifest_path"]).write_text("{}", encoding="utf-8")
    record = inspect_variant(paths, metadata_required=False, attempted=True)
    assert not record["validation"]["ok"]
    assert not should_resume_variant({}, paths, metadata_required=False)
