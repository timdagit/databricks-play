"""Idle tracking for the app's shutdown watchdog (spec section 6.4).

Only calls to `touch` count as activity. The app calls it from callbacks a person triggers,
never from the status polling, so a browser tab left open does not keep the app alive.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class IdleTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._last = clock()

    def touch(self) -> None:
        self._last = self._clock()

    def idle_seconds(self) -> float:
        return self._clock() - self._last
