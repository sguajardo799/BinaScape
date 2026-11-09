"""Bounded retries for publishing an already closed metadata temporary."""

import time
from pathlib import Path


def atomic_replace(temporary: Path, destination: Path) -> None:
    """Retry only selected Windows errors, never preparation or serialization."""
    delays = (0.05, 0.10, 0.20, 0.40)
    for attempt in range(5):
        try:
            temporary.replace(destination)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33}:
                raise
            if attempt == 4:
                exc.add_note(
                    f"Metadata replacement exhausted 5 attempts (0.75 s scheduled wait): "
                    f"{temporary} -> {destination}"
                )
                raise
            time.sleep(delays[attempt])
