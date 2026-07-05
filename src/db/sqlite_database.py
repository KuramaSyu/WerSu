"""SQLite-backed ``Database`` shim for unit tests.

The real production path is :class:`src.db.database.Database`, which
sits on top of ``asyncpg`` and a Postgres connection pool.  Postgres
isn't always available -- locally, in CI sandboxes, in fast unit
suites -- so this shim gives the rest of the data layer (the
:class:`src.db.table.Table` implementation and every repo that uses
it) the same ``fetch`` / ``fetchrow`` / ``execute`` surface backed by
the stdlib ``sqlite3`` driver.

Why not use ``aiosqlite``?  The stdlib ``sqlite3`` driver is already
in the test environment.  Wrapping the synchronous calls with
``asyncio.to_thread`` keeps the async contract of the rest of the
codebase intact without pulling in another dependency.

Notes:

* Every DML statement inside this shim runs inside a transaction, to
  mirror :class:`src.db.database.Database`'s ``@acquire`` semantics.
  Tests can rely on the same rollback / commit behaviour they get in
  production.
* ``fetch`` returns ``list[dict[str, Any]]`` -- a drop-in stand-in
  for ``asyncpg.Record`` since ``Table`` only ever calls
  ``record.keys()`` / ``record[...]`` on the result.
* ``fetchrow`` returns the first row as ``dict[str, Any]`` or
  ``None``.
* ``execute`` accepts a multi-statement string and returns ``""``,
  matching the contract used by :class:`src.db.database.Database` for
  the ``init.sql`` bootstrap.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class SqliteDatabase:
    """In-memory SQLite stand-in for :class:`src.db.database.Database`.

    The shim is intentionally tiny: it only implements what
    :class:`src.db.table.Table` calls.  Adding new methods is welcome
    but should be done together with matching changes in the
    Postgres :class:`src.db.database.Database` so the two stay in
    lockstep.

    Args:
        dsn: either ``":memory:"`` (the default), a ``":memory:?..."``
            shared-cache URI, or a filesystem path.  Anything else is
            rejected.
    """

    def __init__(self, dsn: str = ":memory:") -> None:
        self._dsn = dsn
        self._lock = threading.Lock()
        self._connection: Optional[sqlite3.Connection] = None

    async def init_db(self, init_file: Optional[Union[str, Path]] = None) -> None:
        """Open the SQLite database and optionally apply a schema.

        Args:
            init_file: optional path to a SQL file containing ``CREATE
                TABLE`` / ``INSERT`` statements separated by ``;`` -- a
                drop-in for the ``init.sql`` that
                :class:`src.db.database.Database` consumes.  ``None``
                skips the bootstrap (the test sets up its own
                schema).
        """
        await asyncio.to_thread(self._open_sync, init_file)

    def _open_sync(self, init_file: Optional[Union[str, Path]]) -> None:
        check_same_thread = self._dsn != ":memory:"
        self._connection = sqlite3.connect(
            self._dsn,
            check_same_thread=check_same_thread,
        )
        self._connection.row_factory = sqlite3.Row
        if init_file is not None:
            with open(init_file, "r", encoding="utf-8") as fh:
                script = fh.read()
            self._connection.executescript(script)
            self._connection.commit()

    async def close(self) -> None:
        """Close the SQLite connection (idempotent)."""
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError(
                "SqliteDatabase.init_db() must be awaited before use"
            )
        return self._connection

    async def execute(self, query: str, *args: Any) -> str:
        """Run ``query`` inside an implicit transaction.

        Mirrors :class:`src.db.database.Database.execute`.  ``query``
        can contain multiple ``;``-separated statements.
        """
        return await asyncio.to_thread(self._execute_sync, query, args)

    def _execute_sync(self, query: str, args: Any) -> str:
        with self._lock:
            try:
                self.connection.executescript(query)
                self.connection.commit()
            except sqlite3.Error as exc:
                self.connection.rollback()
                raise
        return ""

    async def fetch(self, query: str, *args: Any) -> List[Dict[str, Any]]:
        """Run ``query`` and return all rows as dicts."""
        return await asyncio.to_thread(self._fetch_sync, query, args)

    def _fetch_sync(self, query: str, args: Any) -> List[Dict[str, Any]]:
        with self._lock:
            try:
                cursor = self.connection.execute(query, args)
                rows = cursor.fetchall()
                self.connection.commit()
            except sqlite3.Error:
                self.connection.rollback()
                raise
        return [dict(row) for row in rows]

    async def fetchrow(self, query: str, *args: Any) -> Optional[Dict[str, Any]]:
        """Run ``query`` and return the first row, or ``None``."""
        return await asyncio.to_thread(self._fetchrow_sync, query, args)

    def _fetchrow_sync(
        self, query: str, args: Any
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            try:
                cursor = self.connection.execute(query, args)
                row = cursor.fetchone()
                self.connection.commit()
            except sqlite3.Error:
                self.connection.rollback()
                raise
        return dict(row) if row is not None else None
