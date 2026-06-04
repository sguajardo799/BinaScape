from pathlib import Path

from acoustic_orchestrator.config.models import NamingConfig, OutputsConfig, ReceiverOutputConfig
from acoustic_orchestrator.pipeline.output_paths import (
    build_clarity_job_id,
    resolve_artifact_layout,
    resolve_clarity_job_paths,
    resolve_output_paths,
)


def test_resolve_output_paths_applies_templates_and_subdirectories(tmp_path: Path) -> None:
    outputs = OutputsConfig(
        artifact_root=tmp_path / "artifacts",
        run_name="run_a",
        naming=NamingConfig(
            scene_id_prefix="scene_static",
            wav_pattern="{scene_id}__{output_type}.wav",
            metadata_pattern="{scene_id}__{output_type}__render.json",
        ),
    )
    receiver_output = ReceiverOutputConfig(
        enabled=True,
        ir_catalog_path=tmp_path / "catalog",
        file_pattern="*.daff",
        output_subdir="bte_rear_hartf",
        num_channels=2,
        required=False,
    )

    layout = resolve_artifact_layout(outputs, "sim_test")
    resolved = resolve_output_paths("scene_static_0001", "bte_rear_hartf", outputs, receiver_output, "sim_test")

    assert resolved["variant_id"] == "scene_static_0001__bte_rear_hartf"
    assert layout["scene_manifest_dir"] == tmp_path / "artifacts" / "run_a" / "manifests" / "scene"
    assert layout["runtime_manifest_dir"] == tmp_path / "artifacts" / "run_a" / "manifests" / "runtime" / "render"
    assert layout["prepared_audio_dir"] == tmp_path / "artifacts" / "run_a" / "audio" / "prepared"
    assert layout["render_index_path"] == tmp_path / "artifacts" / "run_a" / "indexes" / "render_index.jsonl"
    assert Path(resolved["wav_path"]).parts[-2:] == ("bte_rear_hartf", "scene_static_0001__bte_rear_hartf.wav")
    assert Path(resolved["metadata_path"]).parts[-2:] == (
        "bte_rear_hartf",
        "scene_static_0001__bte_rear_hartf__render.json",
    )
    assert resolved["output_subdir"] == "bte_rear_hartf"


def test_resolve_output_paths_keeps_variant_id_stable_when_public_names_change(tmp_path: Path) -> None:
    outputs = OutputsConfig(
        artifact_root=tmp_path / "artifacts",
        run_name=None,
        naming=NamingConfig(
            scene_id_prefix="scene_static",
            wav_pattern="wav-{output_type}-{scene_id}.wav",
            metadata_pattern="meta-{output_type}-{scene_id}.json",
        ),
    )
    receiver_output = ReceiverOutputConfig(
        enabled=True,
        ir_catalog_path=tmp_path / "catalog",
        file_pattern="*.daff",
        output_subdir="binaural_hrtf",
        num_channels=2,
        required=True,
    )

    resolved = resolve_output_paths("scene_static_0001", "binaural_hrtf", outputs, receiver_output, "sim_test")

    assert resolved["variant_id"] == "scene_static_0001__binaural_hrtf"
    assert Path(resolved["wav_path"]).name == "wav-binaural_hrtf-scene_static_0001.wav"
    assert Path(resolved["metadata_path"]).name == "meta-binaural_hrtf-scene_static_0001.json"
    assert Path(resolved["wav_path"]).parts[-6:-2] == ("artifacts", "sim_test", "outputs", "render")


def test_resolve_artifact_layout_includes_clarity_manifest_and_index_paths(tmp_path: Path) -> None:
    outputs = OutputsConfig(
        artifact_root=tmp_path / "artifacts",
        run_name="run_a",
        naming=NamingConfig(
            scene_id_prefix="scene_static",
            wav_pattern="{scene_id}__{output_type}.wav",
            metadata_pattern="{scene_id}__{output_type}__render.json",
        ),
    )

    layout = resolve_artifact_layout(outputs, "sim_test")

    assert layout["clarity_manifest_dir"] == tmp_path / "artifacts" / "run_a" / "manifests" / "runtime" / "clarity"
    assert layout["clarity_jobs_path"] == layout["clarity_manifest_dir"] / "clarity_jobs.jsonl"
    assert layout["clarity_index_path"] == tmp_path / "artifacts" / "run_a" / "indexes" / "clarity_index.jsonl"
    assert layout["degraded_output_root"] == tmp_path / "artifacts" / "run_a" / "outputs" / "degraded"


def test_resolve_clarity_job_paths_is_deterministic_per_variant(tmp_path: Path) -> None:
    paths = resolve_clarity_job_paths(
        output_root=tmp_path / "outputs" / "degraded",
        output_type="binaural_hrtf",
        variant_id="scene_static_0001__binaural_hrtf",
        hearing_profile_id="mild_loss",
        input_wav_path=tmp_path / "render" / "binaural_hrtf" / "scene_static_0001__binaural_hrtf.wav",
    )

    assert paths["job_id"] == build_clarity_job_id("scene_static_0001__binaural_hrtf", "mild_loss")
    assert Path(paths["output_dir"]).parts[-2:] == (
        "binaural_hrtf",
        "mild_loss",
    )
    assert Path(paths["expected_output_wav_path"]).name == "scene_static_0001__binaural_hrtf.wav"
    assert Path(paths["expected_output_metadata_path"]).name == "scene_static_0001__binaural_hrtf.json"


def test_resolve_clarity_job_paths_keeps_multiple_sources_distinct_in_shared_profile_dir(tmp_path: Path) -> None:
    first = resolve_clarity_job_paths(
        output_root=tmp_path / "outputs" / "degraded",
        output_type="binaural_hrtf",
        variant_id="scene_static_0001__binaural_hrtf",
        hearing_profile_id="mild_loss",
        input_wav_path=tmp_path / "render" / "binaural_hrtf" / "scene_static_0001__binaural_hrtf.wav",
    )
    second = resolve_clarity_job_paths(
        output_root=tmp_path / "outputs" / "degraded",
        output_type="binaural_hrtf",
        variant_id="scene_static_0002__binaural_hrtf",
        hearing_profile_id="mild_loss",
        input_wav_path=tmp_path / "render" / "binaural_hrtf" / "scene_static_0002__binaural_hrtf.wav",
    )

    assert first["output_dir"] == second["output_dir"]
    assert first["expected_output_wav_path"] != second["expected_output_wav_path"]
    assert first["expected_output_metadata_path"] != second["expected_output_metadata_path"]
