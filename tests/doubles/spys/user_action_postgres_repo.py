from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class _CapturingTable:
    """In-memory :class:`TableABC` stand-in that records inserts and updates.

    The repo only calls :meth:`insert`, :meth:`update`, and
    :meth:`fetch` (via :meth:`get_actions`), so the recording set is
    small.  Each call appends a snapshot of the bound values.
    """

    insert_calls: List[Dict[str, Any]] = field(default_factory=list)
    update_calls: List[Dict[str, Any]] = field(default_factory=list)
    fetch_calls: List[Dict[str, Any]] = field(default_factory=list)
    stored: List[Dict[str, Any]] = field(default_factory=list)

    async def insert(self, where, returning: str = "*", on_conflict: str = ""):
        self.insert_calls.append(dict(where))
        for row in self.stored:
            for k, v in where.items():
                row.setdefault(k, v)
        if not self.stored:
            self.stored.append(dict(where))
        return [dict(self.stored[-1])]

    async def update(self, set, where, returning: str = "*"):
        self.update_calls.append({"set": dict(set), "where": dict(where)})
        for row in self.stored:
            if all(row.get(k) == v for k, v in where.items()):
                row.update(set)
                return dict(row)
        return None

    async def fetch(self, sql, *args):
        self.fetch_calls.append({"sql": sql, "args": list(args)})
        return [dict(row) for row in self.stored]

    async def fetchrow(self, sql, *args):
        return None

    async def select_row(self, where, select: str = "*"):
        return None

    async def delete(self, where, returning: str = "*"):
        return None

    async def upsert(self, where, returning: str = ""):
        return None

    async def select(self, where, order_by=None, select: str = "*", additional_values=None):
        return None

    async def delete_by_id(self, *id_values):
        return None

    async def fetch_by_id(self, *id_values, select: str = "*"):
        return None

    async def execute(self, sql, *args):
        return await self.fetch(sql, *args)

    def get_id_fields(self) -> List[str]:
        return ["id"]


__all__ = [
    '_CapturingTable',
]
