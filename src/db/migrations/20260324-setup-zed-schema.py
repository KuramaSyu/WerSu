from pathlib import Path

from authzed.api.v1 import WriteSchemaRequest

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Write the authorization schema into SpiceDB from ``schema.zed``."""

    async def up(self, ctx: MigrationContext) -> None:
        """
        Raises
        ------
        ValueError
            If SpiceDB client dependency is missing.
        """
        if ctx.spicedb_client is None:
            raise ValueError("MigrationContext.spicedb_client is required for SpiceDB schema migration")

        schema_text = Path(__file__).with_name("schema.zed").read_text()
        await ctx.spicedb_client.WriteSchema(WriteSchemaRequest(schema=schema_text))
