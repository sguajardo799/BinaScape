"""Identidad nominal de sujetos en catálogos de respuestas cefálicas."""

from pathlib import Path


def list_receiver_irs(directory: Path, pattern: str) -> list[Path]:
    return sorted(directory.glob(pattern))


def receiver_subject_id(path: Path) -> str:
    stem = path.stem
    for suffix in ("_HARTF_front", "_HARTF_rear", "_HRTF"):
        if stem.endswith(suffix):
            return stem.removesuffix(suffix)
    return stem


def index_receiver_subjects(files: list[Path]) -> dict[str, Path]:
    subjects: dict[str, Path] = {}
    for path in sorted(files):
        subject = receiver_subject_id(path)
        if not subject:
            raise ValueError(f"Identidad de receptor vacía: {path}")
        if subject in subjects:
            raise ValueError(
                f"Identidad de receptor ambigua {subject!r}: {subjects[subject]} y {path}"
            )
        subjects[subject] = path
    return subjects


def paired_receiver_catalogs(
    catalogs: dict[str, tuple[Path, str]], required_count: int,
) -> tuple[dict[str, dict[str, Path]], list[str]]:
    indexes = {}
    for name, (directory, pattern) in catalogs.items():
        try:
            indexes[name] = index_receiver_subjects(list_receiver_irs(directory, pattern))
        except ValueError as exc:
            raise ValueError(f"receiver_outputs.{name} ({directory}): {exc}") from exc
    common = sorted(set.intersection(*(set(index) for index in indexes.values())))
    if len(common) < required_count:
        description = ", ".join(f"{name} ({directory})" for name, (directory, _) in catalogs.items())
        raise ValueError(
            f"Emparejamiento de receptor: {len(common)} sujetos comunes disponibles, "
            f"se requieren {required_count}; catálogos: {description}"
        )
    return indexes, common
