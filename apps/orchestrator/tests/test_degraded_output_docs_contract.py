from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_CONTRACT_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "ARCHITECTURE.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "examples" / "example_config.yml",
    REPO_ROOT / "examples" / "example_config_w_loss.yml",
]


def test_degraded_output_docs_and_instructions_stay_aligned() -> None:
    required_markers = [
        "outputs/degraded",
        "{output_type}/{hearing_profile_id}",
    ]
    forbidden_legacy_markers = [
        "degraded.wav",
        "clarity_result.json",
    ]

    for path in DOC_CONTRACT_FILES:
        content = path.read_text(encoding="utf-8")
        for marker in required_markers:
            assert marker in content, f"{path.name} should mention {marker}"
        for marker in forbidden_legacy_markers:
            assert marker not in content, f"{path.name} should not document legacy fixed name {marker}"


def test_readme_and_agent_guidance_describe_preserved_filenames_and_sidecars() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "outputs/degraded/{output_type}/{hearing_profile_id}/{render_wav_name}.wav" in readme
    assert "{render_wav_stem}.json" in readme
    assert "preserve the rendered WAV basename exactly" in agents
    assert "{render_wav_stem}.json" in agents
