from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Set

from src.api.types import LoggingProvider
from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


MIGRATION_FILENAME_PATTERN = re.compile(
    r"^(?:\d{8}|\d{4}-\d{2}-\d{2})-[a-z0-9][a-z0-9-]*\.py$"
)


class MigrationRunner:
    """Discover and execute pending schema migrations.

    Parameters
    ----------
    ctx : MigrationContext
        Dependency container with runtime services used by migrations.
    log_provider : LoggingProvider
        Callable that returns a logger instance for structured logging.
    migrations_dir : Path | None, optional
        Directory that contains migration files. When ``None``, the directory
        of this module is used.
    """

    def __init__(
        self,
        ctx: MigrationContext,
        log_provider: LoggingProvider,
        migrations_dir: Path | None = None,
    ):
        """Initialize a migration runner.

        Parameters
        ----------
        ctx : MigrationContext
            Runtime dependency container for migrations.
        log_provider : LoggingProvider
            Logger factory used for runtime logging.
        migrations_dir : Path | None, optional
            Migration directory override.
        """
        self._ctx = ctx
        self._log_provider = log_provider
        self._log = log_provider(__name__, self)
        self._migrations_dir = migrations_dir or Path(__file__).parent

    async def run_pending_migrations(self) -> None:
        """Run all migrations that are not yet recorded as applied.

        Notes
        -----
        This method ensures the migration bookkeeping table exists, discovers
        valid migration files, and runs each pending migration in filename
        order.
        """
        await self._ensure_migrations_table()

        migration_files = self._discover_migration_files()
        if not migration_files:
            self._log.info("No migration files found")
            return

        applied_migrations = await self._get_applied_migrations()
        
        # log all applied migrations
        if applied_migrations:
            for migration in applied_migrations:
                self._log.info(f"Already applied migration: {migration}")

        for migration_path in migration_files:
            module = self._load_module(migration_path)
            migration = self._read_migration(module, migration_path)
            migration_name = migration.name()
            if migration_name in applied_migrations:
                continue

            # self._log.info(f"Running migration {migration_name}")
            await migration.run(self._ctx)

            await self._ctx.db.execute(
                """
                INSERT INTO public.schema_migrations (migration_name)
                VALUES ($1)
                """,
                migration_name,
            )
            self._log.info(f"Migration {migration_name} applied")

    async def _ensure_migrations_table(self) -> None:
        """Create the migration bookkeeping table if it does not exist."""
        await self._ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS public.schema_migrations (
                migration_name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    def _discover_migration_files(self) -> list[Path]:
        """Return migration files matching the naming convention.

        Returns
        -------
        list[Path]
            Sorted list of migration file paths.
        """
        files: list[Path] = []
        ignored_files: list[Path] = []
        for path in self._migrations_dir.iterdir():
            if not path.is_file() or path.suffix != ".py":
                continue

            if not MIGRATION_FILENAME_PATTERN.match(path.name):
                ignored_files.append(path)
                continue

            files.append(path)
        if ignored_files:
            self._log.debug(
                f"Ignoring {len(ignored_files)} files that do not match migration filename pattern: "
                + ", ".join(str(f.name) for f in ignored_files)
            )
        files.sort(key=lambda x: x.name)
        return files

    async def _get_applied_migrations(self) -> Set[str]:
        """Fetch all applied migration names from the database.

        Returns
        -------
        Set[str]
            Set of migration names already applied.
        """
        rows = await self._ctx.db.fetch(
            """
            SELECT migration_name
            FROM public.schema_migrations
            """
        )
        return {str(row["migration_name"]) for row in rows}

    def _load_module(self, migration_path: Path) -> ModuleType:
        """Load a migration module from a file path.

        Parameters
        ----------
        migration_path : Path
            Filesystem path of the migration Python file.

        Returns
        -------
        ModuleType
            Imported module object.

        Raises
        ------
        RuntimeError
            If the module spec or loader cannot be created.
        """
        module_name = f"migration_{migration_path.stem.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, migration_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load migration module from {migration_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _read_migration(self, module: ModuleType, migration_path: Path) -> MigrationABC:
        """Extract and validate a migration instance from a module.

        Parameters
        ----------
        module : ModuleType
            Loaded migration module.
        migration_path : Path
            Source migration path used for error context.

        Returns
        -------
        MigrationABC
            Instantiated migration object.

        Raises
        ------
        ValueError
            If the module does not define a valid ``Migration`` class.
        """
        migration_cls = getattr(module, "Migration", None)
        if migration_cls is None or not isinstance(migration_cls, type):
            raise ValueError(
                f"Migration {migration_path.name} must define a 'Migration' class"
            )

        if not issubclass(migration_cls, MigrationABC):
            raise ValueError(
                f"Migration {migration_path.name} class 'Migration' must inherit from MigrationABC"
            )

        return migration_cls(
            migration_path=migration_path,
            log_provider=self._log_provider,
        )
