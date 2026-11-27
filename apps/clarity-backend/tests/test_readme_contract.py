"""The job example documented in README.md matches the job contract the tests exercise."""

import json
from pathlib import Path

from test_cli import _build_job

README = Path(__file__).parents[1] / "README.md"


def _readme_job() -> dict:
    section = README.read_text(encoding="utf-8").split("## Manifest Contract", 1)[1]
    return json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])


def test_readme_job_example_matches_test_job(tmp_path: Path) -> None:
    documented = _readme_job()
    job = _build_job(tmp_path, tmp_path / "in.wav", tmp_path / "render.json", tmp_path / "p.yaml")

    assert set(documented) == set(job)
    for key in ("processor", "calibration", "outer_middle_ear", "processor_config", "processing_signature",
                "schema_version", "variant_id", "scene_id", "output_type", "hearing_profile_id"):
        assert documented[key] == job[key], key
    assert documented["job_id"] == job["job_id"]
    assert documented["output_dir"].endswith("output_audio/degraded/msbg/binaural_hrtf/mild_loss")
