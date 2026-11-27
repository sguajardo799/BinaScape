"""Local CLI presentation; no dependency on the sibling orchestrator."""

# Presentation is best-effort even when an arbitrary console/renderer fails.
# ruff: noqa: BLE001

import sys
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from threading import RLock
from time import monotonic

from rich.console import Console
from rich.live import Live
from rich.text import Text


@dataclass(frozen=True)
class JobEvent:
    kind: str
    total: int
    completed: int = 0
    failed: int = 0
    key: str = ""
    duration: float | None = None
    elapsed: float = 0.0
    status: str = ""


JobCallback = Callable[[JobEvent], None]


def seconds(value: float) -> str:
    value = max(0, int(value))
    return f"{value // 3600:02}:{value % 3600 // 60:02}:{value % 60:02}"


class ClarityProgress:
    def __init__(self, *, enabled: bool = True, stream=None, clock=monotonic) -> None:
        self.enabled = enabled
        self.clock = clock
        self.lock = RLock()
        self.event = JobEvent("begin", 0)
        self.received = clock()
        self.samples = 0
        self.mean: float | None = None
        self.failed = False
        self.interactive = False
        self.console: Console | None = None
        self.live: Live | None = None
        try:
            stream = sys.stderr if stream is None else stream
            self.interactive = enabled and bool(stream.isatty())
            self.console = Console(file=stream, force_terminal=self.interactive, color_system=None)
        except Exception:
            self.failed = True

    def __enter__(self):
        if self.enabled and self.interactive and not self.failed:
            try:
                self.live = Live(self, console=self.console, refresh_per_second=2)
                self.live.start()
            except Exception:
                self.failed = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.live is not None:
            try:
                self.live.stop()
            except Exception:
                self.failed = True
        return False

    def __call__(self, event: JobEvent) -> None:
        if not self.enabled or self.failed:
            return
        try:
            with self.lock:
                self.event, self.received = event, self.clock()
                if event.kind == "begin":
                    self.samples, self.mean = 0, None
                if (event.kind == "resolved" and event.status == "completed" and event.duration is not None
                        and isfinite(event.duration) and event.duration >= 0):
                    self.samples += 1
                    self.mean = event.duration if self.mean is None else 0.3 * event.duration + 0.7 * self.mean
                if not self.interactive and event.kind in {"begin", "finish"} and self.console is not None:
                    self.console.print(self.__rich__(), soft_wrap=True)
        except Exception:
            self.failed = True

    def eta(self) -> float | None:
        pending = self.event.total - self.event.completed - self.event.failed
        if self.samples < 3 or pending <= 0 or self.event.kind == "finish":
            return None
        value = pending * (self.mean or 0.0)
        return max(1.0, value) if isfinite(value) else None

    def __rich__(self) -> Text:
        try:
            with self.lock:
                event = self.event
                count = event.completed + event.failed
                filled = int(20 * count / event.total) if event.total else 0
                elapsed = event.elapsed + (max(0, self.clock() - self.received) if event.kind != "finish" else 0)
                eta = self.eta()
                text = (f"Degradado [{'#' * filled}{'-' * (20 - filled)}] {count}/{event.total} jobs "
                        f"elapsed={seconds(elapsed)} "
                        + (f"ETA ~{seconds(eta)}" if eta is not None else "ETA calculando/no disponible")
                        + f"\ncompletados={event.completed} fallidos={event.failed}")
                if not event.total:
                    text += " sin trabajo pendiente"
                if event.key:
                    text += f"\n{event.key}"
                if event.kind == "finish":
                    text += f" {event.status}"
                return Text(text)
        except Exception:
            self.failed = True
            return Text("")
