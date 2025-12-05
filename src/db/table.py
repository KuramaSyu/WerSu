from typing import Any, Callable, List, Optional, Dict, Sequence, cast, Protocol, runtime_checkable, Union
from collections import OrderedDict
from functools import wraps, update_wrapper
from abc import ABC, abstractmethod
import traceback
import logging
import typing

import pandas as pd
import asyncpg
from pandas.core.frame import SequenceNotStr

from api.types import loggingProvider
from db.database import Database

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
        self = args[0]
        return_value = await func(*args, **kwargs)
        if self._as_dataframe:

            columns: List[str] = []
            if isinstance(return_value, list):
                if len(return_value) > 0:
                    columns = [k for k in return_value[0].keys()]
                else:
                    columns = []
            elif isinstance(return_value, dict):
                columns = [k for k in return_value.keys()]
            else:
                raise TypeError(f"{type(return_value)} is not supported. Only list and dict can be converted to dataframe.")
            return_value = pd.DataFrame(data=return_value, columns=cast(SequenceNotStr, columns))
        return return_value
    update_wrapper(wrapper, func)
    return wrapper




@runtime_checkable
class TableABC(Protocol):
    """Abstract base class defining the interface for database table operations.
    
    This protocol defines the contract for all table implementations, providing
    a standard interface for CRUD operations on PostgreSQL database tables.
    Implementations can return results as either asyncpg Records or pandas DataFrames.
    
    Attributes:
        name: The name of the database table.
        db: Database connection instance for executing queries.
        log: Logger instance for debugging and error tracking.
        do_log: Flag indicating whether debug logging is enabled.
        _executed_sql: The last executed SQL query with values (for logging).
        _as_dataframe: Flag to determine if results should be returned as DataFrame.
        _error_logging: Flag to enable/disable error logging.
    
    Example:
        >>> table = Table('users', logging_provider, db)
        >>> await table.insert(where={'name': 'John', 'email': 'john@example.com'})
    """
    
    def return_as_dataframe(self, b: bool) -> None:
        """Configure whether query results should be returned as pandas DataFrame.
        
        When enabled, query results will be automatically converted from
        asyncpg Records to pandas DataFrame format.
        
        Args:
            b: True to return results as DataFrame, False for asyncpg Records.
        """
        ...
    
    def get_id_fields(self) -> List[str]:
        """Get the list of column names that form the table's identifier.
        
        Returns:
            List of column names used as the primary key/identifier.
        """
        ...
    
    async def insert(
        self, 
        where: Dict[str, Any] | pd.DataFrame,
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[Union[List[asyncpg.Record], asyncpg.Record]]:
        """Insert a new record into the table.
        
        Executes an INSERT statement with optional conflict handling.
        
        Args:
            where: Dictionary mapping column names to values, or DataFrame with
                columns as field names and rows as records to insert.
            returning: Columns to return from the inserted row. Defaults to '*'.
            on_conflict: ON CONFLICT clause (e.g., 'DO NOTHING'). Defaults to empty.
        
        Returns:
            The inserted record(s) as specified by the returning parameter,
            or None if the operation fails.
        
        Example:
            >>> await table.insert(where={'name': 'Alice', 'age': 30})
            >>> await table.insert(where={'name': 'Bob'}, on_conflict='DO NOTHING')
        """
        ...
    
    async def upsert(
        self, 
        where: Dict[str, Any] | pd.DataFrame,
        returning: str = ""
    ) -> Optional[Union[List[asyncpg.Record], asyncpg.Record, pd.DataFrame, str]]:
        """Insert a record or update it if it already exists.
        
        Uses INSERT ... ON CONFLICT ... DO UPDATE to implement upsert logic.
        The columns from get_id_fields() are treated as the conflict target.
        
        Args:
            where: Dictionary mapping column names to values, or DataFrame with
                columns as field names and rows as records to upsert.
            returning: Columns to return. Defaults to empty (no return).
        
        Returns:
            The upserted record(s) if returning is specified, or execution status.
            Can be DataFrame if return_as_dataframe is enabled.
        
        Example:
            >>> await table.upsert(where={'id': 1, 'name': 'Alice', 'age': 31})
        """
        ...
    
    async def update(
        self, 
        set: Dict[str, Any], 
        where: Dict[str, Any] | pd.DataFrame,
        returning: str = "*"
    ) -> Optional[Union[List[asyncpg.Record], asyncpg.Record, pd.DataFrame, str]]:
        """Update existing records in the table.
        
        Executes an UPDATE statement with WHERE clause filtering.
        
        Args:
            set: Dictionary mapping column names to new values.
            where: Dictionary mapping column names to filter values, or DataFrame
                with columns as field names and rows as filter criteria.
                Only records matching all conditions will be updated.
            returning: Columns to return from updated rows. Defaults to '*'.
        
        Returns:
            The updated record(s) as specified by returning parameter.
            Can be DataFrame if return_as_dataframe is enabled.
        
        Example:
            >>> await table.update(
            ...     set={'age': 32, 'city': 'NYC'},
            ...     where={'id': 1}
            ... )
        """
        ...
    
    async def delete(
        self, 
        where: Dict[str, Any] | pd.DataFrame,
    ) -> Optional[Union[List[Dict[str, Any]], pd.DataFrame]]:
        """Delete records from the table and return them.
        
        Executes a DELETE statement with WHERE clause and returns deleted records.
        
        Args:
            where: Dictionary mapping column names to filter values, or DataFrame
                with columns as field names and rows as filter criteria.
        
        Returns:
            List of deleted records as dictionaries, or DataFrame if
            return_as_dataframe is enabled. None if operation fails.
        
        Example:
            >>> await table.delete(where={'id': 1})
            >>> await table.delete(where={'status': 'inactive'})
        """
        ...
    
    async def select(
        self, 
        where: Dict[str, Any] | pd.DataFrame,
        order_by: Optional[str] = None, 
        select: str = "*",
        additional_values: Optional[List] = None,
    ) -> Optional[Union[List[Dict[str, Any]], pd.DataFrame]]:
        """Select records from the table with filtering and ordering.
        
        Executes a SELECT statement with WHERE clause and optional ORDER BY.
        
        Args:
            where: Dictionary mapping column names to filter values, or DataFrame
                with columns as field names and rows as filter criteria.
            order_by: ORDER BY clause (e.g., 'created_at DESC').
            select: Columns to select. Defaults to '*'.
            additional_values: Additional parameterized values to append
                to WHERE conditions (for complex queries).
        
        Returns:
            List of selected records as dictionaries, or DataFrame if
            return_as_dataframe is enabled. None if no records found.
        
        Example:
            >>> await table.select(
            ...     where={'status': 'active'},
            ...     order_by='created_at DESC',
            ...     select='id, name'
            ... )
        """
        ...
    
    async def select_row(
        self, 
        where: Dict[str, Any] | pd.DataFrame,
        select: str = "*"
    ) -> Optional[asyncpg.Record]:
        """Select a single row from the table.
        
        Convenience method that calls select() and returns only the first record.
        
        Args:
            where: Dictionary mapping column names to filter values, or DataFrame
                with columns as field names.
            select: Columns to select. Defaults to '*'.
        
        Returns:
            Single record as asyncpg.Record, or None if no record found.
        
        Example:
            >>> row = await table.select_row(where={'id': 1})
        """
        ...
    
    async def delete_by_id(self, *id_values: Any) -> Optional[Dict[str, Any]]:
        """Delete a single record by its identifier.
        
        Convenience method for deleting a record using id_fields as filter.
        Number of values must match the number of id_fields.
        
        Args:
            *id_values: Values for each id field in order.
        
        Returns:
            The deleted record as a dictionary, or None if not found.
        
        Example:
            >>> await table.delete_by_id(123)  # if id_fields is ['id']
            >>> await table.delete_by_id(user_id, org_id)  # if id_fields is ['user_id', 'org_id']
        """
        ...
    
    async def fetch_by_id(self, *id_values: Any) -> Optional[Dict[str, Any]]:
        """Fetch a single record by its identifier.
        
        Convenience method for selecting a record using id_fields as filter.
        Number of values must match the number of id_fields.
        
        Args:
            *id_values: Values for each id field in order.
        
        Returns:
            The record as a dictionary, or None if not found.
        
        Example:
            >>> user = await table.fetch_by_id(123)  # if id_fields is ['id']
            >>> user = await table.fetch_by_id(user_id, org_id)  # if id_fields is ['user_id', 'org_id']
        """
        ...
    
    async def fetch(self, sql: str, *args) -> Optional[Union[List[asyncpg.Record], pd.DataFrame]]:
        """Execute a custom SQL query and return results.
        
        Provides direct access to execute arbitrary SQL with parameterized values.
        Results are logged if debug logging is enabled.
        
        Args:
            sql: SQL query string with $1, $2, ... placeholders for parameters.
            *args: Values to substitute for placeholders in order.
        
        Returns:
            Query results as list of asyncpg Records, or DataFrame if
            return_as_dataframe is enabled. None if query fails.
        
        Example:
            >>> await table.fetch(
            ...     'SELECT * FROM users WHERE age > $1 AND city = $2',
            ...     25, 'NYC'
            ... )
        """
        ...
    
    async def execute(self, sql: str, *args) -> Optional[Union[List[asyncpg.Record], pd.DataFrame]]:
        """Execute a custom SQL query.
        
        Alias for fetch() method. Executes arbitrary SQL with parameterized values.
        
        Args:
            sql: SQL query string with $1, $2, ... placeholders for parameters.
            *args: Values to substitute for placeholders in order.
        
        Returns:
            Query results as list of asyncpg Records, or DataFrame if
            return_as_dataframe is enabled. None if query fails.
        
        Example:
            >>> await table.execute('DELETE FROM logs WHERE created_at < $1', cutoff_date)
        """
        ...
    
    @staticmethod
    def create_where_statement(columns: List[str], dollar_start: int = 1) -> str:
        """Create a parameterized WHERE clause from column names.
        
        Generates a WHERE clause string with AND conditions for each column,
        using PostgreSQL's $n placeholder syntax.
        
        Args:
            columns: List of column names to include in WHERE clause.
            dollar_start: Starting index for $n placeholders. Defaults to 1.
        
        Returns:
            WHERE clause string (without 'WHERE' keyword), e.g.,
            'id=$1 AND name=$2'.
        
        Example:
            >>> Table.create_where_statement(['id', 'status'])
            'id=$1 AND status=$2'
            >>> Table.create_where_statement(['email'], dollar_start=3)
            'email=$3'
        """
        ...
    
    def _create_sql_log_message(self, sql: str, values: List[Any]) -> None:
        """Create formatted SQL log message for debugging.
        
        Stores the SQL query and parameter values in a formatted string
        for use in debug logging and error messages.
        
        Args:
            sql: The SQL query string.
            values: List of parameter values used in the query.
        """
        ...


class Table:
    """Concrete implementation of database table operations.
    
    Provides complete CRUD (Create, Read, Update, Delete) operations for
    PostgreSQL database tables with built-in logging, error handling, and
    optional DataFrame conversion.
    
    This class implements the TableABC protocol and uses asyncpg for database
    operations. All query methods support parameterized queries to prevent
    SQL injection.
    
    Attributes:
        name: The name of the database table.
        db: Database connection instance.
        log: Logger instance for this table.
        do_log: True if debug logging is enabled.
        _executed_sql: Last executed SQL with values (for logging).
        _as_dataframe: When True, return results as pandas DataFrame.
        _error_logging: When True, log errors and exceptions.
        id_fields: Column names that form the table's identifier.
    
    Example:
        >>> db = Database(...)
        >>> users_table = Table('users', logging_provider, db, id_fields=['id'])
        >>> await users_table.insert(where={'name': 'Alice', 'age': 30})
        >>> users = await users_table.select(where={'status': 'active'})
    """

    def __init__(
        self, 
        table_name: str,
        logging_provider: loggingProvider, 
        db: Database,
        error_log: bool = True,
        id_fields: Optional[List[str]] = None,
    ):
        """Initialize the Table instance.
        
        Args:
            table_name: Name of the table in the database.
            logging_provider: Function that returns a configured logger.
            db: Database instance for executing queries.
            error_log: Enable error logging. Defaults to True.
            id_fields: List of column names that form the table's identifier.
                Used by delete_by_id() and fetch_by_id() methods.
        """

        self.name = table_name
        self.db = db
        self.log = logging_provider(__name__, self.name)
        self.do_log = self.log.level == logging.DEBUG
        self.id_fields = id_fields or []
        self._executed_sql = ""
        self._as_dataframe: bool = False
        self._error_logging = error_log
    
    def get_id_fields(self) -> List[str]:
        """Get the list of column names that form the table's identifier.
        
        Returns:
            List of column names used as the primary key/identifier.
        """
        return self.id_fields

    def return_as_dataframe(self, b: bool) -> None:
        self._as_dataframe = b

    @with_log()
    async def insert(
        self, 
        where: Dict[str, Any] | pd.DataFrame,
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[asyncpg.Record]:
        """Insert a new record into the table.
        
        Executes an INSERT statement with optional conflict handling.
        Automatically handles both dictionary and DataFrame formats.
        
        Args:
            where: Dictionary mapping column names to values, or DataFrame with
                columns as field names and rows as records to insert.
            returning: Columns to return from inserted row. Defaults to '*'.
            on_conflict: ON CONFLICT clause (e.g., 'DO NOTHING'). Defaults to empty.
        
        Returns:
            The inserted record(s) or None if operation fails.
        """
        # Convert DataFrame to dict-like format
        if isinstance(where, pd.DataFrame):
            # For now, handle single row DataFrame
            if len(where) != 1:
                raise ValueError("DataFrame must contain exactly one row for insert")
            where = dict(where.iloc[0])
        
        which_columns = list(where.keys())
        values = list(where.values())

        values_chain = [f'${num}' for num in range(1, len(values)+1)]
        sql = (
            f"INSERT INTO {self.name} ({', '.join(which_columns)})\n"
            f"VALUES ({', '.join(values_chain)})\n" 
        )
        if on_conflict:
            sql += f"ON CONFLICT {on_conflict}\n"
        if returning:
            sql += f"RETURNING {returning}\n"
        self._create_sql_log_message(sql, values)
        return_values = await self.db.fetch(sql, *values)
        return return_values

    @with_log()
    @formatter
    async def upsert(
        self, 
        where: Dict[str, Any] | pd.DataFrame,
        returning: str = ""
    ) -> Optional[asyncpg.Record]:
        """Insert a record or update if it already exists (upsert).
        
        Uses INSERT ... ON CONFLICT ... DO UPDATE to implement upsert logic.
        The columns from get_id_fields() are treated as the conflict target.
        
        Args:
            where: Dictionary mapping column names to values, or DataFrame with
                columns as field names and rows as records to upsert.
            returning: Columns to return. Defaults to empty (no return).
        
        Returns:
            The upserted record(s) if returning is specified, or None.
        """
        # Convert DataFrame to dict-like format
        if isinstance(where, pd.DataFrame):
            if len(where) != 1:
                raise ValueError("DataFrame must contain exactly one row for upsert")
            where = dict(where.iloc[0])
        
        which_columns = list(where.keys())
        values = list(where.values())
        
        assert values and which_columns
        values_chain = [f'${num}' for num in range(1, len(values)+1)]
        update_set_query = ""
        for i, item in enumerate(zip(which_columns, values_chain)):
            if i == 0:
                continue
            update_set_query += f"{item[0]}={item[1]}, "
        update_set_query = update_set_query[:-2]  # remove last ","
        
        # Use id_fields from dependency injection
        id_fields = self.get_id_fields()
        on_conflict_values = ", ".join(id_fields) if id_fields else which_columns[0]
        
        sql = (
            f"INSERT INTO {self.name} ({', '.join(which_columns)}) \n"
            f"VALUES ({', '.join(values_chain)}) \n"
            f"ON CONFLICT ({on_conflict_values}) DO UPDATE \n"
            f"SET {update_set_query} \n"
        )
        if returning:
            sql += f"RETURNING {returning} \n"
        self._create_sql_log_message(sql, values)
        return_values = await self.db.execute(sql, *values)
        return return_values   

    @with_log()
    @formatter
    async def update(
        self, 
        set: Dict[str, Any], 
        where: Dict[str, Any] | pd.DataFrame,
        returning: str = "*"
    ) -> Optional[asyncpg.Record]:
        """Update existing records in the table.
        
        Args:
            set: Dictionary mapping column names to new values.
            where: Dictionary mapping column names to filter values, or DataFrame
                with columns as field names and rows as filter criteria.
                Only matching records will be updated.
            returning: Columns to return from updated rows. Defaults to '*'.
        
        Returns:
            The updated record(s) or None if operation fails.
        """
        # Convert DataFrame to dict if needed
        if isinstance(where, pd.DataFrame):
            if len(where) != 1:
                raise ValueError("DataFrame must contain exactly one row for update")
            where = dict(where.iloc[0])
        num_gen = (num for num in range(1,100))
        update_set_query = ", ".join([f'{col_name}=${i}' for i, col_name in zip(num_gen, set.keys())])
        next_ = next(num_gen) -1  # otherwise it would be one to high - python bug?
        sql = (
            f"UPDATE {self.name} \n"
            f"SET {update_set_query} \n"
            f"WHERE {self.__class__.create_where_statement([*where.keys()], dollar_start=next_)}\n"
        )
        if returning:
            sql += f"RETURNING {returning} \n"
        values = [*set.values(), *where.values()]
        self._create_sql_log_message(sql, values)
        return_values = await self.db.execute(sql, *values)
        return return_values   

    @with_log()
    @formatter
    async def delete(
        self, 
        where: Dict[str, Any] | pd.DataFrame,
    ) -> Optional[List[Dict[str, Any]]]:
        """Delete records from the table and return them.
        
        Args:
            where: Dictionary mapping column names to filter values, or DataFrame
                with columns as field names and rows as filter criteria.
        
        Returns:
            List of deleted records as dictionaries, or None if operation fails.
        """
        # Convert DataFrame to dict if needed
        if isinstance(where, pd.DataFrame):
            if len(where) != 1:
                raise ValueError("DataFrame must contain exactly one row for delete")
            where = dict(where.iloc[0])
        
        columns = list(where.keys())
        matching_values = list(where.values())
        where_stmt = self.__class__.create_where_statement(columns)

        sql = (
            f"DELETE FROM {self.name}\n"
            f"WHERE {where_stmt}\n"
            f"RETURNING *"
        )
        self._create_sql_log_message(sql, matching_values)

        records = await self.db.fetch(sql, *matching_values)
        return records

    @with_log()
    @formatter
    async def alter(self):
        pass

    @with_log()
    @formatter
    async def select(
        self, 
        where: Dict[str, Any] | pd.DataFrame,
        order_by: Optional[str] = None, 
        select: str = "*",
        additional_values: Optional[List] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Select records from the table with filtering and ordering.
        
        Args:
            where: Dictionary mapping column names to filter values, or DataFrame
                with columns as field names and rows as filter criteria.
            order_by: ORDER BY clause (e.g., 'created_at DESC').
            select: Columns to select. Defaults to '*'.
            additional_values: Additional parameterized values to append.
        
        Returns:
            List of selected records as dictionaries, or None if no records found.
        """
        # Convert DataFrame to dict if needed
        if isinstance(where, pd.DataFrame):
            if len(where) != 1:
                raise ValueError("DataFrame must contain exactly one row for select")
            where = dict(where.iloc[0])
        
        columns = list(where.keys())
        matching_values = list(where.values())
        where_stmt = self.__class__.create_where_statement(columns)
        sql = (
            f"SELECT {select} FROM {self.name}\n"
            f"WHERE {where_stmt}"
        )
        if order_by:
            sql += f"\nORDER BY {order_by}"
        if additional_values:
            matching_values.extend(additional_values)
        self._create_sql_log_message(sql, matching_values)

        records = await self.db.fetch(sql, *matching_values)
        return records

    async def select_row(
        self, 
        where: Dict[str, Any] | pd.DataFrame,
        select: str = "*"
    ) -> Optional[asyncpg.Record]:
        """Select a single row from the table.
        
        Convenience method that calls select() and returns only the first record.
        
        Args:
            where: Dictionary mapping column names to filter values, or DataFrame
                with columns as field names.
            select: Columns to select. Defaults to '*'.
        
        Returns:
            Single record as asyncpg.Record, or None if no record found.
        
        Example:
            >>> row = await table.select_row(where={'id': 1})
        """
        records = await self.select(where, select=select)
        if not records:
            return None
        return records[0]

    async def delete_by_id(self, *id_values: Any) -> Optional[Dict]:
        """Delete a single record by its identifier.
        
        Args:
            *id_values: Values for each id field in order.
        
        Returns:
            The deleted record as a dictionary, or None if not found.
        """
        if not self.id_fields:
            raise ValueError("Table has no id_fields configured")
        if len(id_values) != len(self.id_fields):
            raise ValueError(f"Expected {len(self.id_fields)} id values, got {len(id_values)}")
        
        where = dict(zip(self.id_fields, id_values))
        return await self.delete(where=where)

    async def fetch_by_id(self, *id_values: Any) -> Optional[Dict]:
        """Fetch a single record by its identifier.
        
        Args:
            *id_values: Values for each id field in order.
        
        Returns:
            The record as a dictionary, or None if not found.
        """
        if not self.id_fields:
            raise ValueError("Table has no id_fields configured")
        if len(id_values) != len(self.id_fields):
            raise ValueError(f"Expected {len(self.id_fields)} id values, got {len(id_values)}")
        
        where = dict(zip(self.id_fields, id_values))
        rec = await self.select(where=where)
        if not rec:
            return None
        return rec[0]

    @with_log()
    @formatter
    async def fetch(self, sql: str, *args) -> Optional[List[asyncpg.Record]]:
        """Execute a custom SQL query and return results.
        
        Args:
            sql: SQL query with $1, $2, ... placeholders.
            *args: Values to substitute for placeholders.
        
        Returns:
            Query results as list of asyncpg Records, or None if query fails.
        """
        self._create_sql_log_message(sql, [*args])
        return await self.db.fetch(sql, *args)

    @staticmethod
    def create_where_statement(columns: List[str], dollar_start: int = 1) -> str:
        where = ""
        for i, item in zip(range(dollar_start, dollar_start+len(columns)+1),columns):
            where += f"{'AND ' if i > 0 else ''}{item}=${i} "
        return where[4:]  # cut first and
    
    def _create_sql_log_message(self, sql:str, values: List):
        self._executed_sql = (
            f"SQL:\n"
            f"{sql}\n"
            f"WITH VALUES: {values}"
        )

    async def execute(self, sql: str, *args) -> Optional[List[asyncpg.Record]]:
        """Execute a custom SQL query.
        
        Alias for fetch() method.
        
        Args:
            sql: SQL query with $1, $2, ... placeholders.
            *args: Values to substitute for placeholders.
        
        Returns:
            Query results, or None if query fails.
        """
        return await self.fetch(sql, *args)

def setup_table_logging(logging_provider: loggingProvider):
    global log
    log = logging_provider(__name__, "decorator")
