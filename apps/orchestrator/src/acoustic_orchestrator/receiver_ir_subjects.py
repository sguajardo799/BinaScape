"""Subject identity of HRTF/HARTF catalog files, shared by validation and sampling."""

from pathlib import Path


def receiver_ir_subject_id(path: Path) -> str | None:
    """Return the filename part before the first underscore (`id22_HRTF.daff` -> `id22`)."""
    prefix, separator, _ = path.name.partition("_")
    return prefix if separator and prefix else None


def subject_catalog(files: list[Path]) -> tuple[dict[str, Path], list[Path], dict[str, list[Path]]]:
    """Map subject IDs to files, reporting files without an ID and IDs used more than once."""
    by_subject: dict[str, list[Path]] = {}
    unidentified: list[Path] = []
    for path in files:
        subject = receiver_ir_subject_id(path)
        if subject is None:
            unidentified.append(path)
        else:
            by_subject.setdefault(subject, []).append(path)
    unique = {subject: paths[0] for subject, paths in by_subject.items() if len(paths) == 1}
    duplicates = {subject: paths for subject, paths in by_subject.items() if len(paths) > 1}
    return unique, unidentified, duplicates
