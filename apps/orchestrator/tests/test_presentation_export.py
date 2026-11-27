import json
import math
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from typer.testing import CliRunner

from acoustic_orchestrator.cli import app
from acoustic_orchestrator.pipeline.presentation_export import export_presentation, load_playback_calibration

VARIANT = "scene_static_0001__binaural_hrtf"
BASENAME = f"{VARIANT}.wav"


def _wav(path: Path, rms: tuple[float, float], *, n: int = 4800) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    noise = rng.standard_normal((n, 2))
    noise /= np.sqrt(np.mean(noise**2, axis=0))
    sf.write(path, noise * np.array(rms), 48000, subtype="FLOAT")
    return path


def _run(
    tmp_path: Path, *, k: float = 95.0, degraded: dict[str, str] | None = None,
    rms: tuple[float, float] = (0.01, 0.01), degraded_rms: tuple[float, float] = (0.005, 0.005),
) -> Path:
    """A run with one render, its calibration record and degraded outputs (processor -> status)."""
    run = tmp_path / "run"
    indexes = run / "metadata" / "indexes"
    indexes.mkdir(parents=True)
    render_wav = _wav(run / "output_audio" / "render" / "binaural_hrtf" / BASENAME, rms)
    (indexes / "render_index.jsonl").write_text(json.dumps({
        "variant_id": VARIANT, "scene_id": "scene_static_0001", "output_type": "binaural_hrtf",
        "status": "completed", "rendered_wav_path": render_wav.as_posix(),
        "runtime_manifest_path": "x", "render_metadata_path": "y", "receiver_ir_path": "r",
    }) + "\n", encoding="utf-8")
    (indexes / "level_calibration.jsonl").write_text(json.dumps({
        "variant_id": VARIANT, "input_wav_path": render_wav.as_posix(), "mode": "presentation_level",
        "effective_scale_db_spl": k, "status": "ok",
    }) + "\n", encoding="utf-8")
    records = []
    for processor, status in (degraded or {"msbg": "completed", "whis": "completed"}).items():
        out_dir = run / "output_audio" / "degraded" / processor / "binaural_hrtf" / "mild_loss"
        wav = out_dir / BASENAME
        sidecar = out_dir / f"{VARIANT}.json"
        if status == "completed":
            _wav(wav, degraded_rms)
            sidecar.write_text(json.dumps({"status": "completed", "calibration": {"effective_scale_db_spl": k}}),
                               encoding="utf-8")
        records.append({
            "job_id": f"clarity__{VARIANT}__mild_loss__{processor}", "variant_id": VARIANT,
            "scene_id": "scene_static_0001", "output_type": "binaural_hrtf", "hearing_profile_id": "mild_loss",
            "processor": processor, "status": status, "manifest_path": "m", "input_wav_path": render_wav.as_posix(),
            "hearing_profiles_path": "h", "output_dir": out_dir.as_posix(), "expected_output_wav_path": wav.as_posix(),
            "expected_output_metadata_path": sidecar.as_posix(),
            "validation": {"ok": status == "completed", "issues": []},
        })
    (indexes / "clarity_index.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return run


def _playback(tmp_path: Path, **overrides) -> Path:
    data = {"playback_id": "lab_hd650", "k_play_db": {"left": 100.0, "right": 100.0}, "max_spl_db": 90.0}
    data.update(overrides)
    path = tmp_path / "playback.yaml"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _export_dir(run: Path) -> Path:
    return run / "output_audio" / "presentation" / "lab_hd650"


def _gain_db(source: Path, exported: Path) -> np.ndarray:
    x, _ = sf.read(source)
    y, _ = sf.read(exported)
    return 20 * np.log10(np.sqrt(np.mean(y**2, axis=0)) / np.sqrt(np.mean(x**2, axis=0)))


def test_incomplete_playback_calibration_fails_naming_the_field_and_writes_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path)
    with pytest.raises(ValueError, match=r"k_play_db\.right"):
        export_presentation(run, _playback(tmp_path, k_play_db={"left": 100.0}))
    assert not (run / "output_audio" / "presentation").exists()


@pytest.mark.parametrize(
    "overrides, field",
    [({"playback_id": "../x"}, "playback_id"), ({"max_spl_db": float("nan")}, "max_spl_db"),
     ({"k_play_db": {"left": "a", "right": 1}}, "k_play_db.left")],
)
def test_invalid_playback_fields_are_rejected(tmp_path: Path, overrides: dict, field: str) -> None:
    path = tmp_path / "p.yaml"
    data = {"playback_id": "lab", "k_play_db": {"left": 100.0, "right": 100.0}, "max_spl_db": 90.0} | overrides
    path.write_text(json.dumps(data).replace("NaN", ".nan"), encoding="utf-8")
    with pytest.raises(ValueError, match=field.replace(".", r"\.")):
        load_playback_calibration(path)


def test_three_versions_of_a_render_get_the_same_gain(tmp_path: Path) -> None:
    run = _run(tmp_path, k=95.0)

    summary = export_presentation(run, _playback(tmp_path))

    root = _export_dir(run)
    exported = {
        "unprocessed": root / "unprocessed" / "binaural_hrtf" / BASENAME,
        "msbg": root / "msbg" / "binaural_hrtf" / "mild_loss" / BASENAME,
        "whis": root / "whis" / "binaural_hrtf" / "mild_loss" / BASENAME,
    }
    sources = {
        "unprocessed": run / "output_audio" / "render" / "binaural_hrtf" / BASENAME,
        "msbg": run / "output_audio" / "degraded" / "msbg" / "binaural_hrtf" / "mild_loss" / BASENAME,
        "whis": run / "output_audio" / "degraded" / "whis" / "binaural_hrtf" / "mild_loss" / BASENAME,
    }
    for version, path in exported.items():
        assert np.allclose(_gain_db(sources[version], path), -5.0, atol=1e-4)
        assert sf.info(path).subtype == "FLOAT"
        sidecar = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        assert sidecar["gain_db"] == {"left": -5.0, "right": -5.0}
        assert sidecar["effective_scale_db_spl"] == 95.0 and sidecar["version"] == version
        assert sidecar["variant_id"] == VARIANT and sidecar["k_play_db"] == {"left": 100.0, "right": 100.0}
    unprocessed = json.loads(exported["unprocessed"].with_suffix(".json").read_text(encoding="utf-8"))
    assert unprocessed["estimated_spl_db"]["left"] == pytest.approx(95 + 20 * math.log10(0.01), abs=1e-6)
    assert summary["exported_renders"] == 1 and summary["exported_files"] == 3
    assert (root / "export_summary.json").is_file()


def test_channels_with_different_sensitivity_get_different_gains(tmp_path: Path) -> None:
    run = _run(tmp_path)

    export_presentation(run, _playback(tmp_path, k_play_db={"left": 100.0, "right": 98.0}))

    for version in ("unprocessed", "msbg/binaural_hrtf/mild_loss", "whis/binaural_hrtf/mild_loss"):
        path = _export_dir(run) / version.replace("unprocessed", "unprocessed/binaural_hrtf") / BASENAME
        record = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        assert record["gain_db"]["right"] - record["gain_db"]["left"] == pytest.approx(2.0)


def test_render_too_loud_is_skipped_entirely(tmp_path: Path) -> None:
    run = _run(tmp_path, k=112.0, rms=(0.1, 0.1))  # unprocessed at 92 dB SPL

    summary = export_presentation(run, _playback(tmp_path, max_spl_db=85.0, k_play_db={"left": 120, "right": 120}))

    assert summary["skipped_renders"] == {VARIANT: "exceeds_max_spl"}
    assert not list(_export_dir(run).rglob("*.wav"))


def test_render_whose_whis_version_would_clip_is_skipped_entirely(tmp_path: Path) -> None:
    run = _run(tmp_path, k=95.0, rms=(0.01, 0.01), degraded_rms=(0.01, 0.01))
    whis = run / "output_audio" / "degraded" / "whis" / "binaural_hrtf" / "mild_loss" / BASENAME
    audio, fs = sf.read(whis)
    audio[100, 0] = 0.9
    sf.write(whis, audio, fs, subtype="FLOAT")

    summary = export_presentation(run, _playback(tmp_path, k_play_db={"left": 90.0, "right": 90.0}))  # +5 dB

    assert summary["skipped_renders"] == {VARIANT: "would_clip"}
    assert not list(_export_dir(run).rglob("*.wav"))


def test_failed_whis_exports_unprocessed_and_msbg_and_reports_the_absence(tmp_path: Path) -> None:
    run = _run(tmp_path, degraded={"msbg": "completed", "whis": "failed"})

    summary = export_presentation(run, _playback(tmp_path))

    exported = sorted(path.relative_to(_export_dir(run)).as_posix() for path in _export_dir(run).rglob("*.wav"))
    assert exported == [
        f"msbg/binaural_hrtf/mild_loss/{BASENAME}",
        f"unprocessed/binaural_hrtf/{BASENAME}",
    ]
    assert summary["missing_degraded"] == {
        VARIANT: [{"processor": "whis", "hearing_profile_id": "mild_loss", "reason": "status_failed"}],
    }


def test_render_without_valid_degraded_outputs_still_exports_unprocessed(tmp_path: Path) -> None:
    run = _run(tmp_path, degraded={"msbg": "failed"})

    summary = export_presentation(run, _playback(tmp_path))

    assert summary["exported_files"] == 1
    assert (_export_dir(run) / "unprocessed" / "binaural_hrtf" / BASENAME).is_file()


def test_render_without_calibration_record_is_not_exported(tmp_path: Path) -> None:
    run = _run(tmp_path)
    (run / "metadata" / "indexes" / "level_calibration.jsonl").write_text("", encoding="utf-8")

    summary = export_presentation(run, _playback(tmp_path))

    assert summary["skipped_renders"] == {VARIANT: "no_calibration"}
    assert summary["exported_files"] == 0


def test_cli_export_presentation(tmp_path: Path) -> None:
    run = _run(tmp_path)
    runner = CliRunner()

    ok = runner.invoke(app, ["export-presentation", str(run), "--playback", str(_playback(tmp_path))])
    bad = runner.invoke(app, ["export-presentation", str(run), "--playback", str(_playback(tmp_path, max_spl_db=None))])

    assert ok.exit_code == 0 and "renders=1" in ok.output and "archivos=3" in ok.output
    assert bad.exit_code == 1


def test_documented_playback_example_exports_a_run(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[1] / "examples" / "playback_calibration.yaml"
    playback = load_playback_calibration(example)
    run = _run(tmp_path, k=95.0)

    result = CliRunner().invoke(app, ["export-presentation", str(run), "--playback", str(example)])

    assert result.exit_code == 0, result.output
    record = json.loads(
        (run / "output_audio" / "presentation" / playback["playback_id"] / "unprocessed" / "binaural_hrtf"
         / f"{VARIANT}.json").read_text(encoding="utf-8")
    )
    assert record["gain_db"] == {"left": 95.0 - 100.0, "right": 95.0 - 98.0}
