"""Helpers for collapsing :data:`UndefinedOr` / :data:`UndefinedNoneOr` values."""

from __future__ import annotations

from typing import Optional

from src.api.other.undefined import UndefinedNoneOr, is_undefined


def resolve_undefined_none(value: UndefinedNoneOr[str]) -> Optional[str]:
    """Map a nullable ``UndefinedNoneOr`` into a SQL-friendly ``Optional[str]``.

    * ``UNDEFINED`` -> ``None`` (no value supplied).
    * ``None``      -> ``None`` (explicitly cleared).
    * concrete str -> ``str(value)``.
    """
    if is_undefined(value):
        return None
    if value is None:
        return None
    return str(value)


__all__ = ["resolve_undefined_none"]
