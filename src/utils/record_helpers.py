"""Helpers for parsing asyncpg row records.

Tiny utility module living next to the other record-shaping
helpers in :mod:`src.utils`.  Kept generic so it can be reused
by any repo that consumes `asyncpg.Record` rows.
"""

from __future__ import annotations

from typing import Any, List, TypeVar, cast

import asyncpg

_T = TypeVar("_T")


def all_valid_items(
    record: asyncpg.Record,
    field: str,
    *,
    cast_to: type[_T],
) -> List[_T]:
    """Return ``record[field]`` as a list of non-null items of type `cast_to`.

    Args:
        record: the `asyncpg.Record` row straight from
            `Database.fetchrow` / `Database.fetch`.
        field: key holding the array.  Must be one of the
            columns the originating SQL actually selected.
        cast_to: element type the caller wants back.

    Raises:
        KeyError: when `field` is not one of the columns the row
            selected -- indicates a SQL/repo mismatch rather than
            an empty result.

    Returns:
        List[_T]: every truthy element cast to `cast_to`.
        Returns `[]` when the column value is falsy (`NULL`,
        empty array, ...).
    """
    if field not in record.keys():
        raise KeyError(
            f"column {field!r} not present in asyncpg.Record; "
            f"available columns: {list(record.keys())!r}"
        )
    raw: List[Any] = record[field] or []
    return [cast(_T, v) for v in raw if v]


__all__ = ["all_valid_items"]