from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Track the README note id on each directory.

    Notes
    -----
    The :class:`~src.services.directory.DirectoryServiceImpl` already
    pins a ``README.md`` note per directory via the
    ``note#parent_directory@directory`` SpiceDB relation.  This
    migration adds a Postgres pointer so that ``get_directory`` and
    ``get_directories`` can fetch the README in O(1) without a
    SpiceDB lookup, and so that note insert/delete hooks have a
    concrete place to bind / unbind the binding.

    The foreign key uses ``ON DELETE SET NULL`` so deleting a README
    note also clears the directory's pointer without an extra round
    trip.
    """

    async def up(self, ctx: MigrationContext) -> None:
        """Add the `readme_note_id` column to `note.directory`."""
        await ctx.db.execute(
            """
            ALTER TABLE note.directory
            ADD COLUMN IF NOT EXISTS readme_note_id TEXT NULL
                REFERENCES note.content(id)
                ON DELETE SET NULL
                ON UPDATE CASCADE
            """
        )
