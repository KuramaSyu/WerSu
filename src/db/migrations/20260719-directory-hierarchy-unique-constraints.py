from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Deduplicate 
    + enforce uniqueness on the directory hierarchy tables
    + prevent self-cycles
    """

    async def up(self, ctx: MigrationContext) -> None:
        """Deduplicate existing rows, then add the unique constraints."""
        # dedup directory_subdirectory, keeping the oldest row per pair
        await ctx.db.execute(
            """
            DELETE FROM note.directory_subdirectory AS d
            WHERE d.id NOT IN (
                SELECT MIN(id)
                FROM note.directory_subdirectory
                GROUP BY directory_id, child_directory_id
            )
            """
        )

        # dedup directory_note the same way
        await ctx.db.execute(
            """
            DELETE FROM note.directory_note AS d
            WHERE d.id NOT IN (
                SELECT MIN(id)
                FROM note.directory_note
                GROUP BY directory_id, note_id
            )
            """
        )

        # unique constraint on directory_subdirectory (parent, child)
        await ctx.db.execute(
            """
            ALTER TABLE note.directory_subdirectory
            ADD CONSTRAINT directory_subdirectory_directory_id_child_directory_id_key
            UNIQUE (directory_id, child_directory_id)
            """
        )

        # unique constraint on directory_note (directory, note)
        await ctx.db.execute(
            """
            ALTER TABLE note.directory_note
            ADD CONSTRAINT directory_note_directory_id_note_id_key
            UNIQUE (directory_id, note_id)
            """
        )

        # a directory cannot be its own parent (prevents self-cycles)
        await ctx.db.execute(
            """
            ALTER TABLE note.directory_subdirectory
            ADD CONSTRAINT directory_subdirectory_no_self_parent_check
            CHECK (directory_id <> child_directory_id)
            """
        )
