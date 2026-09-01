"""Read a column from an asyncpg row or a plain mapping."""

from __future__ import annotations

from typing import Mapping

import asyncpg


type _Row = asyncpg.Record | Mapping[str, object]


def row_get(row: _Row, key: str) -> object:
    """Return ``row[key]`` for either an ``asyncpg.Record`` or a ``Mapping``.

    The :class:`Table` machinery may surface either depending on
    the dialect / table wrapper, so callers use this helper to
    stay agnostic about which driver produced the row.

    Raises:
        TypeError: when ``row`` is neither an ``asyncpg.Record`` nor a ``Mapping``.
    """
    if isinstance(row, asyncpg.Record):
        return row.get(key)
    if isinstance(row, Mapping):
        return row.get(key)
    raise TypeError(f"Unsupported row type: {type(row)}")


__all__ = ["row_get"]
