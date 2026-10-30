from __future__ import annotations

from src.db.table import TableABC
from typing import List, Optional


class _FakeLinkTable(TableABC):
    """In-memory :class:`TableABC` for the ``note.attachment_note_link`` table.

    Stores rows as a list of dicts and records the ``insert`` / ``delete``
    calls so tests can inspect both the resulting linkage and the exact
    operations the code under test performed.
    """

    def __init__(self) -> None:
        self.rows: List[dict] = []
        self.insert_calls: List[dict] = []
        self.delete_calls: List[dict] = []

    async def insert(self, record: dict) -> None:
        # Dedup: keep unique (note_id, attachment_key) pairs.
        key = (record["note_id"], record["attachment_key"])
        if not any(
            (r["note_id"], r["attachment_key"]) == key for r in self.rows
        ):
            self.rows.append(record)
            self.insert_calls.append(record)

    async def delete(self, where: dict) -> None:
        self.delete_calls.append(where)
        self.rows = [
            r
            for r in self.rows
            if not all(r.get(k) == v for k, v in where.items())
        ]

    async def select(self, where: Optional[dict] = None) -> List[dict]:
        if not where:
            return list(self.rows)
        return [
            r
            for r in self.rows
            if all(r.get(k) == v for k, v in where.items())
        ]


class _FakeUserLinkTable(TableABC):
    """In-memory :class:`TableABC` for the ``note.attachment_user_link`` table."""

    def __init__(self) -> None:
        self.rows: List[dict] = []

    async def insert(self, record: dict) -> None:
        key = (record["user_id"], record["attachment_key"])
        if not any(
            (r["user_id"], r["attachment_key"]) == key for r in self.rows
        ):
            self.rows.append(record)

    async def delete(self, where: dict) -> None:
        self.rows = [
            r
            for r in self.rows
            if not all(r.get(k) == v for k, v in where.items())
        ]

    async def select(self, where: Optional[dict] = None) -> List[dict]:
        if not where:
            return list(self.rows)
        return [
            r
            for r in self.rows
            if all(r.get(k) == v for k, v in where.items())
        ]


__all__ = [
    '_FakeLinkTable',
    '_FakeUserLinkTable',
]
