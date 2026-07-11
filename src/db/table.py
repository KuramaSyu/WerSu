from typing import Any, Callable, List, Optional, Dict, cast, Protocol, runtime_checkable, Union
from collections import OrderedDict
from functools import wraps, update_wrapper
from abc import ABC, abstractmethod
import traceback
import logging
import typing

import asyncpg
from asyncpg import Record

from src.api.types import LoggingProvider
from src.db.database import Database
from src.db.sql_builders import (
    DeleteStmtABC,
    InsertStmtABC,
    SelectStmtABC,
    SqlBuilderABC,
    SqlBuilderFactory,
    SqlStatement,
    UpdateStmtABC,
)
from src.utils import asdict, drop_undefined

log: Optional[logging.Logger] = None

def with_log(reraise_exc: bool = True):
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            self = args[0]
            try:
                return_value = await func(*args, **kwargs)
                if self.do_log and log is not None:
                    log.debug(f"{self._executed_sql}\n->{return_value}")
                return return_value
            except Exception as e:
                if self._error_logging and log is not None:
                    log.error(f"{self._executed_sql}")
                    log.exception(f"{traceback.format_exc()}")
                    if reraise_exc:
                        raise e
                    return None
        update_wrapper(wrapper, func)
        return wrapper
    return decorator

def formatter(func: Callable):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        return await func(*args, **kwargs)
    update_wrapper(wrapper, func)
    return wrapper




@runtime_checkable
class TableABC(Protocol):
    """Abstract base class for database table operations.

    Defines the contract for all table implementations, providing a
    standard interface for CRUD operations on PostgreSQL database
    tables.
    """
    name: str

    def get_id_fields(self) -> List[str]:
        """Get the list of column names that form the table's identifier.

        Returns:
            List of column names used as the primary key/identifier.
        """
        ...

    async def insert(
        self,
        where: Dict[str, Any],
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[List[Record]]:
        """Insert a new record into the table.

        Args:
            where: column -> value mapping for the row to insert.
            returning: columns to return from the inserted row. Defaults to '*'.
            on_conflict: ON CONFLICT clause (e.g., 'DO NOTHING'). Defaults to empty.
        """
        ...

    async def upsert(
        self,
        where: Dict[str, Any],
        returning: str = ""
    ) -> Optional[Union[List[Record], Record, str]]:
        """Insert a record or update it if it already exists.

        Uses INSERT ... ON CONFLICT ... DO UPDATE. The columns from
        :meth:`get_id_fields` are the conflict target.
        """
        ...

    async def update(
        self,
        set: Dict[str, Any],
        where: Dict[str, Any],
        returning: str = "*"
    ) -> Optional[Union[List[Record], Record, str]]:
        """Update existing records in the table.
        """
        ...

    async def delete(
        self,
        where: Dict[str, Any],
        returning: str = "*"
    ) -> Optional[List[Record]]:
        """Delete records from the table and return them.
        """
        ...

    async def select(
        self,
        where: Dict[str, Any],
        order_by: Optional[str] = None,
        select: str = "*",
        additional_values: Optional[List] = None,
    ) -> Optional[List[Record]]:
        """Select records from the table with filtering and ordering.

        Args:
            where: column -> value mapping for the WHERE clause.
            order_by: optional ORDER BY fragment (e.g. ``created_at DESC``).
            select: comma-separated columns or ``"*"``.
            additional_values: extra values appended to the bound params.
        """
        ...

    async def select_row(
        self,
        where: Dict[str, Any],
        select: str = "*"
    ) -> Optional[Record]:
        """Select a single row from the table.

        Convenience wrapper that calls :meth:`select` and returns the
        first record.
        """
        ...
    
    async def delete_by_id(self, *id_values: Any) -> Optional[Record]:
        """Delete a single record by its identifier.

        Convenience over :meth:`delete` that uses :meth:`get_id_fields`
        as the WHERE clause. Number of values must match the number of
        id_fields.
        """
        ...

    async def fetch_by_id(self, *id_values: Any, select: str = "*") -> Optional[Record]:
        """Fetch a single record by its identifier.

        Convenience over :meth:`select` that uses :meth:`get_id_fields`
        as the WHERE clause.
        """
        ...

    async def fetch(self, sql: str, *args) -> Optional[List[Record]]:
        """Execute a caller-supplied SQL string.

        The string passes straight through the active
        :class:`src.db.sql_builders.SqlBuilderABC` -- the dialect still
        owns placeholder conventions via :meth:`SqlBuilderABC.fetch`.
        """
        ...

    async def execute(self, sql: str, *args) -> Optional[List[Record]]:
        """Alias for :meth:`fetch`."""
        return await self.fetch(sql, *args)



class Table(TableABC):
    """Concrete implementation of database table operations.

    Implements :class:`TableABC` on top of asyncpg (or the SQLite
    shim used in tests).  All query methods are parameterized.
    """

    def __init__(
        self,
        table_name: str,
        logging_provider: LoggingProvider,
        db: Database,
        error_log: bool = True,
        id_fields: Optional[List[str]] = None,
        dialect: str = "postgres",
        builder: Optional[SqlBuilderABC] = None,
    ):
        """Initialize the Table instance.

        Args:
            table_name: target table in the database.
            logging_provider: callable that returns a configured logger.
            db: any object exposing ``fetch``, ``fetchrow`` and
                ``execute`` -- :class:`src.db.database.Database` and
                :class:`src.db.sqlite_database.SqliteDatabase` both
                qualify.
            error_log: enable error logging. Defaults to True.
            id_fields: columns that form the table's identifier.
                Consumed by :meth:`delete_by_id` and
                :meth:`fetch_by_id`.
            dialect: SQL dialect name forwarded to
                :class:`src.db.sql_builders.SqlBuilderFactory.create`
                (unless ``builder`` is given).  Defaults to
                ``"postgres"`` to preserve historical behaviour.
            builder: pre-built :class:`SqlBuilderABC`.  Inject a
                custom builder to mock or swap SQL generation in
                tests.
        """
        self.name = table_name
        self.db = db
        self.log = logging_provider(__name__, self)
        self.do_log = self.log.level == logging.DEBUG
        self.id_fields = id_fields or []
        self._executed_sql = ""
        self._error_logging = error_log
        self.dialect = dialect
        self.builder: SqlBuilderABC = builder or SqlBuilderFactory.create(
            dialect, name=table_name
        )

    def get_id_fields(self) -> List[str]:
        return self.id_fields

    def _log_statement(self, stmt: SqlStatement) -> None:
        """Cache the last executed statement for debug logs."""
        self._executed_sql = (
            f"SQL:\n{stmt.sql}\nWITH VALUES: {list(stmt.params)}"
        )
        if self.do_log:
            self.log.debug(self._executed_sql)

    async def insert(
        self,
        where: Dict[str, Any],
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[List[Record]]:
        return await self._insert(
            where=where,
            returning=returning,
            on_conflict=on_conflict,
        )

    @with_log()
    async def _insert(
        self,
        where: Dict[str, Any],
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[List[Record]]:
        which_columns = list(where.keys())
        values = list(where.values())

        staged = cast(InsertStmtABC, self.builder.insert()).into(self.name)
        if which_columns:
            staged = staged.columns(*which_columns).values(*values)
        if on_conflict:
            staged = staged.on_conflict(on_conflict)
        if returning:
            staged = staged.returning(*([c.strip() for c in returning.split(",")]))
        stmt = staged.build()
        self._log_statement(stmt)
        return_values = await self.db.fetch(stmt.sql, *stmt.params)
        return return_values

    async def upsert(self, where: Dict[str, Any], returning: str = "") -> Optional[Union[List[Record], Record, str]]:
        return await self._upsert(
            where=where,
            returning=returning
        )

    async def _upsert(
        self,
        where: Dict[str, Any],
        returning: str = ""
    ) -> Optional[Record]:
        which_columns = list(where.keys())
        values = list(where.values())

        assert values and which_columns
        id_fields = self.get_id_fields()
        conflict_target = ", ".join(id_fields) if id_fields else which_columns[0]
        # Skip the conflict target column from the SET clause -- the
        # legacy code did the same, and ``EXCLUDED.<col>`` for the
        # target would be a no-op anyway.
        set_pairs = [
            (col, val) for col, val in zip(which_columns, values) if col != conflict_target
        ]
        on_conflict_fragment = (
            f"({conflict_target}) DO UPDATE SET "
            + ", ".join(f"{col} = EXCLUDED.{col}" for col, _ in set_pairs)
        )
        staged = cast(InsertStmtABC, self.builder.insert()).into(self.name)
        staged = (
            staged
            .columns(*which_columns)
            .values(*values)
            .on_conflict(on_conflict_fragment)
        )
        if returning:
            staged = staged.returning(*([c.strip() for c in returning.split(",")]))
        stmt = staged.build()
        self._log_statement(stmt)
        return_values = await self.db.fetch(stmt.sql, *stmt.params)
        return return_values
    
    async def update(
        self, 
        set: Dict[str, Any], 
        where: Dict[str, Any],
        returning: str = "*"
    ) -> Optional[Union[List[Record], Record, str]]:
        return await self._update(
            set=set,
            where=where,
            returning=returning
        )

    async def _update(
        self,
        set: Dict[str, Any],
        where: Dict[str, Any],
        returning: str = "*"
    ) -> Optional[List[Record]]:
        where = drop_undefined(where)
        set_pairs = list(set.items())

        staged = cast(UpdateStmtABC, self.builder.update()).table(self.name)
        if set_pairs:
            staged = staged.set(**dict(set_pairs))
        if where:
            staged = staged.where(and_=dict(where.items()) if where else None)
        if returning:
            staged = staged.returning(
                *([c.strip() for c in returning.split(",")])
            )
        stmt = staged.build()
        self._log_statement(stmt)
        return_values = await self.db.fetchrow(stmt.sql, *stmt.params)
        return return_values

    async def delete(
        self,
        where: Dict[str, Any],
        returning: str = "*"
    ) -> Optional[List[Record]]:
        return await self._delete(
            where=where,
            returning=returning
        )

    async def _delete(
        self,
        where: Dict[str, Any],
        returning: str = "*"
    ) -> Optional[List[Record]]:
        staged = cast(DeleteStmtABC, self.builder.delete()).from_table(self.name)
        if where:
            staged = staged.where(and_=dict(where.items()))
        if returning:
            staged = staged.returning(
                *([c.strip() for c in returning.split(",")])
            )
        stmt = staged.build()
        self._log_statement(stmt)
        records = await self.db.fetch(stmt.sql, *stmt.params)
        return records

    @with_log()
    @formatter
    async def alter(self):
        pass


    async def select(
        self,
        where: Dict[str, Any],
        order_by: Optional[str] = None,
        select: str = "*",
        additional_values: Optional[List] = None,
    ) -> Optional[List[Record]]:
        return await self._select(
            where=where,
            order_by=order_by,
            select=select,
            additional_values=additional_values
        )

    @with_log()
    @formatter
    async def _select(
        self,
        where: Dict[str, Any],
        order_by: Optional[str] = None,
        select: str = "*",
        additional_values: Optional[List] = None,
    ) -> Optional[List[Record]]:
        staged = cast(SelectStmtABC, self.builder.select()).from_table(self.name)
        if select and select != "*":
            staged = staged.columns(*[c.strip() for c in select.split(",")])
        if where:
            staged = staged.where(and_=dict(where.items()))
        if order_by:
            staged = staged.order_by(order_by)
        stmt = staged.build()
        self._log_statement(stmt)

        # additional_values get appended to the bound params -- the
        # legacy API expected them to bind into leftover ``$N`` slots
        # in hand-rolled SQL.  Callers that need that should use
        # ``fetch()`` for full control.
        params = list(stmt.params)
        if additional_values:
            params.extend(additional_values)

        records = await self.db.fetch(stmt.sql, *params)
        return records

    async def select_row(
        self,
        where: Dict[str, Any],
        select: str = "*"
    ) -> Optional[Record]:
        return await self._select_row(
            where=where,
            select=select
        )
    async def _select_row(
        self,
        where: Dict[str, Any],
        select: str = "*"
    ) -> Optional[Record]:

        records = await self.select(where, select=select)
        if not records:
            return None
        return records[0]

    async def delete_by_id(self, *id_values: Any) -> Optional[Record]:
        """Delete a single record by its identifier.

        Convenience over :meth:`delete` that uses :meth:`get_id_fields`
        as the WHERE clause. Number of values must match the number of
        id_fields.

        Returns:
            The deleted record as a dictionary, or None if not found.
        """
        if not self.id_fields:
            raise ValueError("Table has no id_fields configured")
        if len(id_values) != len(self.id_fields):
            raise ValueError(f"Expected {len(self.id_fields)} id values, got {len(id_values)}")

        where = dict(zip(self.id_fields, id_values))
        ret = await self.delete(where=where)
        if not ret:
            return None
        return ret[0]

    async def fetch_by_id(self, *id_values: Any, select: str = "*") -> Optional[Record]:
        """Fetch a single record by its identifier.

        Convenience over :meth:`select` that uses :meth:`get_id_fields`
        as the WHERE clause.

        Returns:
            The record as a dictionary, or None if not found.
        """
        if not self.id_fields:
            raise ValueError("Table has no id_fields configured")
        if len(id_values) != len(self.id_fields):
            raise ValueError(f"Expected {len(self.id_fields)} id values, got {len(id_values)}")

        where = dict(zip(self.id_fields, id_values))
        rec = await self.select(where=where, select=select)
        if not rec:
            return None
        return rec[0]

    async def fetch(self, sql: str, *args) -> Optional[List[Record]]:
        return await self._fetch(
            sql,
            *args
        )

    @with_log()
    @formatter
    async def _fetch(self, sql: str, *args) -> Optional[List[Record]]:
        stmt = self.builder.fetch(sql, args)
        self._log_statement(stmt)
        return await self.db.fetch(stmt.sql, *stmt.params)

    async def execute(self, sql: str, *args) -> Optional[List[Record]]:
        return await self.fetch(sql, *args)


def setup_table_logging(logging_provider: LoggingProvider):
    """Install the legacy ``log`` global used by :func:`with_log` and
    :func:`formatter`.
    """
    global log
    log = logging_provider(__name__, "decorator")
