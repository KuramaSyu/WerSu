"""Backfill ``directory#owner`` for existing directories.

Enforcing the new rank rules without an owner would leave existing
directories unmanageable.  The migration:

1. enumerates every directory row;
2. skips directories that already have an ``owner`` tuple (idempotent);
3. promotes the earliest ``directory_created`` activity actor when one
   exists;
4. otherwise promotes every existing direct user ``admin`` (permissive,
   additive, and preserves current access).

Role subjects holding ``directory#admin`` are left untouched; only direct
user admins are promoted in the fallback.
"""

from __future__ import annotations

from typing import Dict, List

from src.api.other.relationship import (
    DirectoryRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.other.undefined import UNDEFINED
from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Promote directory admins/creators to owners."""

    async def up(self, ctx: MigrationContext) -> None:
        permission_repo = ctx.services.permission_repo
        if permission_repo is None:
            raise ValueError(
                "MigrationContext.services.permission_repo is required "
                "for the directory owner backfill"
            )

        directory_rows = await ctx.db.fetch("SELECT id FROM note.directory")
        directory_ids = [str(row["id"]) for row in directory_rows or []]

        creator_rows = await ctx.db.fetch(
            """
            SELECT DISTINCT ON (directory_id) directory_id, actor_id
            FROM activity
            WHERE action = 'directory_created'
              AND directory_id IS NOT NULL
              AND actor_id IS NOT NULL
            ORDER BY directory_id, at ASC
            """
        )
        creator_by_directory: Dict[str, str] = {
            str(row["directory_id"]): str(row["actor_id"])
            for row in creator_rows or []
        }

        relationships = await permission_repo.list_relationships(
            ObjectRef(ObjectTypeEnum.DIRECTORY, UNDEFINED)
        )

        owners_by_directory: Dict[str, List[Relationship]] = {}
        admins_by_directory: Dict[str, List[Relationship]] = {}
        for rel in relationships:
            directory_id = str(rel.resource.object_id)
            if str(rel.relation) == str(DirectoryRelationEnum.OWNER):
                owners_by_directory.setdefault(directory_id, []).append(rel)
            elif (
                str(rel.relation) == str(DirectoryRelationEnum.ADMIN)
                and str(rel.subject.object_type) == str(ObjectTypeEnum.USER)
            ):
                admins_by_directory.setdefault(directory_id, []).append(rel)

        to_insert: List[Relationship] = []
        for directory_id in directory_ids:
            if owners_by_directory.get(directory_id):
                continue

            creator = creator_by_directory.get(directory_id)
            if creator:
                to_insert.append(
                    self._owner_relationship(directory_id, creator)
                )
                self._log.info(
                    f"Backfilled directory owner {directory_id} from activity: {creator}"
                )
                continue

            for admin in admins_by_directory.get(directory_id, []):
                to_insert.append(
                    self._owner_relationship(
                        directory_id, str(admin.subject.object_id)
                    )
                )
                self._log.info(
                    f"Backfilled directory owner {directory_id} from admin: "
                    f"{admin.subject.object_id}"
                )

        if to_insert:
            await permission_repo.insert(to_insert)
        self._log.info(
            f"Directory owner backfill complete: {len(to_insert)} owner tuples"
        )

    @staticmethod
    def _owner_relationship(directory_id: str, user_id: str) -> Relationship:
        return Relationship(
            resource=ObjectRef(ObjectTypeEnum.DIRECTORY, directory_id),
            relation=DirectoryRelationEnum.OWNER,
            subject=SubjectRef(ObjectTypeEnum.USER, user_id),
        )