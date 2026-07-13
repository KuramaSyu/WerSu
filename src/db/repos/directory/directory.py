"""Facade :class:`DirectoryRepo` composing the Postgres repo + the permission service.

This module replaces the old monolithic
:class:`DirectoryRepoFacade`.  The persistence machinery
lives in :class:`src.db.repos.directory.postgres.PostgresDirectoryRepo`
and the permission / relation logic lives in
:class:`src.api.permission_repo.PermissionRepoABC`.  The facade here
composes them so existing consumers
(:class:`~src.services.directory.DirectoryService` and friends) can
keep depending on the :class:`src.api.directory_repo.DirectoryRepo`
ABC without rewiring.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional, Tuple

from src.api.directory_facade import DirectoryFacade
from src.api.directory_service import (
    DirectoryIncludeOptions,
    resolve_directory_include_options,
)
from src.api.permission_repo import PermissionRepoABC
from src.api.directory_repo import DirectoryRepoABC
from src.api.relationship import (
    DirectoryRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.types import LoggingProvider
from src.api.undefined import UNDEFINED, is_undefined, unwrap_undefined
from src.api.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.domain.permission_chain import HasDirectoryViewPerm, PermissionCheckChain, PermissionCheckChainStart
from src.utils import convert_entity_for_db


class DirectoryRepoFacade(DirectoryFacade):
    """Compose a :class:`PostgresDirectoryRepoABC` with the permission repo.

    The facade routes every :class:`DirectoryRepo` call to either the
    low-level Postgres repo (for storage) or the permission repo
    (for visibility checks and user-flavoured relation writes).
    """

    def __init__(
        self,
        postgres_repo: DirectoryRepoABC,
        permission_repo: PermissionRepoABC,
        log: LoggingProvider,
    ) -> None:
        self._postgres = postgres_repo
        self._permission_repo = permission_repo
        self._log = log(self)

    # ---- public contract ---------------------------------------------

    async def create_directory(self, entity: DirectoryEntity, user_ctx: UserContextABC) -> DirectoryEntity:
        """Insert a row and mirror the entity's relations + parent pointers."""
        entity_data = convert_entity_for_db(entity)

        # Insert the directory row.
        assert entity_data.slug
        created_entity = await self._postgres.insert_directory(
            slug=entity_data.slug,
            display_name=entity_data.display_name,
            description=entity_data.description,
            image_url=entity_data.image_url,
            readme_note_id=entity_data.readme_note_id,
        )
        dir_id = unwrap_undefined(created_entity.id)

        parent_ids = entity.parent_directory_ids
        if parent_ids:
            await self._replace_parents(dir_id, list(parent_ids))

        # add user relation; other relations are are kept as they are.
        admin_relation = await self._create_user_admin_relation(dir_id, user_ctx)
        created_entity.relations = created_entity.relations or []
        if created_entity.relations:
            self._log.warning(
                f"Unwanted behaviour: create_directory() was called with non-empty relations: {created_entity.relations}. Only the admin relation will be persisted."
            )
        created_entity.relations.append(admin_relation)

        # If the entity carried tags, persist them now.  An empty
        # list is treated as "clear every tag" -- the same semantics
        # the update path already followed.
        if entity.tag_ids:
            await self._postgres.replace_directory_tags(
                str(dir_id), list(entity.tag_ids)
            )

        return created_entity

    async def fetch_directory(
        self,
        id: str,
        *,
        include: Optional[DirectoryIncludeOptions] = None,
    ) -> Optional[DirectoryEntity]:
        """Load a directory + its relations + optionally hydrated fields.

        Args:
            id: directory id to load.
            include: opt-in enrichment flags; see
                :class:`~src.api.directory_service.DirectoryIncludeOptions`.
                When ``None`` (or every flag ``False``) only the row
                + SpiceDB relations are returned.

        Returns:
            Optional[DirectoryEntity]: the directory, or ``None``
            when no row matches ``id``.
        """
        resolved = resolve_directory_include_options(include)
        entity = await self._postgres.fetch_directory(
            str(id), include=resolved
        )
        if not entity:
            return None
        # deprecated
        # await self._hydrate_relations(
        #     entity,
        #     populate_parents=bool(resolved.get("include_parents")),
        # )
        return entity

    async def add_note_to_directory(
        self,
        note_id: str,
        directory_id: str,
    ) -> None:
        """Bind ``note_id`` as a direct child of ``directory_id``.

        Mirrors the bind on both sides of the contract: writes the
        Postgres hierarchy row and the SpiceDB ``parent_directory``
        relation so visibility checks against the directory pick up
        the new note.
        """
        self._assert_note_to_directory_ids(note_id, directory_id)
        await self._postgres.bind_note(str(directory_id), str(note_id))
        await self._permission_repo.insert(
            [
                Relationship(
                    resource=ObjectRef(
                        object_type=ObjectTypeEnum.NOTE,
                        object_id=str(note_id),
                    ),
                    relation="parent_directory",
                    subject=SubjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY,
                        object_id=str(directory_id),
                    ),
                )
            ]
        )

    async def remove_note_from_directory(
        self,
        note_id: str,
        directory_id: str,
    ) -> None:
        """Unbind ``note_id`` from the direct child of ``directory_id``.

        Drops both the Postgres hierarchy row and the SpiceDB
        ``parent_directory`` relation so visibility checks no
        longer surface the note under this directory.
        """
        self._assert_note_to_directory_ids(note_id, directory_id)
        await self._postgres.unbind_note(str(directory_id), str(note_id))
        await self._permission_repo.delete(
            Relationship(
                resource=ObjectRef(
                    object_type=ObjectTypeEnum.NOTE,
                    object_id=str(note_id),
                ),
                relation="parent_directory",
                subject=SubjectRef(
                    object_type=ObjectTypeEnum.DIRECTORY,
                    object_id=str(directory_id),
                ),
            )
        )

    async def update_directory(
        self,
        entity: DirectoryEntity,
    ) -> Optional[DirectoryEntity]:
        """Partially update a directory.

        Honours the UNDEFINED / None / value semantics
        :meth:`PostgresDirectoryRepoABC.update_directory` provides
        for scalar columns.  When ``entity.parent_directory_ids`` is
        set the entire parent set is replaced (empty list clears).
        """
        if not entity.id:
            raise ValueError("Directory ID is required for update")

        updated_entity = await self._postgres.update_directory(
            str(entity.id),
            slug=entity.slug or UNDEFINED,
            display_name=entity.display_name,
            description=entity.description,
            image_url=entity.image_url,
            readme_note_id=entity.readme_note_id,
        )
        if not updated_entity:
            return None

        if entity.parent_directory_ids is not UNDEFINED:
            await self._replace_parents(
                str(entity.id), list(entity.parent_directory_ids)
            )

        if entity.tag_ids:
            await self._postgres.replace_directory_tags(
                str(entity.id), list(entity.tag_ids)
            )

        return await self.fetch_directory(str(entity.id))

    async def fetch_directories(
        self, user: UserContextABC
    ) -> List[DirectoryEntity]:
        """Return every directory visible to ``user`` (direct tuples)."""
        # here we need a permission repo call to enforce permissions
        directory_ids = await self.list_user_directory_ids(user)
        return await self._fetch_and_hydrate(directory_ids)

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        """Return every directory id the user has view access to (direct tuples)."""
        # this is more or less a permission check as well as the source of truth for the directory hierarchy
        return await self._permission_repo.lookup(
            Relationship(
                resource=ObjectRef(
                    object_type=ObjectTypeEnum.DIRECTORY, object_id=UNDEFINED
                ),
                relation=DirectoryRelationEnum.VIEW,
                subject=SubjectRef(
                    object_type=ObjectTypeEnum.USER, object_id=user.user_id
                ),
            )
        )

    async def list_note_directory_ids(self, note_id: str) -> List[str]:
        """Return the directory ids that directly parent ``note_id``.

        Implementation goes through the permission repo (it's a
        relationship lookup, not a Postgres row) since
        ``note#parent_directory@directory`` is a SpiceDB-anchored
        relation that lives next to the hierarchy pointer.
        """
        return sorted(
            await self._permission_repo.lookup(
                Relationship(
                    resource=ObjectRef(ObjectTypeEnum.NOTE, str(note_id)),
                    relation="parent_directory",
                    subject=SubjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY, object_id=UNDEFINED
                    ),
                )
            )
        )

    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        """Delete the directory row (cleanup is the caller's job)."""
        if not entity.id:
            raise ValueError("Directory ID is required for deletion")
        return await self._postgres.delete_directory(str(entity.id))

    async def resolve_files_of_directory(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
    ) -> List[str]:
        """Return note ids reachable from directory_id for actor."""
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")
        if not directory_id:
            # all dirs the user can view - more expensive through SpiceDB wildcard call
            start_directories = await self.list_user_directory_ids(actor)
        else:
            start_directories = [str(directory_id)]

            # check view for dir
            view_chain: PermissionCheckChain = HasDirectoryViewPerm(directory_id=str(directory_id)).set_permission_repo(self._permission_repo)
            can_view = await view_chain.check(actor)
            if can_view.error:
                raise can_view.error

        note_ids: set[str] = set()
        for start in start_directories:
            note_ids.update(
                await self._postgres.get_children(
                    start, "notes", descendants=True, max_depth=max_depth
                )
            )
        return sorted(note_ids)

    async def resolve_subtree(
        self,
        directory_id: str,
        max_depth: int = 10,
    ) -> Tuple[List[str], List[str]]:
        """Walk the hierarchy table and return ``(note_ids, directory_ids)``."""
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")
        notes = await self._postgres.get_children(
            directory_id, "notes", descendants=True, max_depth=max_depth
        )
        directories = await self._postgres.get_children(
            directory_id, "directories", descendants=True, max_depth=max_depth
        )
        return notes, directories

    # ---- counts ------------------------------------------------------

    # NOTE: count helpers are no longer abstract on the ABC --
    # ``fetch_directory(include_counts=True)`` is the single
    # canonical fast-path.  In-memory fakes populate the counts
    # in ``_hydrate_relations`` directly when the kwarg is set.

    # ---- internal helpers --------------------------------------------

    async def _fetch_and_hydrate(self, ids: List[str]) -> List[DirectoryEntity]:
        """Fetch a batch of directories and hydrate relations + parent + counts."""
        if not ids:
            return []
        entities = await self._postgres.fetch_directories_by_ids(ids)
        if not entities:
            return []

        # Hydrate parents in parallel.
        async def _hydrate(entity: DirectoryEntity) -> DirectoryEntity:
            await self._hydrate_parents(entity, populate_parents=True)
            return entity

        hydrated = await asyncio.gather(*(_hydrate(e) for e in entities))
        return list(hydrated)

    async def _hydrate_parents(
        self,
        entity: DirectoryEntity,
        *,
        populate_parents: bool = False,
    ) -> None:
        """Hydrate `parent_directory_ids` + ~~`relations`~~ in place.

        Args:
            populate_parents: whether or not to fetch the parent directory ids from Postgres
        """
        if not (directory_id := entity.id):
            return
        if populate_parents:
            entity.parent_directory_ids = (
                await self._postgres.parent_directory_ids_of(directory_id)
            )
        # deprecated
        # entity.relations = await self._fetch_user_relations_for_directory(
        #     directory_id
        # )

    async def _fetch_user_relations_for_directory(
        self,
        directory_id: str,
    ) -> List[Relationship]:
        """Return every user-flavoured relation on this directory."""
        matched: List[Relationship] = await self._permission_repo.lookup_relationships(
            Relationship(
                resource=ObjectRef(
                    object_type=ObjectTypeEnum.DIRECTORY,
                    object_id=directory_id,
                ),
                relation=DirectoryRelationEnum.VIEW,
                subject=SubjectRef(
                    object_type=ObjectTypeEnum.USER, object_id=UNDEFINED
                ),
            )
        )
        return matched

    async def _replace_parents(
        self,
        directory_id: str,
        new_parent_ids: List[str],
    ) -> None:
        """Replace the full parent set for ``directory_id``.

        Reads the existing parents, drops the SpiceDB ``parent``
        relations that go away, keeps / inserts the ones that stay,
        then rewrites the Postgres hierarchy rows in a single call.
        Empty ``new_parent_ids`` clears the directory of every parent.
        """
        existing = set(
            await self._postgres.parent_directory_ids_of(directory_id)
        )
        desired = {str(p) for p in new_parent_ids if p}

        # Drop SpiceDB relations for parents that are no longer wanted.
        for removed in existing - desired:
            await self._permission_repo.delete(
                Relationship(
                    resource=ObjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY,
                        object_id=directory_id,
                    ),
                    relation=DirectoryRelationEnum.PARENT,
                    subject=SubjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY,
                        object_id=removed,
                    ),
                )
            )

        # Insert SpiceDB relations for any new parents.
        for added in desired - existing:
            await self._permission_repo.insert(
                [
                    Relationship(
                        resource=ObjectRef(
                            object_type=ObjectTypeEnum.DIRECTORY,
                            object_id=directory_id,
                        ),
                        relation=DirectoryRelationEnum.PARENT,
                        subject=SubjectRef(
                            object_type=ObjectTypeEnum.DIRECTORY,
                            object_id=added,
                        ),
                    )
                ]
            )

        # Mirror the bind in the Postgres hierarchy table.
        await self._postgres.set_parent_directories(
            directory_id, sorted(desired)
        )

    async def _create_user_admin_relation(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ) -> Relationship:
        """Insert the user-supplied ``relations`` against this directory."""

        admin_relation = Relationship(
            resource=ObjectRef(
                object_type=ObjectTypeEnum.DIRECTORY, object_id=directory_id
            ),
            relation=DirectoryRelationEnum.ADMIN,
            subject=SubjectRef(
                object_type=ObjectTypeEnum.USER, object_id=user_ctx.user_id
            ),
        )
        await self._permission_repo.insert([admin_relation])
        return admin_relation  # speed tradeoff to not call the permission repo a second time

    @staticmethod
    def _assert_note_to_directory_ids(
        note_id: object,
        directory_id: object,
    ) -> None:
        """Reject :obj:`~src.api.undefined.UNDEFINED` or ``None`` ids.

        Shared by :meth:`add_note_to_directory` and
        :meth:`remove_note_from_directory` so the validation matches
        the contract on :class:`DirectoryFacade`.
        """
        if note_id is None or is_undefined(note_id):  # type: ignore[arg-type]
            raise ValueError("note_id is required")
        if directory_id is None or is_undefined(directory_id):  # type: ignore[arg-type]
            raise ValueError("directory_id is required")


__all__ = ["DirectoryRepoFacade"]
