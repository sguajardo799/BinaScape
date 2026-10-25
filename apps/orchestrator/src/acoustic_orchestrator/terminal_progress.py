"""CLI-owned presentation. A single thread owns interactive console writes."""

# Presentation is best-effort even when an arbitrary console/renderer fails.
# ruff: noqa: BLE001

import sys
from dataclasses import replace
from pathlib import Path
from queue import Empty, Queue
from threading import Event, RLock, Thread
from time import monotonic

from rich.console import Console
from rich.live import Live
from rich.text import Text

from acoustic_orchestrator.pipeline.matlab_diagnostics import MatlabActivityReader
from acoustic_orchestrator.pipeline.progress import ProgressEvent, StageEstimate


def seconds(value: float) -> str:
    value = max(0, int(value))
    return f"{value // 3600:02}:{value % 3600 // 60:02}:{value % 60:02}"


class TerminalProgress:
    def __init__(self, *, enabled: bool = True, stream=None, clock=monotonic) -> None:
        self.stream = sys.stderr if stream is None else stream
        self.enabled = enabled
        self.clock = clock
        self.failed = False
        self.interactive = False
        self.console: Console | None = None
        try:
            self.interactive = bool(self.stream.isatty()) if enabled else False
            self.console = Console(file=self.stream, force_terminal=self.interactive, color_system=None)
        except Exception:
            self.failed = True
        self.queue: Queue[tuple[ProgressEvent | None, Event]] = Queue()
        self.lock = RLock()
        self.thread: Thread | None = None
        self.live: Live | None = None
        self.current: ProgressEvent | None = None
        self.received = clock()
        self.estimate = StageEstimate()
        self.suspended = False
        self.closed = False
        self.activities: dict[str, tuple[str, MatlabActivityReader, float]] = {}

    def __enter__(self):
        if self.enabled and self.interactive and not self.failed:
            try:
                self.thread = Thread(target=self._run, name="pipeline-progress", daemon=True)
                self.thread.start()
            except Exception:
                self.failed = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close(interrupted=exc_type is not None)
        return False

    def __call__(self, event: ProgressEvent) -> None:
        if not self.enabled or self.failed:
            return
        if self.interactive:
            ack = Event()
            self.queue.put((event, ack))
            if event.kind in {"suspend", "resume"}:
                ack.wait(2)
        else:
            with self.lock:
                self._safe_consume(event)

    def _safe_consume(self, event: ProgressEvent) -> None:
        try:
            self._consume(event)
        except Exception:
            self.failed = True

    def _consume(self, event: ProgressEvent) -> None:
        if event.kind == "interrupt":
            if self.current is None or self.current.kind == "finish":
                return
            event = replace(self.current, kind="finish", outcome="interrumpida",
                            elapsed=self.current.elapsed + max(0.0, self.clock() - self.received))
        if event.kind in {"suspend", "resume"}:
            self.suspended = event.kind == "suspend"
            self._stop_live()
            return
        if event.kind == "attempt_begin" and self.interactive:
            self.activities[event.attempt_id] = (
                event.key, MatlabActivityReader(Path(event.events_path), event.attempt_id), self.clock(),
            )
        elif event.kind == "attempt_end":
            self.activities.pop(event.attempt_id, None)
        self.current = event
        self.received = self.clock()
        self.estimate.observe(event)
        if not self.interactive and event.kind in {"begin", "finish"} and self.console is not None:
            self.console.print(self._text(event), soft_wrap=True)

    def _text(self, event: ProgressEvent) -> Text:
        elapsed = event.elapsed
        if event.kind != "finish":
            elapsed += max(0.0, self.clock() - self.received)
        event = replace(event, elapsed=elapsed)
        result = f"{event.stage} "
        if event.total is not None:
            fraction = min(1.0, event.completed / event.total) if event.total else 0
            filled = int(fraction * 20)
            result += f"[{'#' * filled}{'-' * (20 - filled)}] {event.completed}/{event.total} {event.unit} "
        result += f"elapsed={seconds(elapsed)} "
        eta = self.estimate.remaining(event)
        result += f"ETA ~{seconds(eta)}" if eta is not None else "ETA calculando/no disponible"
        if event.total == 0:
            result += " sin trabajo pendiente"
        if event.outcome and event.kind == "finish":
            result += f" {event.outcome}"
        if event.counters:
            result += "\n" + " ".join(f"{key}={value}" for key, value in event.counters.items())
        if event.key and event.kind == "start":
            result += f"\nActiva: {event.key}"
        for key, reader, started in self.activities.values():
            result += f"\nActiva: {key} / {reader.poll()} / elapsed={seconds(self.clock() - started)}"
        if event.kind == "attempt_end":
            result += f"\n{event.key}: validando resultado"
        return Text(result)  # IDs and paths are literal, never Rich markup.

    def _refresh(self) -> None:
        if self.failed or self.suspended or self.current is None:
            return
        text = self._text(self.current)
        if self.live is None:
            self.live = Live(text, console=self.console, auto_refresh=False)
            self.live.start()
        self.live.update(text, refresh=True)

    def _stop_live(self) -> None:
        live, self.live = self.live, None
        if live is not None:
            try:
                live.stop()
            except Exception:
                self.failed = True

    def _run(self) -> None:
        try:
            while True:
                try:
                    event, ack = self.queue.get(timeout=0.5)
                except Empty:
                    event, ack = None, None
                if ack is not None:
                    try:
                        if event is None:
                            break
                        self._safe_consume(event)
                    finally:
                        ack.set()
                try:
                    self._refresh()
                except Exception:
                    self.failed = True
        finally:
            self._stop_live()
            self.activities.clear()

    def close(self, *, interrupted: bool = False) -> None:
        if not self.enabled or self.closed:
            return
        self.closed = True
        try:
            if self.thread is not None:
                # FIFO ensures queued observations are consumed before shutdown.
                if interrupted:
                    self.queue.put((ProgressEvent("interrupt", ""), Event()))
                self.queue.put((None, Event()))
                self.thread.join(timeout=2)
            elif interrupted and self.current is not None:
                self._safe_consume(ProgressEvent("interrupt", ""))
        except Exception:
            self.failed = True
