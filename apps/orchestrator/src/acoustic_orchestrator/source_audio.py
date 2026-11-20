from contextlib import contextmanager
import os
from pathlib import Path, PurePath
from typing import Iterator


# Listings reused within one static preflight inspection; None means disabled.
_listing_cache: dict[Path, list[Path]] | None = None


@contextmanager
def source_listing_cache() -> Iterator[None]:
    """Reuse each directory listing until the block exits (one inspection)."""
    global _listing_cache
    previous, _listing_cache = _listing_cache, {}
    try:
        yield
    finally:
        _listing_cache = previous


def enable_source_listing_cache() -> None:
    """Worker-process initializer: the pool, and its cache, last one inspection."""
    global _listing_cache
    _listing_cache = {}


def _is_wav(entry: os.DirEntry) -> bool:
    # PurePath.suffix keeps the exact extension rules of the previous listing
    # (e.g. a file named ".wav" has no suffix).
    return PurePath(entry.name).suffix.lower() == ".wav" and entry.is_file()


# Mirrors pathlib's own case-sensitivity test for the native flavour.
_CASE_SENSITIVE = os.path.normcase("Aa") == "Aa"


def _path_order_key(parts: tuple[str, ...]) -> list[str]:
    # Same ordering as comparing the relative PurePaths: pathlib compares
    # str(path).lower() parts on Windows (not os.path.normcase, which differs
    # for some non-ASCII characters) and case-sensitive parts on POSIX.
    text = os.path.join(*parts)
    return (text if _CASE_SENSITIVE else text.lower()).split(os.sep)


def list_source_wavs(directory: Path) -> list[Path]:
    """List root WAVs and WAVs directly inside immediate identity folders."""
    if _listing_cache is None:
        return _scan_source_wavs(directory)
    if directory not in _listing_cache:
        _listing_cache[directory] = _scan_source_wavs(directory)
    return list(_listing_cache[directory])


def _scan_source_wavs(directory: Path) -> list[Path]:
    # DirEntry type checks reuse data from the directory read, avoiding one
    # stat per file on large catalogs.
    relative: list[tuple[str, ...]] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if _is_wav(entry):
                relative.append((entry.name,))
            elif entry.is_dir():
                with os.scandir(entry.path) as children:
                    relative.extend((entry.name, child.name) for child in children if _is_wav(child))
    relative.sort(key=_path_order_key)
    return [directory.joinpath(*parts) for parts in relative]
