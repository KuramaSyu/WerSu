from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Track the parent/child graph between directories and notes.

    Notes
    -----
    The directory tree is split across two single-purpose tables so
    that every row unambiguously describes one relationship:

    * ``note.directory_subdirectory`` -- parent directory ->
      child directory (the directory tree itself).
    * ``note.directory_note`` -- directory -> note (the notes
      contained in a directory).

    :class:`src.services.directory.DirectoryServiceImpl` reads both
    tables to compute immediate-child counts (``subdirectories``
    and ``pages``) without hitting SpiceDB on every read.

    Each row binds a parent directory to exactly one child of the
    matching kind, so the previous XOR constraint is no longer
    needed.  Cascades mirror the parent rows so deleting a parent
    directory (or a child note/directory) cleans up its hierarchy
    pointer in a single statement.
    """

    async def up(self, ctx: MigrationContext) -> None:
        """Create the two directory-hierarchy tables."""
        # Parent directory -> child directory.  This is the
        # directory tree itself: a directory may have many parents
        # (the schema lets ``directory_id`` repeat) and many
        # children (``child_directory_id`` repeats).
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS note.directory_subdirectory (
                id BIGSERIAL PRIMARY KEY,
                directory_id TEXT NOT NULL
                    REFERENCES note.directory(id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE,
                child_directory_id TEXT NOT NULL
                    REFERENCES note.directory(id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE
            )
            """
        )

        # Two indexes: one for "what children does this parent
        # have?" (the common count / fetch path) and one for the
        # reverse walk used by ``parent_directory_ids_of`` /
        # ``_fetch_directory_with_parents``.
        await ctx.db.execute(
            """
            CREATE INDEX IF NOT EXISTS directory_subdirectory_directory_idx
                ON note.directory_subdirectory (directory_id)
            """
        )
        await ctx.db.execute(
            """
            CREATE INDEX IF NOT EXISTS directory_subdirectory_child_directory_idx
                ON note.directory_subdirectory (child_directory_id)
            """
        )

        # Directory (n) <-> (m) note
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS note.directory_note (
                id BIGSERIAL PRIMARY KEY,
                directory_id TEXT NOT NULL
                    REFERENCES note.directory(id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE,
                note_id TEXT NOT NULL
                    REFERENCES note.content(id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE
            )
            """
        )

        # Two indexes mirroring the directory-side ones: one for
        # the "list notes in this directory" path, one for the
        # reverse walk used by ``CombinedNotePostgresRepo`` to
        # populate ``note.directory_ids``.
        await ctx.db.execute(
            """
            CREATE INDEX IF NOT EXISTS directory_note_directory_idx
                ON note.directory_note (directory_id)
            """
        )
        await ctx.db.execute(
            """
            CREATE INDEX IF NOT EXISTS directory_note_note_idx
                ON note.directory_note (note_id)
            """
        )
