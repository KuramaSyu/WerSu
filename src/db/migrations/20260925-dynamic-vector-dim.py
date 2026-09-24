"""Drop the fixed VECTOR(384) so the embedding column can hold any dim.

The PK on note.embedding is (note_id, model), so old 384-dim
rows stay in place; future generators may write other dims into
the same column. PK keeps multi-model rows from clobbering each
other until you DELETE old model rows.
"""

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):

    async def up(self, ctx: MigrationContext) -> None:
        await ctx.db.execute(
            "ALTER TABLE note.embedding "
            "ALTER COLUMN embedding TYPE VECTOR USING embedding::vector"
        )