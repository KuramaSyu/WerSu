"""Wire-format pin for :class:`~src.db.repos.user.user_action.UserActionPostgresRepo`.

The ``user_action`` table stores ``execute_at`` / ``executed_at`` as
``TIMESTAMP WITHOUT TIME ZONE``. asyncpg's encoder rejects aware
datetimes bound to that column (the codec's internal epoch
subtraction raises ``TypeError: can't subtract offset-naive and
offset-aware datetimes``).

The production wiring in :mod:`src.main` calls
``get_now=lambda: datetime.now()`` -- a naive local datetime -- and
the repo is expected to pass that through unchanged.  These tests
drive the repo against a fake :class:`Table` that records what was
bound, so the wire-format contract is pinned at the call site
without needing a real Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from src.api.other.undefined import UNDEFINED
from src.db.entities.user.user_action import UserActionEntity
from src.db.repos.user.user_action import UserActionPostgresRepo


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


def _repo() -> tuple[UserActionPostgresRepo, _CapturingTable]:
    table = _CapturingTable()
    return UserActionPostgresRepo(table=table), table


# -- write path --------------------------------------------------------


async def test_add_action_binds_naive_utc_datetime_for_execute_at() -> None:
    """The repo passes ``execute_at`` through unchanged -- no tz attach.

    Mirrors the production wiring (``get_now=lambda: datetime.now()``
    in :mod:`src.main`) and the read-back contract where asyncpg
    returns ``TIMESTAMP WITHOUT TIME ZONE`` as naive.
    """
    repo, table = _repo()
    naive = datetime(2026, 8, 4, 17, 40)
    await repo.add_action(
        UserActionEntity(
            user_id="user-1",
            action="disable",
            execute_at=naive,
        )
    )
    assert len(table.insert_calls) == 1
    bound = table.insert_calls[0]["execute_at"]
    assert bound.tzinfo is None
    assert bound == naive


async def test_update_action_binds_naive_executed_at() -> None:
    """The repo passes ``executed_at`` through unchanged when the
    process sets it to the result of ``get_now=lambda: datetime.now()``.
    """
    repo, table = _repo()
    action = UserActionEntity(
        id="action-1",
        user_id="user-1",
        action="disable",
        execute_at=datetime(2026, 8, 4, 17, 40),
        executed_at=UNDEFINED,
    )
    table.stored.append({
        "id": "action-1",
        "user_id": "user-1",
        "action": "disable",
        "execute_at": datetime(2026, 8, 4, 17, 40),
        "executed_at": None,
    })

    action.executed_at = datetime(2026, 8, 4, 17, 41)
    await repo.update_action(action)

    assert len(table.update_calls) == 1
    bound = table.update_calls[0]["set"]["executed_at"]
    assert bound.tzinfo is None
    assert bound == datetime(2026, 8, 4, 17, 41)


# -- read path ---------------------------------------------------------


async def test_from_record_keeps_naive_datetimes() -> None:
    """``_from_record`` round-trips the column shape: naive in, naive out.

    asyncpg returns ``TIMESTAMP WITHOUT TIME ZONE`` columns as naive
    datetimes; the repo must not silently attach a tzinfo (doing so
    would re-introduce the encoder crash on the next update).
    """
    record = {
        "id": "action-1",
        "user_id": "user-1",
        "action": "disable",
        "execute_at": datetime(2026, 8, 4, 17, 40),
        "executed_at": None,
    }
    entity = UserActionPostgresRepo._from_record(record)
    assert entity.execute_at.tzinfo is None
    assert entity.executed_at is None
