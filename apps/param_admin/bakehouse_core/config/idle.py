"""Idle tracking for the app's shutdown watchdog (spec section 6.4).

Only calls to `touch` count as activity. The app calls it from callbacks a person triggers,
never from the status polling, so a browser tab left open does not keep the app alive.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


class IdleTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._last = clock()

    def touch(self) -> None:
        self._last = self._clock()

    def idle_seconds(self) -> float:
        return self._clock() - self._last


class IdleWatchdog:
    """Stops the app once nobody has used it for `limit_seconds` (spec section 6.4).

    `check` holds the decision and is called every `interval_seconds` by a background thread.
    It does nothing during `grace_seconds` after the start, so a short limit cannot stop the app
    before the person who started it has opened the page. If `stop` fails (for example the app
    may not stop itself), the error is logged and the next try waits `retry_seconds`.
    """

    def __init__(
        self,
        tracker: IdleTracker,
        stop: Callable[[], object],
        limit_seconds: float,
        grace_seconds: float = 0,
        interval_seconds: float = 15,
        retry_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._tracker = tracker
        self._stop = stop
        self.limit_seconds = limit_seconds
        self._grace = grace_seconds
        self._interval = interval_seconds
        self._retry = retry_seconds
        self._clock = clock
        self._started_at = clock()
        self._last_failure: float | None = None
        self._finished = threading.Event()
        self.stop_requested = False

    def check(self) -> bool:
        """Request the stop if the app is idle past the limit. True if it was requested."""
        now = self._clock()
        if self.stop_requested or now - self._started_at < self._grace:
            return False
        if self._tracker.idle_seconds() <= self.limit_seconds:
            return False
        if self._last_failure is not None and now - self._last_failure < self._retry:
            return False
        try:
            self._stop()
        except Exception as exc:  # noqa: BLE001 - any failure to stop must be logged, not raised
            self._last_failure = now
            log.error(
                "idle stop failed (%s: %s); will try again in %s seconds",
                type(exc).__name__,
                exc,
                self._retry,
                exc_info=True,
            )
            return False
        self.stop_requested = True
        log.warning("idle for %.0f seconds: stop requested", self._tracker.idle_seconds())
        return True

    def start(self) -> threading.Thread:
        """Run `check` in a daemon thread until a stop has been requested."""

        def loop() -> None:
            while not self._finished.wait(self._interval):
                if self.check():
                    return

        thread = threading.Thread(target=loop, name="idle-watchdog", daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        self._finished.set()
