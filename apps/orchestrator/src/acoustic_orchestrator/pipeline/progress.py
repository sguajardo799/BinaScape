"""Optional, non-authoritative observations of pipeline work. No console I/O."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
from threading import RLock
from time import monotonic


@dataclass(frozen=True)
class ProgressEvent:
    kind: str
    stage: str
    total: int | None = None
    unit: str = ""
    key: str = ""
    outcome: str = ""
    completed: int = 0
    elapsed: float = 0.0
    duration: float | None = None
    parallel: bool = False
    counters: dict[str, int] = field(default_factory=dict)
    attempt_id: str = ""
    events_path: str = ""


ProgressCallback = Callable[[ProgressEvent], None]


class StageEstimate:
    """Estimates new accepted work only, never the visual completion counter."""

    def __init__(self) -> None:
        self.samples = 0
        self.mean: float | None = None
        self.first_dispatch: float | None = None

    def observe(self, event: ProgressEvent) -> None:
        if event.kind == "begin":
            self.__init__()
        elif event.kind == "start" and self.first_dispatch is None:
            self.first_dispatch = event.elapsed
        elif (event.kind == "resolved" and event.outcome in {"aceptadas", "nuevas"}
              and event.duration is not None and isfinite(event.duration) and event.duration >= 0):
            self.samples += 1
            self.mean = event.duration if self.mean is None else 0.3 * event.duration + 0.7 * self.mean

    def remaining(self, event: ProgressEvent) -> float | None:
        pending = (event.total or 0) - event.completed
        if self.samples < 3 or pending <= 0 or event.kind == "finish":
            return None
        if event.parallel:
            if self.first_dispatch is None:
                return None
            estimate = pending * (event.elapsed - self.first_dispatch) / self.samples
        else:
            estimate = pending * (self.mean or 0.0)
        return max(1.0, estimate) if isfinite(estimate) and estimate >= 0 else None


class ProgressReporter:
    """One reporter per invocation; exceptions from a callback disable it."""

    def __init__(self, callback: ProgressCallback | None = None, *, clock=monotonic) -> None:
        self.callback = callback
        self.clock = clock
        self.lock = RLock()
        self.stage = ""
        self.total: int | None = None
        self.unit = ""
        self.parallel = False
        self.started = clock()
        self.starts: dict[str, float] = {}
        self.resolved: set[str] = set()
        self.counters: Counter[str] = Counter()

    @property
    def enabled(self) -> bool:
        return self.callback is not None

    def emit(self, kind: str, **values) -> None:
        with self.lock:
            if self.callback is None:
                return
            event = ProgressEvent(
                kind=kind, stage=self.stage, total=self.total, unit=self.unit,
                completed=len(self.resolved), elapsed=max(0.0, self.clock() - self.started),
                parallel=self.parallel, counters=dict(self.counters), **values,
            )
            try:
                self.callback(event)
            except Exception:  # noqa: BLE001 - arbitrary observers cannot affect pipeline decisions.
                # Presentation must not replace a functional error, even on close.
                self.callback = None

    def begin(self, stage: str, total: int | None = None, unit: str = "", *, parallel: bool = False) -> None:
        with self.lock:
            self.stage, self.total, self.unit, self.parallel = stage, total, unit, parallel
            self.started = self.clock()
            self.starts.clear()
            self.resolved.clear()
            self.counters.clear()
            self.emit("begin")

    def start(self, key: str) -> None:
        with self.lock:
            self.starts.setdefault(key, self.clock())
            self.emit("start", key=key)

    def resolve(self, key: str, outcome: str) -> None:
        with self.lock:
            if key in self.resolved:
                return
            started = self.starts.pop(key, None)
            self.resolved.add(key)
            self.counters[outcome] += 1
            duration = None if started is None else max(0.0, self.clock() - started)
            self.emit("resolved", key=key, outcome=outcome, duration=duration)

    def count(self, name: str) -> None:
        with self.lock:
            self.counters[name] += 1
            self.emit("counter")

    def finish(self, outcome: str = "") -> None:
        if not outcome:
            outcome = "con fallos" if self.counters["fallidas"] or self.counters["conflictos"] else "completo"
        self.emit("finish", outcome=outcome)
