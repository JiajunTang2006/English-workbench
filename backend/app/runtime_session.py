from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic


@dataclass(frozen=True)
class BrowserSessionSnapshot:
    connections: int
    ever_connected: bool
    last_disconnected_at: float


class BrowserSessionLifecycle:
    """Track live workbench pages so the desktop launcher can follow their lifecycle."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._connections = 0
        self._ever_connected = False
        self._last_disconnected_at = monotonic()

    def connect(self) -> None:
        with self._lock:
            self._connections += 1
            self._ever_connected = True

    def disconnect(self) -> None:
        with self._lock:
            self._connections = max(0, self._connections - 1)
            if self._connections == 0:
                self._last_disconnected_at = monotonic()

    def snapshot(self) -> BrowserSessionSnapshot:
        with self._lock:
            return BrowserSessionSnapshot(
                connections=self._connections,
                ever_connected=self._ever_connected,
                last_disconnected_at=self._last_disconnected_at,
            )

    def should_stop(self, *, grace_seconds: float, now: float | None = None) -> bool:
        snapshot = self.snapshot()
        current = monotonic() if now is None else now
        return (
            snapshot.ever_connected
            and snapshot.connections == 0
            and current - snapshot.last_disconnected_at >= grace_seconds
        )
