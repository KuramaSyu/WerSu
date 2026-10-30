from __future__ import annotations


class _DummyTable:
    """Minimal table placeholder for unit testing diff logic."""

    def __init__(self, name: str) -> None:
        self.name = name


__all__ = [
    '_DummyTable',
]
