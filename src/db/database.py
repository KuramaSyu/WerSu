from abc import ABC, abstractmethod
import functools
from typing import Awaitable, Callable, Coroutine, Dict, Optional, List, Any
import asyncpg
from asyncpg import Pool, Connection, Record

from utils.singleton import SingletonMeta

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
    def __init__(self, dsn: str):
        self._pool: Optional[Pool] = None
        self._dsn: str = dsn
        self._instance = self
    
    async def init_db(self):
        self._pool = await asyncpg.create_pool(dsn=self._dsn)
        
        content = ""
        with open("init.sql") as f:
            content = f.read()
        await self._pool.execute(content)

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
        return await _cxn.execute(query, *args)

    
    @acquire
    async def fetch(self, query: str, *args: Any, _cxn: Connection) -> List[Record]:
        """use when making selections.

        Returns:
        --------
        List[Record]:
            the records from the selection/return
        """
        return await _cxn.fetch(query, *args)

    @acquire
    async def fetchrow(self, query: str, *args: Any, _cxn: Connection) -> Optional[Record]:
        """use when making selections that return a single row.

        Returns:
        --------
        Optional[Record]:
            the record from the selection/return or None
        """
        return await _cxn.fetchrow(query, *args)