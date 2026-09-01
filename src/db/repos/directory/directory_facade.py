"""Facade :class:`DirectoryRepo` composing the Postgres repo + the permission service.

This module replaces the old monolithic
:class:`DirectoryFacadeImpl`.  The persistence machinery
lives in :class:`src.db.repos.directory.postgres.PostgresDirectoryRepo`
and the permission / relation logic lives in
:class:`src.api.permission_repo.PermissionRepoABC`.  The facade here
composes them so existing consumers
(:class:`~src.services.directory.DirectoryServiceImpl` and friends) can
keep depending on the :class:`src.api.directory_repo.DirectoryRepo`
ABC without rewiring.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Tuple

from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.api.repos.tag_repo import TagRepoABC
from src.api.services.directory_service import (
    DirectoryIncludeOptions,
    resolve_directory_include_options,
)
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.repos.directory_repo import (
    DirectoryChildType,
    DirectoryHierarchyType,
    DirectoryParentType,
    DirectoryRepoABC,
)
from src.api.other.relationship import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, unwrap_undefined
from src.api.other.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.domain.permission_chain import HasDirectoryViewPerm, PermissionCheckChain, PermissionCheckChainStart
from src.utils import convert_entity_for_db, non_empty


class DirectoryFacadeImpl(DirectoryFacadeABC):
    """Composes :class:`DirectoryRepoABC`, :class:`PermissionRepoABC` and :class:`TagRepoABC` 
    """
    def __init__(
        self,
        directory_repo: DirectoryRepoABC,
        permission_repo: PermissionRepoABC,
        tag_repo: TagRepoABC,
        log: LoggingProvider,
        shelf_repo: ShelfRepoABC,
    ) -> None:
        self._dir_repo = directory_repo
        self._perm_repo = permission_repo
        self._tag_repo = tag_repo
        self._shelf_repo = shelf_repo
        self._log = log(self)

    # ---- public contract ---------------------------------------------

    async def create_directory(self, entity: DirectoryEntity, user_ctx: UserContextABC) -> DirectoryEntity:
        """Insert a row and mirror the entity's relations + parent pointers."""
        entity_data = convert_entity_for_db(entity)

        # Insert the directory to DB
        assert entity_data.slug
        created_entity = await self._dir_repo.insert_directory(
            slug=entity_data.slug,
            display_name=entity_data.display_name,
            description=entity_data.description,
            image_url=entity_data.image_url,
            readme_note_id=entity_data.readme_note_id,
        )

        # assert it has an id for later usage
        dir_id = unwrap_undefined(created_entity.id)

        # check is parents are given; if so - replace them
        parent_ids = entity.parent_directory_ids
        if parent_ids:
            await self._replace_parents(dir_id, list(parent_ids))
        elif entity.shelf_ids:
            # No directory parent -> bind to every supplied shelf.
            for sid in non_empty(entity.shelf_ids):
                await self._shelf_repo.add_book(
                    sid, dir_id, user_ctx=user_ctx,
                )

        # add note#admin@user relation for consistency and permission checks
        admin_relation = await self._create_user_admin_relation(dir_id, user_ctx)
        if created_entity.relations:
            self._log.warning(
                f"Unwanted behaviour: create_directory() was called with non-empty relations: {created_entity.relations}. Only the admin relation will be persisted."
            )
        created_entity.relations = created_entity.relations or []
        # created_entity.relations.append(admin_relation)

        # If the entity carried tags, persist them now.  An empty
        # list is treated as "clear every tag" -- the same semantics
        # the update path already followed.
        if entity.tag_ids:
            await self._tag_repo.replace_tags_of(
                "directory", str(dir_id), list(entity.tag_ids),
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
        entity = await self._dir_repo.fetch_directory(
            str(id), include=resolved
        )
        if not entity:
            return None
        # deprecated
        # await self._hydrate_relations(
        #     entity,
        #     populate_parents=bool(resolved.get("include_parents")),
        # )
        if resolved.get("include_shelves"):
            await self._populate_shelf_ids(entity)
        return entity

    async def _populate_shelf_ids(
        self,
        entity: DirectoryEntity,
    ) -> None:
        """Populate ``entity.shelf_ids`` from the shelf repo.

        Errors from the shelf repo are swallowed and the entity
        ends up with an empty list so the read path never fails
        just because the shelf layer had a hiccup.
        """
        if entity.id is None or entity.id is UNDEFINED:
            return
        try:
            shelf_ids = await self._shelf_repo.get_shelves_of_book(str(entity.id))
        except Exception:  # noqa: BLE001 -- best-effort enrichment
            entity.shelf_ids = []
            return
        entity.shelf_ids = [str(s) for s in shelf_ids if s]

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

        updated_entity = await self._dir_repo.update_directory(
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

        # Shelf binding: replace the entire shelf set when the entity
        # carries shelf_ids; otherwise leave the binding alone.
        if entity.shelf_ids:
            current = await self.fetch_directory(entity.id)
            current_shelves: Set[str] = set()
            if current and current.shelf_ids:
                current_shelves = set(current.shelf_ids)
   
            new_shelf_ids = set(entity.shelf_ids or [])

            # delete old shelf bindings
            for sid in current_shelves - new_shelf_ids:
                await self._shelf_repo.remove_book(
                    sid, str(entity.id), user_ctx=None,
                )
            # add new bindings
            for sid in new_shelf_ids - current_shelves:
                await self._shelf_repo.add_book(
                    sid, str(entity.id), user_ctx=None,
                )

        if entity.tag_ids:
            await self._tag_repo.replace_tags_of(
                "directory", str(entity.id), list(entity.tag_ids),
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
        return await self._perm_repo.lookup(
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

    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        """Delete the directory row (cleanup is the caller's job)."""
        if not entity.id:
            raise ValueError("Directory ID is required for deletion")
        return await self._dir_repo.delete_directory(str(entity.id))

    async def fetch_directories_by_ids(
        self,
        ids: List[str],
    ) -> List[DirectoryEntity]:
        """Delegate to the inner directory repo.

        Lets callers holding the facade (bootstrap strategies,
        migrations) probe multiple rows by id without reaching
        around it.  Missing ids are silently dropped.
        """
        if not ids:
            return []
        return await self._dir_repo.fetch_directories_by_ids(
            [str(i) for i in ids if i]
        )

    # ---- DirectoryHelperMixin: hierarchy helpers ---------------------

    async def set_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
    ) -> None:
        """Replace every parent of ``child_id`` (typed) with ``parent_ids``.

        For ``parent_type="directory"`` writes both the Postgres
        hierarchy row and the matching SpiceDB
        ``parent_directory`` / ``parent`` relation so visibility
        checks against the directory pick up the new parents.

        For ``parent_type="shelf"`` only the Postgres row is
        written -- shelves own their own binding table
        (``note.shelf_book``) and the
        :class:`~src.api.repos.shelf_repo.ShelfRepoABC` is the
        source of truth for shelf<->book edges.
        """
        if parent_type == "shelf":
            raise ValueError(
                "shelf parents are owned by ShelfRepoABC.set_books_of()"
            )
        # Postgres write
        await self._dir_repo.set_parents_of(
            child_type, str(child_id), parent_type, parent_ids
        )
        # SpiceDB mirror: clear stale edges and write the new set.
        if child_type == "note":
            await self._perm_repo.delete(
                Relationship(
                    resource=ObjectRef("note", str(child_id)),
                    relation=NoteRelationEnum.PARENT_DIRECTORY,
                    subject=SubjectRef("directory", UNDEFINED),
                )
            )
            for p in parent_ids:
                await self._perm_repo.insert([
                    Relationship(
                        resource=ObjectRef("note", str(child_id)),
                        relation=NoteRelationEnum.PARENT_DIRECTORY,
                        subject=SubjectRef("directory", str(p)),
                    )]
                )
        elif child_type == "directory":
            await self._perm_repo.delete(
                Relationship(
                    resource=ObjectRef("directory", str(child_id)),
                    relation=DirectoryRelationEnum.PARENT,
                    subject=SubjectRef("directory", UNDEFINED),
                )
            )
            for p in parent_ids:
                await self._perm_repo.insert([
                    Relationship(
                        resource=ObjectRef("directory", str(child_id)),
                        relation=DirectoryRelationEnum.PARENT,
                        subject=SubjectRef("directory", str(p)),
                    )]
                )



    async def get_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
    ) -> List[str]:
        """Return parent ids of ``child_id`` (typed)."""
        return await self._dir_repo.get_parents_of(
            child_type, str(child_id), parent_type
        )

    async def get_children_of(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> List[str]:
        """Return child ids under ``parent_id`` (typed)."""
        return await self._dir_repo.get_children_of(
            parent_type, str(parent_id), child_type, depth=depth
        )

    async def get_children_for(
        self,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> Dict[str, List[str]]:
        """Return child ids for multiple ``parent_ids`` (typed)."""
        return await self._dir_repo.get_children_for(
            parent_type, [str(d) for d in parent_ids], child_type, depth=depth
        )

    async def get_parents_for(
        self,
        child_type: DirectoryChildType,
        child_ids: List[str],
        parent_type: DirectoryParentType,
    ) -> Dict[str, List[str]]:
        """Return parent ids for multiple ``child_ids`` (typed)."""
        return await self._dir_repo.get_parents_for(
            child_type, [str(c) for c in child_ids], parent_type
        )

    async def add_child_to(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        """Add a note or directory as a child of a directory or shelf.

        For ``parent_type="directory"`` writes both the Postgres
        hierarchy row and the matching SpiceDB relation.  For
        ``parent_type="shelf"`` raises -- shelf bindings live on
        :class:`ShelfRepoABC.add_book`.
        """
        if parent_type == "shelf":
            raise ValueError(
                "shelf bindings are owned by ShelfRepoABC.add_book()"
            )
        # Postgres write
        await self._dir_repo.add_child_to(
            parent_type, str(parent_id), child_type, str(child_id)
        )
        # SpiceDB mirror
        if child_type == "note":
            await self._perm_repo.insert(
                [
                    Relationship(
                        resource=ObjectRef(
                            object_type=ObjectTypeEnum.NOTE,
                            object_id=str(child_id),
                        ),
                        relation=NoteRelationEnum.PARENT_DIRECTORY,
                        subject=SubjectRef(
                            object_type=ObjectTypeEnum.DIRECTORY,
                            object_id=str(parent_id),
                        ),
                    )
                ]
            )
        elif child_type == "directory":
            await self._perm_repo.insert(
                [
                    Relationship(
                        resource=ObjectRef(
                            object_type=ObjectTypeEnum.DIRECTORY,
                            object_id=str(child_id),
                        ),
                        relation=DirectoryRelationEnum.PARENT,
                        subject=SubjectRef(
                            object_type=ObjectTypeEnum.DIRECTORY,
                            object_id=str(parent_id),
                        ),
                    )
                ]
            )

    async def remove_child_from(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        """Remove a binding (mirror)."""
        if parent_type == "shelf":
            raise ValueError(
                "shelf bindings are owned by ShelfRepoABC.remove_book()"
            )
        # Postgres write
        await self._dir_repo.remove_child_from(
            parent_type, str(parent_id), child_type, str(child_id)
        )
        # SpiceDB mirror
        if child_type == "note":
            await self._perm_repo.delete(
                Relationship(
                    resource=ObjectRef(
                        object_type=ObjectTypeEnum.NOTE,
                        object_id=str(child_id),
                    ),
                    relation=NoteRelationEnum.PARENT_DIRECTORY,
                    subject=SubjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY,
                        object_id=str(parent_id),
                    ),
                )
            )
        elif child_type == "directory":
            await self._perm_repo.delete(
                Relationship(
                    resource=ObjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY,
                        object_id=str(child_id),
                    ),
                    relation=DirectoryRelationEnum.PARENT,
                    subject=SubjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY,
                        object_id=str(parent_id),
                    ),
                )
            )

    # ---- higher-level helpers (facade-only, not on the ABC) ----------

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
            view_chain: PermissionCheckChain = HasDirectoryViewPerm(directory_id=str(directory_id)).set_permission_repo(self._perm_repo)
            can_view = await view_chain.check(actor)
            if can_view.error:
                raise can_view.error

        note_ids: set[str] = set()
        for start in start_directories:
            note_ids.update(
                await self._dir_repo.get_children_of(
                    "directory", start, "note", depth=max_depth
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
        notes = await self._dir_repo.get_children_of(
            "directory", directory_id, "note", depth=max_depth
        )
        directories = [directory_id]
        directories.extend(
            await self._dir_repo.get_children_of(
                "directory", directory_id, "directory", depth=max_depth
            )
        )
        return notes, sorted(set(directories))

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
        entities = await self._dir_repo.fetch_directories_by_ids(ids)
        if not entities:
            return []

        # Hydrate parents in parallel.
        async def _hydrate(entity: DirectoryEntity) -> DirectoryEntity:
            await self._hydrate_parents(entity, populate_parents=True)
            return entity

        hydrated = await asyncio.gather(*(_hydrate(e) for e in entities))
        result = list(hydrated)
        # Best-effort shelf_ids hydration.  Errors are swallowed;
        # an empty shelf_ids list is fine.
        await self._hydrate_shelf_ids_for(result)
        return result

    async def _hydrate_shelf_ids_for(
        self,
        entities: List[DirectoryEntity],
    ) -> None:
        """Populate ``shelf_ids`` for a batch of directories in one query.

        The :class:`ShelfRepoABC` exposes
        :meth:`ShelfRepoABC.get_shelves_of_book` per id, so we
        fan out in parallel for the batch.  Each call is one
        indexed SQL lookup against ``note.shelf_book``, which is
        cheap enough for the directories a single user can see.
        """
        if not entities:
            return

        async def _load(d: DirectoryEntity) -> None:
            if d.id is None or d.id is UNDEFINED:
                return
            try:
                shelf_ids = await self._shelf_repo.get_shelves_of_book(
                    str(d.id)
                )
            except Exception:  # noqa: BLE001 -- best-effort
                d.shelf_ids = []
                return
            d.shelf_ids = [str(s) for s in shelf_ids if s]

        await asyncio.gather(*(_load(d) for d in entities))

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
                await self._dir_repo.get_parents_of(
                    "directory", directory_id, "directory"
                )
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
        matched: List[Relationship] = await self._perm_repo.lookup_relationships(
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
            await self._dir_repo.get_parents_of(
                "directory", directory_id, "directory"
            )
        )
        desired = {str(p) for p in new_parent_ids if p}

        # Drop SpiceDB relations for parents that are no longer wanted.
        for removed in existing - desired:
            await self._perm_repo.delete(
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
            await self._perm_repo.insert(
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
        await self._dir_repo.set_parents_of(
            "directory", directory_id, "directory", sorted(desired)
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
        await self._perm_repo.insert([admin_relation])
        return admin_relation  # speed tradeoff to not call the permission repo a second time

__all__ = ["DirectoryFacadeImpl"]
