from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Tag taxonomy + rename ``note.directory.name`` to ``slug``.

    Notes
    -----
    Adds the ``note.tag`` taxonomy and two association tables so notes
    and directories can carry many tags:

    * ``note.tag`` -- ``id`` (UUIDv7), ``slug`` (unique), ``display_name``.
    * ``note.note_tag`` -- ``note_id``, ``tag_id``.
    * ``note.directory_tag`` -- ``directory_id``, ``tag_id``.

    Association rows mirror the parent rows: deleting a note /
    directory or a tag cascades the link row away so the join tables
    cannot outlive their endpoints.

    Also renames ``note.directory.name`` to ``slug``. The new name
    matches the gRPC ``Directory.slug`` field and the entity field
    ``DirectoryEntity.slug``. Postgres carries the rename through to
    every view, constraint and index that referenced ``name``.
    """

    async def up(self, ctx: MigrationContext) -> None:
        """Apply tag taxonomy + directory slug rename."""
        # Rename first so the rest of the schema work happens on the
        # column name every downstream consumer (entity, repo, gRPC
        # visitor) already uses.
        await ctx.db.execute(
            "ALTER TABLE note.directory RENAME COLUMN name TO slug"
        )

        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS note.tag (
                id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
                slug TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL
            )
            """
        )

        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS note.note_tag (
                note_id TEXT NOT NULL
                    REFERENCES note.content(id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE,
                tag_id TEXT NOT NULL
                    REFERENCES note.tag(id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE,
                PRIMARY KEY (note_id, tag_id)
            )
            """
        )

        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS note.directory_tag (
                directory_id TEXT NOT NULL
                    REFERENCES note.directory(id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE,
                tag_id TEXT NOT NULL
                    REFERENCES note.tag(id)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE,
                PRIMARY KEY (directory_id, tag_id)
            )
            """
        )

        # Both association tables are scanned by ``tag_id`` whenever
        # the inverse lookup ("which notes / directories carry this
        # tag?") fires.  Keep the index local.
        await ctx.db.execute(
            """
            CREATE INDEX IF NOT EXISTS note_note_tag_tag_id_idx
                ON note.note_tag (tag_id)
            """
        )
        await ctx.db.execute(
            """
            CREATE INDEX IF NOT EXISTS note_directory_tag_tag_id_idx
                ON note.directory_tag (tag_id)
            """
        )
