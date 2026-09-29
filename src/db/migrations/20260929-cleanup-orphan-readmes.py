
from __future__ import annotations

from typing import List

from src.api.other.relationship import (
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
)
from src.api.other.undefined import UNDEFINED
from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


_ORPHAN_README_SQL = """
SELECT c.id
FROM note.content c
WHERE c.title = 'README.md'
    AND NOT EXISTS (
        SELECT 1 FROM note.directory d
        WHERE d.readme_note_id = c.id
    )
"""


class Migration(MigrationABC):
    """Delete orphan README.md notes that no directory points at as its official README."""
    async def up(self, ctx: MigrationContext) -> None:
        permission_repo = ctx.services.permission_repo
        if permission_repo is None:
            raise ValueError(
                "MigrationContext.services.permission_repo is required to "
                "clean up orphan README notes in SpiceDB"
            )

        rows = await ctx.db.fetch(_ORPHAN_README_SQL)
        orphan_ids: List[str] = [str(r["id"]) for r in rows if r.get("id")]
        if not orphan_ids:
            return

        # Wildcards catch parent_directory + owner/admin/writer edges.
        for note_id in orphan_ids:
            await permission_repo.delete(
                Relationship(
                    resource=ObjectRef(ObjectTypeEnum.NOTE, str(note_id)),
                    relation=UNDEFINED,
                    subject=UNDEFINED,
                )
            )

        # Postgres cascade clears directory_note + embedding + versions + tags.
        await ctx.db.execute(
            "DELETE FROM note.content WHERE id = ANY($1::text[])",
            orphan_ids,
        )