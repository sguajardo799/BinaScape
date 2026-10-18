from pathlib import Path


def list_source_wavs(directory: Path) -> list[Path]:
    """List root WAVs and WAVs directly inside immediate identity folders."""
    files: list[Path] = []
    for entry in directory.iterdir():
        if entry.is_file() and entry.suffix.lower() == ".wav":
            files.append(entry)
        elif entry.is_dir():
            files.extend(
                child for child in entry.iterdir()
                if child.is_file() and child.suffix.lower() == ".wav"
            )
    return sorted(files, key=lambda path: path.relative_to(directory))
