"""Helpers for parsing asyncpg row records.

Tiny utility module living next to the other record-shaping
helpers in :mod:`src.utils`.  Kept generic so it can be reused
by any repo that consumes `asyncpg.Record` rows.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, TypeVar, cast

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


def parse_json_columns(
    record: Mapping[str, Any],
    columns: tuple[str, ...],
    *,
    default: Any = None,
) -> Dict[str, Any]:
    """Copy a row mapping, decoding JSON-string ``columns`` into objects.

    asyncpg decodes a ``JSONB`` column as the raw JSON string (and the
    SQLite test schema stores it as ``TEXT``), so repos that hand the
    row straight to an entity must parse those columns back into
    Python objects first.

    Columns that are already decoded (``dict``, ``None``, ...) are
    left untouched.  A string that fails to parse becomes ``default``.

    Args:
        record: the asyncpg row or plain mapping to copy.
        columns: column names that may hold a JSON string.
        default: value used when a string column fails to parse.

    Returns:
        Dict[str, Any]: a shallow copy of ``record`` with the JSON
        columns decoded.
    """
    data: Dict[str, Any] = dict(record)
    for column in columns:
        value = data.get(column)
        if not isinstance(value, str):
            continue
        try:
            data[column] = json.loads(value)
        except (TypeError, ValueError):
            data[column] = default
    return data


def parse_json_object(value: Any, *, default: Any = None) -> Optional[Dict[str, Any]]:
    """Coerce ``value`` into a JSON object (``dict``) or ``default``.

    Accepts an already-decoded ``dict`` verbatim, parses a non-empty
    JSON string, and falls back to ``default`` for ``None``, an empty
    string, malformed JSON, or a non-object JSON value.
    """
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, dict) else default


__all__ = ["all_valid_items", "parse_json_columns", "parse_json_object"]