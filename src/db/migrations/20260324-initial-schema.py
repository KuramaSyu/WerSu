from pathlib import Path

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Initialize base database schema objects."""

    async def up(self, ctx: MigrationContext) -> None:
        """Apply initial schema SQL from a sibling file.

        Parameters
        ----------
        ctx : MigrationContext
            Migration dependency container.
        """
        migration_sql = Path(__file__).with_name("initial-schema.sql").read_text()
        await ctx.db.execute(migration_sql)
