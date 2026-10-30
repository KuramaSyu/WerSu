from __future__ import annotations


class FakeClock:
    """Manually advanced clock for deterministic TTL tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


__all__ = [
    'FakeClock',
]
