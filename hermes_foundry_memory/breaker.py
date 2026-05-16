"""Circuit breaker with cooldown and half-open trial state."""
from __future__ import annotations

import threading
import time
from typing import Callable


class CircuitBreaker:
    """Simple circuit breaker.

    States:
      - CLOSED: normal; failures accumulate; threshold consecutive failures opens.
      - OPEN: is_open() returns True until cooldown elapses.
      - HALF_OPEN: after cooldown, is_open() returns False once to allow a trial.
        A subsequent record_success() closes it (and resets counters); a
        record_failure() re-opens with a fresh cooldown.

    Any record_success() in CLOSED state resets the failure counter.
    """

    _CLOSED = "closed"
    _OPEN = "open"
    _HALF_OPEN = "half_open"

    def __init__(
        self,
        threshold: int = 5,
        cooldown_secs: float = 120,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        if cooldown_secs < 0:
            raise ValueError("cooldown_secs must be non-negative")
        self._threshold = threshold
        self._cooldown = float(cooldown_secs)
        self._clock = clock
        self._lock = threading.RLock()
        self._state = self._CLOSED
        self._failures = 0
        self._opened_at: float | None = None

    def record_failure(self) -> None:
        with self._lock:
            if self._state == self._HALF_OPEN:
                # Trial failed: re-open with fresh cooldown.
                self._state = self._OPEN
                self._opened_at = self._clock()
                return
            self._failures += 1
            if self._state == self._CLOSED and self._failures >= self._threshold:
                self._state = self._OPEN
                self._opened_at = self._clock()

    def record_success(self) -> None:
        with self._lock:
            # Any success closes and resets.
            self._state = self._CLOSED
            self._failures = 0
            self._opened_at = None

    def is_open(self) -> bool:
        with self._lock:
            if self._state == self._OPEN:
                assert self._opened_at is not None
                if self._clock() - self._opened_at >= self._cooldown:
                    # Transition to half-open; allow one trial.
                    self._state = self._HALF_OPEN
                    return False
                return True
            # CLOSED or HALF_OPEN
            return False
