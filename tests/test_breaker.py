import pytest

from hermes_foundry_memory.breaker import CircuitBreaker


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_initial_closed():
    cb = CircuitBreaker(threshold=5, cooldown_secs=120, clock=FakeClock())
    assert cb.is_open() is False


def test_opens_after_threshold_failures():
    cb = CircuitBreaker(threshold=5, cooldown_secs=120, clock=FakeClock())
    for _ in range(4):
        cb.record_failure()
    assert cb.is_open() is False
    cb.record_failure()
    assert cb.is_open() is True


def test_remains_open_within_cooldown():
    clock = FakeClock()
    cb = CircuitBreaker(threshold=5, cooldown_secs=120, clock=clock)
    for _ in range(5):
        cb.record_failure()
    assert cb.is_open() is True
    clock.advance(60)
    assert cb.is_open() is True
    clock.advance(59.9)
    assert cb.is_open() is True


def test_half_open_after_cooldown():
    clock = FakeClock()
    cb = CircuitBreaker(threshold=5, cooldown_secs=120, clock=clock)
    for _ in range(5):
        cb.record_failure()
    clock.advance(121)
    # half-open: is_open() returns False to allow trial
    assert cb.is_open() is False


def test_half_open_success_resets():
    clock = FakeClock()
    cb = CircuitBreaker(threshold=5, cooldown_secs=120, clock=clock)
    for _ in range(5):
        cb.record_failure()
    clock.advance(121)
    assert cb.is_open() is False
    cb.record_success()
    # Now closed; failures count reset
    for _ in range(4):
        cb.record_failure()
    assert cb.is_open() is False


def test_half_open_failure_reopens():
    clock = FakeClock()
    cb = CircuitBreaker(threshold=5, cooldown_secs=120, clock=clock)
    for _ in range(5):
        cb.record_failure()
    clock.advance(121)
    assert cb.is_open() is False  # half-open
    cb.record_failure()
    # back to OPEN, cooldown restarts
    assert cb.is_open() is True
    clock.advance(60)
    assert cb.is_open() is True
    clock.advance(61)
    assert cb.is_open() is False  # half-open again


def test_success_midway_resets_counter():
    cb = CircuitBreaker(threshold=5, cooldown_secs=120, clock=FakeClock())
    for _ in range(4):
        cb.record_failure()
    cb.record_success()
    for _ in range(4):
        cb.record_failure()
    assert cb.is_open() is False
    cb.record_failure()
    assert cb.is_open() is True
