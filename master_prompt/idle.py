"""Idle clock for auto prompt. Stores the time of the last key, never the key itself."""
from __future__ import annotations


class IdleTimer:
    """Fires once after `idle_seconds` with no further activity."""

    enabled: bool

    def __init__(self, idle_seconds: float) -> None:
        self.idle_seconds = float(idle_seconds) if idle_seconds >= 1 else 1.0
        self.enabled = False
        self._last: float | None = None
        self._fired = False

    def note_activity(self, now: float) -> None:
        """Remember `now`. Takes no key code and no characters."""
        self._last = now
        self._fired = False

    def poll(self, now: float) -> bool:
        """Return True once when the idle gap is reached, then stay False until the next key."""
        if not self.enabled or self._last is None or self._fired:
            return False
        if now - self._last >= self.idle_seconds:
            self._fired = True
            return True
        return False
