from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class _Call:
    """One captured table method invocation."""

    name: str
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)


class RecordingTable:
    """In-memory TableABC stand-in that records every call.

    Args:
        name: the table name the fake reports to callers.
        seed: optional initial rows keyed by id.  Pass these in
            when a test wants the read paths to return a known
            shape without first running an insert.
        fetch_returns: optional override list returned by
            :meth:`fetch`; when set, it bypasses the row scan.
            Use it for raw-SQL paths that do not map cleanly
            to the id/user_id lookup.
        id_fields: columns that form the table's identifier.
            Defaults to ``["id"]``; override for composite keys.
    """

    name: str = "recording"

    def __init__(
        self,
        name: str = "recording",
        seed: Optional[Dict[str, Dict[str, Any]]] = None,
        fetch_returns: Optional[List[Dict[str, Any]]] = None,
        id_fields: Optional[List[str]] = None,
    ) -> None:
        self.name = name
        self.calls: List[_Call] = []
        self.stored: Dict[str, Dict[str, Any]] = dict(seed or {})
        self.fetch_returns = fetch_returns
        self.id_fields: List[str] = list(id_fields or ["id"])
        self._next_id: int = 0

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(_Call(name=name, args=args, kwargs=kwargs))

    def _new_id(self) -> str:
        self._next_id += 1
        return f"row-{self._next_id}"

    def _matches(self, row: Dict[str, Any], where: Dict[str, Any]) -> bool:
        return all(row.get(k) == v for k, v in where.items())

    # ---- helpers used by tests ------------------------------------

    def calls_of(self, name: str) -> List[_Call]:
        """Return every captured call whose method name is `name`."""
        return [c for c in self.calls if c.name == name]

    def reset(self) -> None:
        """Drop captured calls and seeded rows; leave id counter alone."""
        self.calls.clear()
        self.stored.clear()

    # ---- write ----------------------------------------------------

    async def insert(
        self,
        where: Dict[str, Any],
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[List[Dict[str, Any]]]:
        self._record("insert", where=dict(where), returning=returning)
        row = dict(where)
        rid = self._new_id()
        row["id"] = rid
        self.stored[rid] = row
        return [row]

    async def upsert(
        self,
        where: Dict[str, Any],
        returning: str = "",
    ) -> Optional[List[Dict[str, Any]]]:
        self._record("upsert", where=dict(where), returning=returning)
        return [dict(where)]

    async def update(
        self,
        set: Dict[str, Any],
        where: Dict[str, Any],
        returning: str = "*",
    ) -> Optional[Dict[str, Any]]:
        self._record("update", set=dict(set), where=dict(where))
        rid = where.get("id")
        if rid is None or rid not in self.stored:
            return None
        self.stored[rid].update(set)
        return dict(self.stored[rid])

    async def delete(
        self,
        where: Dict[str, Any],
        returning: str = "*",
    ) -> Optional[List[Dict[str, Any]]]:
        self._record("delete", where=dict(where), returning=returning)
        return [dict(r) for r in self.stored.values() if self._matches(r, where)]

    # ---- read -----------------------------------------------------

    async def select(
        self,
        where: Dict[str, Any],
        order_by: Optional[str] = None,
        select: str = "*",
        additional_values: Optional[List[Any]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        self._record(
            "select",
            where=dict(where),
            order_by=order_by,
            select=select,
        )
        return [dict(r) for r in self.stored.values() if self._matches(r, where)]

    async def select_row(
        self,
        where: Dict[str, Any],
        select: str = "*",
    ) -> Optional[Dict[str, Any]]:
        self._record("select_row", where=dict(where), select=select)
        for row in self.stored.values():
            if self._matches(row, where):
                return dict(row)
        return None

    async def delete_by_id(self, *id_values: Any) -> Optional[Dict[str, Any]]:
        if not self.id_fields or len(id_values) != len(self.id_fields):
            return None
        where = dict(zip(self.id_fields, id_values))
        for rid, row in list(self.stored.items()):
            if all(row.get(k) == v for k, v in where.items()):
                return self.stored.pop(rid)
        return None

    async def fetch_by_id(
        self, *id_values: Any, select: str = "*"
    ) -> Optional[Dict[str, Any]]:
        self._record("fetch_by_id", id_values=id_values, select=select)
        if not id_values:
            return None
        rid = id_values[0]
        if rid in self.stored:
            return dict(self.stored[rid])
        for row in self.stored.values():
            if any(row.get(k) == rid for k in self.id_fields):
                return dict(row)
        return None

    async def fetch(
        self, sql: str, *args: Any
    ) -> Optional[List[Dict[str, Any]]]:
        self._record("fetch", sql=" ".join(sql.split()), args=args)
        # explicit override wins -- use it for raw-SQL paths
        # that don't map to the id/user_id lookup.
        if self.fetch_returns is not None:
            return [dict(r) for r in self.fetch_returns]
        # default: match the first bound arg against id or user_id
        # (covers the common "WHERE user_id = $1" pattern that
        # the auth repo's select / hydration paths use).
        if args:
            key = args[0]
            out = []
            for row in self.stored.values():
                if row.get("id") == key or row.get("user_id") == key:
                    out.append(dict(row))
            return out
        return [dict(r) for r in self.stored.values()]

    async def execute(
        self, sql: str, *args: Any
    ) -> Optional[List[Dict[str, Any]]]:
        return await self.fetch(sql, *args)

    def get_id_fields(self) -> List[str]:
        return list(self.id_fields)


__all__ = [
    '_Call',
    'RecordingTable',
]
