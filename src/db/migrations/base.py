from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.api.types import LoggingProvider
from src.db.migrations.context import MigrationContext


class MigrationABC(ABC):
    """Abstract base class for all schema migrations.

    Parameters
    ----------
    migration_path : Path
        Filesystem path to the migration file.
    log_provider : LoggingProvider
        Callable that provides a logger instance.
    """

    def __init__(self, migration_path: Path, log_provider: LoggingProvider):
        """Initialize migration context.

        Parameters
        ----------
        migration_path : Path
            Filesystem path to the migration file.
        log_provider : LoggingProvider
            Callable that provides a logger instance.
        """
        self._migration_path = migration_path
        self._log = log_provider(__name__, self)
        #self._log.info(self.make_log_entry("initialized"))

    def name(self) -> str:
        """Return migration identifier derived from the filename.

        Returns
        -------
        str
            Migration name without file extension.
        """
        return self._migration_path.stem

    async def run(self, ctx: MigrationContext) -> None:
        """Run the migration using a template flow.

        Parameters
        ----------
        ctx : MigrationContext
            Dependency container passed into ``up()``.

        Notes
        -----
        This method emits log messages before and after execution and delegates
        the actual schema changes to ``up()``.
        """
        self._log.info(self.make_log_entry("running"))
        await self.up(ctx)
        self._log.info(self.make_log_entry("completed"))

    def make_log_entry(self, action: str) -> str:
        """Build a consistent log message for migration lifecycle events.

        Parameters
        ----------
        action : str
            Lifecycle action, for example ``initialized`` or ``running``.

        Returns
        -------
        str
            Formatted log message.
        """
        return f"Migration {self.name()} {action}"

    @abstractmethod
    async def up(self, ctx: MigrationContext) -> None:
        """Apply schema changes for this migration.

        Parameters
        ----------
        ctx : MigrationContext
            Dependency container for migration execution.
        """
        raise NotImplementedError
