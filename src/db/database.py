from abc import ABC, abstractmethod
import functools
from typing import Awaitable, Callable, Coroutine, Dict, Optional, List, Any
import asyncpg
from asyncpg import Pool, Connection, Record

from src.api.types import LoggingProvider
from src.api.undefined import UNDEFINED
from src.utils.singleton import SingletonMeta


def strip_args(*args: Any) -> List[Any]:
    """strips strings or numbers to max 100 chars/values for logging purposes"""
    stripped = []
    for arg in args:
        if isinstance(arg, str):
            if len(arg) > 100:
                stripped.append(f"{arg[:100]}... (len={len(arg)})")
            else:
                stripped.append(arg)
        elif isinstance(arg, (int, float, complex)):
            stripped.append(arg)
        else:
            stripped.append(repr(arg))
    return stripped


def _assert_no_undefined(value: Any, path: str = "arg") -> None:
    """Raise when UNDEFINED is present in query arguments."""
    if value is UNDEFINED:
        raise ValueError(
            f"UNDEFINED is not a valid database parameter at {path}; convert to None or a concrete value first"
        )

    if isinstance(value, tuple):
        for i, inner in enumerate(value):
            _assert_no_undefined(inner, f"{path}[{i}]")
        return

    if isinstance(value, list):
        for i, inner in enumerate(value):
            _assert_no_undefined(inner, f"{path}[{i}]")
        return

    if isinstance(value, dict):
        for key, inner in value.items():
            _assert_no_undefined(inner, f"{path}.{key}")


def validate_query_args(*args: Any) -> tuple[Any, ...]:
    """Validate query arguments before passing them to asyncpg."""
    for i, arg in enumerate(args):
        _assert_no_undefined(arg, f"arg[{i}]")
    return args

def acquire(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Wrapper for a coroutine which injects
    an aquired connection as first parameter
    """
   

    @functools.wraps(func)
    async def wrapper(self: "Database", *coro_args, **coro_kwargs) -> Any:
        if isinstance(self, str):
            coro_args = (self, *coro_args)
            self = Database.get_instance()

        # aquire pool from DatabaseConnection
        pool = self.pool

        async with pool.acquire() as connection:
            # aquired connection
            async with connection.transaction():
                # started transaction
                # -> call coroutine inside the transaction
                # and pass the connection as first arg
                return await func(
                    self,
                    *coro_args,
                    _cxn=connection,
                    **coro_kwargs
                )
    return wrapper

def copy_docs(copy_from_func: Callable):
    """decorator factory to replace the function docs with the given ones"""
    def decocator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        wrapper.__doc__ = copy_from_func.__doc__
        return wrapper
    return decocator

class DatabaseABC(ABC):
    """Abstract Base Class for Database connections."""
    @abstractmethod
    async def init_db(self):
        """Initializes the database connection pool."""
        ...

    async def close(self):
        """Shuts down the connection pool"""
        ...
        
    @property
    @abstractmethod
    def pool(self) -> asyncpg.Pool:
        """Returns the database connection pool."""
        ...
    
    @abstractmethod
    async def execute(self, query: str, *args: Any) -> str:
        """Executes an SQL command (or commands)."""
        ...
    
    @abstractmethod
    async def fetch(self, query: str, *args: Any) -> List[Dict]:
        """Fetches multiple records from the database."""
        ...
    
    @abstractmethod
    async def fetchrow(self, query: str, *args: Any) -> Optional[Dict]:
        """Fetches a single record from the database."""
        ...
    
class Database(DatabaseABC):
    _instance: Optional["Database"] = None
    def __init__(self, dsn: str, log: LoggingProvider, init_file: str = "src/init.sql"):
        self._pool: Optional[Pool] = None
        self._dsn: str = dsn
        self._instance = self
        self._log = log(__name__, self)
        self._init_file_path = init_file
    
    async def init_db(self):
        self._pool = await asyncpg.create_pool(dsn=self._dsn)
        self._log.info("Database connected")
        
        content = ""
        with open(self._init_file_path) as f:
            content = f.read()
        await self._pool.execute(content)
        self._log.info("Database initialized with init.sql")

    async def close(self):
        if self._pool:
            await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        """returns the DB connection pool

        Raises
        -------
        AssertionError:
            when the DB was not initialized with init_db()
        """
        assert self._pool
        return self._pool

    @classmethod
    def get_instance(cls) -> "Database":
        assert cls._instance
        return cls._instance

    @acquire
    async def execute(self, query: str, *args: Any, _cxn: Connection) -> str:
        """
        Execute an SQL command (or commands).

        This method can execute many SQL commands at once, when no arguments are provided.

        Example:
        ```
        >>> await execute('''
        ...     CREATE TABLE mytab (a int);
        ...     INSERT INTO mytab (a) VALUES (100), (200), (300);
        ... ''')
        INSERT 0 3
        ```

        ```
        >>> await execute('''
        ...     INSERT INTO mytab (a) VALUES ($1), ($2)
        ... ''', 10, 20)
        INSERT 0 2
        ```

        args: Query arguments.
        """
        validated_args = validate_query_args(*args)
        self._log.debug(f"{query} ;; {strip_args(*validated_args)}")
        return await _cxn.execute(query, *validated_args)

    
    @acquire
    async def fetch(self, query: str, *args: Any, _cxn: Connection) -> List[Record]:
        """use when making selections.

        Returns:
        --------
        List[Record]:
            the records from the selection/return
        """
        validated_args = validate_query_args(*args)
        self._log.debug(f"{query} ;; {strip_args(*validated_args)}")
        return await _cxn.fetch(query, *validated_args)

    @acquire
    async def fetchrow(self, query: str, *args: Any, _cxn: Connection) -> Optional[Record]:
        """use when making selections that return a single row.

        Returns:
        --------
        Optional[Record]:
            the record from the selection/return or None
        """
        validated_args = validate_query_args(*args)
        self._log.debug(f"{query} ;; {strip_args(*validated_args)}")
        return await _cxn.fetchrow(query, *validated_args)