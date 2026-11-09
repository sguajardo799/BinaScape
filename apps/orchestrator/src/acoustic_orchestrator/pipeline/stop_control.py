"""Cooperative render shutdown; requesting a stop never performs I/O."""

from contextlib import contextmanager
import os
import signal
import subprocess
import time
from typing import Callable


def render_process_options(platform: str | None = None) -> dict:
    """Keep console SIGINT directed at the orchestrator, not its render child."""
    if (platform or os.name) == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}
    return {"start_new_session": True}


class RenderStopped(RuntimeError):
    """The operator requested a stop, not a functional render failure."""

    def __init__(self, *, manifest_paths=None, summary=None):
        super().__init__("Render detenido por solicitud del operador")
        self.manifest_paths = manifest_paths or []
        self.summary = summary


class StopControl:
    def __init__(self, *, notify: Callable[[str], None] | None = None) -> None:
        self.requested = False
        self._restore: Callable[[], None] | None = None
        self._notify = notify
        self._announced_active: int | None = None

    def request(self) -> None:
        # A signal handler must not acquire locks (including Event's lock), print
        # or publish files. Python executes this assignment on the main thread.
        self.requested = True

    @property
    def observe_while_waiting(self) -> bool:
        """Existing silent programmatic calls keep their direct sequential path."""
        return self._notify is not None or self._restore is not None

    def checkpoint(self) -> None:
        if self.requested:
            self.observe(0)
            raise RenderStopped()

    def observe(self, active: int) -> None:
        """Presentation belongs to the coordinator, never the signal handler."""
        if not self.requested or active == self._announced_active:
            return
        first = self._announced_active is None
        self._announced_active = active
        if self._notify is not None:
            try:
                self._notify(("Parada solicitada; " if first else "Drenando render; ")
                             + f"esperando {active} procesos activos. No se iniciará trabajo nuevo.")
            except Exception:
                self._notify = None

    def wait_delay(self, seconds: float) -> None:
        """An unconsumed retry wait can be stopped without reserving an ordinal."""
        deadline = time.monotonic() + seconds
        while True:
            self.checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))

    def release_signals(self) -> None:
        """End render-only signal ownership before handing off to Clarity."""
        if self._restore is not None:
            restore, self._restore = self._restore, None
            restore()


@contextmanager
def cooperative_sigint(control: StopControl):
    previous = signal.getsignal(signal.SIGINT)

    def request_stop(signum, frame):
        control.request()

    signal.signal(signal.SIGINT, request_stop)
    control._restore = lambda: signal.signal(signal.SIGINT, previous)
    try:
        yield control
    finally:
        control.release_signals()
