from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple
from src.api.repos.combined_note_repo import CombinedNoteRepoABC
from src.api.repos.tag_repo import TagRepoABC, TagSubjectType
from src.api.services.directory_service import DirectoryIncludeOptions
from src.api.services.note_service import NoteIncludeOptions
from src.api.other.undefined import UNDEFINED, is_undefined, unwrap_undefined_or
from src.api.other.user_context import UserContextABC
from src.db.database import DatabaseABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.tag import TagEntity
from src.db.entities.note.versioning import NoteVersionEntry
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.repos.directory_repo import (
    DirectoryChildType,
    DirectoryHierarchyType,
    DirectoryParentType,
)
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.other.relationship import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.repos.note_content_repo import NoteContentRepo
from src.db.table import TableABC
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.api.facades.note_facade import NoteFacadeABC, SearchType
from src.api.services.note_service import NoteResponse, NoteServiceABC
from src.services.attachment_facade import Attachment, AttachmentFacadeABC
from src.db.repos.note.versioning import NoteVersionRepoABC


class _FakeSpyDirectoryRepo(DirectoryFacadeABC):
    """In-memory directory repo used by unit tests.

    The default behaviour (used by every existing test that just wants
    *something* resolvable per user) seeds one directory per user id:
    ``"<default-name>-<user_id>"``.  Tests that need to drive
    create / update / delete / lookups by id, or a richer directory
    set, can populate ``self.directories_by_id`` /
    ``self.user_to_directory_ids`` directly.
    """

    def __init__(self, *, permission_repo: Optional[PermissionRepoABC] = None) -> None:
        # ``directories_by_id``: directory id -> DirectoryEntity.  Tests
        # can pre-populate this to control what ``fetch_directory``
        # returns.
        self.directories_by_id: Dict[str, DirectoryEntity] = {}
        # ``user_to_directory_ids``: user id -> list of directory ids.
        # When populated, ``list_user_directory_ids`` returns the
        # corresponding ids; otherwise the legacy fallback of a single
        # default directory is used so older tests keep working.
        self.user_to_directory_ids: Dict[str, List[str]] = {}
        # ``subtree_by_root``: root directory id ->
        # ``(note_ids, directory_ids)``.  Tests that need a populated
        # subtree seed this so ``resolve_subtree`` returns their ids.
        # Roots not in the dict fall back to ``([], [directory_id])``.
        self.subtree_by_root: Dict[str, Tuple[List[str], List[str]]] = {}
        # Recorded calls for assertions.
        self.created: List[DirectoryEntity] = []
        self.updated: List[DirectoryEntity] = []
        self.deleted: List[str] = []
        self._next_directory_id = 0
        # In-memory note / directory bindings.  Production persists
        # these in the ``note.directory_note`` /
        # ``note.directory_subdirectory`` tables; this fake mirrors the
        # shape the production code expects from those tables so the
        # ``add_child_to`` + ``get_parents_of`` /
        # ``get_children_of`` round-trip works end-to-end.
        self._directory_note: set[tuple[str, str]] = set()
        self._directory_child: set[tuple[str, str]] = set()
        # Optional permission repo reference so that ``create_directory``
        # can mirror the production behaviour of writing the relations
        # the entity carries.  Without this, tests that use the real
        # :class:`DirectoryServiceImpl` would fail every visibility check
        # inside :class:`NoteServiceImpl` / :class:`NoteFacadeImpl`.
        self._permission_repo = permission_repo

    @property
    def _default_directory_name(self) -> str:
        return self.get_default_directory_specs()[0].name

    def _seed_default_directory(self, user: UserContextABC) -> str:
        """Return and lazily create the default directory for ``user``."""
        directory_id = f"{self._default_directory_name}-{user.user_id}"
        if directory_id not in self.directories_by_id:
            self.directories_by_id[directory_id] = DirectoryEntity(
                id=directory_id, slug=self._default_directory_name
            )
        return directory_id

    async def create_directory(self, entity: DirectoryEntity, user_ctx: UserContextABC = None) -> DirectoryEntity:
        self._next_directory_id += 1
        new_id = entity.id if entity.id not in (None, UNDEFINED) else f"dir-{self._next_directory_id}"
        created = DirectoryEntity(
            id=new_id,
            slug=entity.slug,
            display_name=entity.display_name,
            description=entity.description,
            image_url=entity.image_url,
            readme_note_id=entity.readme_note_id,
            parent_directory_ids=entity.parent_directory_ids,
            tag_ids=entity.tag_ids,
            relations=entity.relations,
        )
        self.directories_by_id[str(new_id)] = created
        self.created.append(created)

        # Mirror the production :class:`DirectoryFacadeImpl`
        # behaviour: write the entity's relations through the permission
        # repo so that subsequent reads see the new directory as
        # visible to the caller.
        if self._permission_repo is not None and entity.relations:
            resolved = []
            for rel in entity.relations:
                resolved.append(
                    Relationship(
                        resource=ObjectRef(
                            object_type=rel.resource.object_type,
                            object_id=str(new_id),
                        ),
                        relation=rel.relation,
                        subject=rel.subject,
                    )
                )
            await self._permission_repo.insert(resolved)

        # Mirror the production :class:`DirectoryFacadeImpl` behaviour
        # of always attaching a ``dir#admin@user`` relation for the
        # caller.  Production writes it to the permission repo and
        # returns it on ``created.relations``; do the same here so
        # callers can inspect the relation without poking the
        # permission repo themselves.
        if self._permission_repo is not None and user_ctx is not None:
            admin_relation = Relationship(
                resource=ObjectRef(
                    object_type=ObjectTypeEnum.DIRECTORY,
                    object_id=str(new_id),
                ),
                relation=DirectoryRelationEnum.ADMIN,
                subject=SubjectRef(
                    object_type=ObjectTypeEnum.USER,
                    object_id=str(user_ctx.user_id),
                ),
            )
            await self._permission_repo.insert([admin_relation])
            if not isinstance(created.relations, list):
                created.relations = []
            created.relations.append(admin_relation)

        # Mirror the parent relations written when
        # ``entity.parent_directory_ids`` is set, matching the
        # production repo.
        parent_ids = entity.parent_directory_ids
        if (
            self._permission_repo is not None
            and parent_ids not in (UNDEFINED, None)
            and len(parent_ids) > 0
        ):
            for parent_id in parent_ids:
                if not parent_id:
                    continue
                await self._permission_repo.insert(
                    [
                        Relationship(
                            resource=ObjectRef(
                                object_type=ObjectTypeEnum.DIRECTORY,
                                object_id=str(new_id),
                            ),
                            relation=DirectoryRelationEnum.PARENT,
                            subject=SubjectRef(
                                object_type=ObjectTypeEnum.DIRECTORY,
                                object_id=str(parent_id),
                            ),
                        )
                    ]
                )

        return created

    async def fetch_directory(
        self,
        id: str,
        *,
        include: Optional[DirectoryIncludeOptions] = None,
    ) -> Optional[DirectoryEntity]:
        existing = self.directories_by_id.get(id)
        if existing is None:
            existing = DirectoryEntity(
                id=id, slug=self._default_directory_name
            )
        # No aggregate counts in this fake -- callers derive them
        # from `len(child_directory_ids)` / `len(child_note_ids)` when
        # those lists are populated.
        return existing

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        # Start from the test-managed map (always wins).
        if user.user_id in self.user_to_directory_ids:
            direct = set(self.user_to_directory_ids[user.user_id])
        else:
            direct = {self._seed_default_directory(user)}

        # Mirror the production behaviour: ``admin``, ``writer``,
        # ``reader`` and ``owner`` relations on a directory all imply
        # ``view`` (see :data:`docs.spicedb-schema`), so any directory
        # the user has one of those relations on is visible to them.
        # The in-memory :class:`InMemoryPermissionRepo` does not
        # expand transitive implications, so this fallback lives on
        # the directory repo instead.
        if self._permission_repo is not None:
            for rel in getattr(self._permission_repo, "_store", []):
                try:
                    if (
                        str(rel.resource.object_type)
                        == ObjectTypeEnum.DIRECTORY.value
                        and str(rel.subject.object_type)
                        == ObjectTypeEnum.USER.value
                        and str(rel.subject.object_id) == user.user_id
                        and str(rel.relation)
                        in {"admin", "writer", "reader", "owner"}
                        and rel.resource.object_id is not None
                    ):
                        direct.add(str(rel.resource.object_id))
                except Exception:
                    # Tolerate half-constructed relationships.
                    continue
        return sorted(direct)

    async def fetch_all_directories(self) -> List[DirectoryEntity]:
        return list(self.directories_by_id.values())

    async def fetch_directories_by_ids(self, ids: List[str]) -> List[DirectoryEntity]:
        """Return directories in input order; missing ids are dropped."""
        out: List[DirectoryEntity] = []
        for did in ids:
            existing = self.directories_by_id.get(str(did))
            if existing is not None:
                out.append(existing)
        return out

    async def insert_directory(
        self,
        *,
        slug: str,
        display_name: "Optional[str]" = None,
        description: "Optional[str]" = None,
        image_url: "Optional[str]" = None,
        readme_note_id: "Optional[str]" = None,
    ) -> DirectoryEntity:
        """Insert a directory row and return the persisted entity.

        Low-level counterpart of :meth:`create_directory` -- exposes
        the :class:`DirectoryRepoABC` surface the production
        :class:`DirectoryFacadeImpl` calls.  Permissions and
        per-user admin relations are written by the facade on top
        of this stub; here we only own the row state.
        """
        self._next_directory_id += 1
        new_id = f"dir-{self._next_directory_id}"
        created = DirectoryEntity(
            id=new_id,
            slug=slug,
            display_name=display_name,
            description=description,
            image_url=image_url,
            readme_note_id=readme_note_id,
        )
        self.directories_by_id[str(new_id)] = created
        return created

    async def delete_directory(self, entity_or_id) -> bool:  # type: ignore[override]
        """Delete a directory, accepting either an entity or an id."""
        directory_id = (
            str(entity_or_id.id)
            if hasattr(entity_or_id, "id")
            else str(entity_or_id)
        )
        self.deleted.append(directory_id)
        if directory_id in self.directories_by_id:
            del self.directories_by_id[directory_id]
        return True

    async def update_directory(
        self,
        id_or_entity,  # type: ignore[no-untyped-def]
        *,
        slug="__UNSET__",
        display_name="__UNSET__",
        description="__UNSET__",
        image_url="__UNSET__",
        readme_note_id="__UNSET__",
    ) -> Optional[DirectoryEntity]:
        """Update a directory, accepting either the repo or facade form.

        The :class:`DirectoryFacadeImpl` form passes ``id`` plus
        per-field kwargs; the legacy / unit-test form passes a
        :class:`DirectoryEntity`.  Both call sites land here; the
        repo form is the canonical one.
        """
        is_entity_form = hasattr(id_or_entity, "slug")
        if is_entity_form:
            entity = id_or_entity
            existing_id = str(entity.id)
            existing = self.directories_by_id.get(existing_id)
            if existing is None:
                return None
            updated = DirectoryEntity(
                id=existing_id,
                slug=entity.slug if not is_undefined(entity.slug) else existing.slug,
                display_name=(
                    entity.display_name
                    if not is_undefined(entity.display_name)
                    else existing.display_name
                ),
                description=(
                    entity.description
                    if not is_undefined(entity.description)
                    else existing.description
                ),
                image_url=(
                    entity.image_url
                    if not is_undefined(entity.image_url)
                    else existing.image_url
                ),
                readme_note_id=(
                    entity.readme_note_id
                    if not is_undefined(entity.readme_note_id)
                    else existing.readme_note_id
                ),
            )
            self.directories_by_id[existing_id] = updated
            self.updated.append(updated)
            return updated

        existing_id = str(id_or_entity)
        existing = self.directories_by_id.get(existing_id)
        if existing is None:
            return None
        updated = DirectoryEntity(
            id=existing_id,
            slug=slug if slug != "__UNSET__" else existing.slug,
            display_name=(
                display_name if display_name != "__UNSET__" else existing.display_name
            ),
            description=(
                description if description != "__UNSET__" else existing.description
            ),
            image_url=(
                image_url if image_url != "__UNSET__" else existing.image_url
            ),
            readme_note_id=(
                readme_note_id
                if readme_note_id != "__UNSET__"
                else existing.readme_note_id
            ),
        )
        self.directories_by_id[existing_id] = updated
        self.updated.append(updated)
        return updated

    async def resolve_files_of_directory(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
    ) -> List[str]:
        return []

    async def _count_direct_child_directories_for(
        self, directory_id: str,
    ) -> int:
        """Internal helper: count direct child directories via the in-memory store."""
        if self._permission_repo is None:
            return 0
        return len(
            await self._permission_repo.lookup(
                Relationship(
                    resource=ObjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY, object_id=UNDEFINED
                    ),
                    relation=DirectoryRelationEnum.PARENT,
                    subject=SubjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY, object_id=str(directory_id)
                    ),
                )
            )
        )

    async def _count_direct_child_notes_for(
        self, directory_id: str,
    ) -> int:
        """Internal helper: count direct child notes via the in-memory store."""
        if self._permission_repo is None:
            return 0
        return len(
            await self._permission_repo.lookup(
                Relationship(
                    resource=ObjectRef(
                        object_type=ObjectTypeEnum.NOTE, object_id=UNDEFINED
                    ),
                    relation=NoteRelationEnum.PARENT_DIRECTORY,
                    subject=SubjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY, object_id=str(directory_id)
                    ),
                )
            )
        )

    async def resolve_subtree(
        self, directory_id: str, *, max_depth: int = 10,
    ) -> tuple[list[str], list[str]]:
        """Return seeded subtree ids for `directory_id`.

        Returns the seeded ``(note_ids, directory_ids)`` for
        ``directory_id`` from :attr:`subtree_by_root`, or the root
        alone when no seed is present.
        """
        return self.subtree_by_root.get(directory_id, ([], [directory_id]))

    # ---- DirectoryHelperMixin: hierarchy helpers (typed) ------

    async def set_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
    ) -> None:
        """Replace every parent of ``child_id`` with ``parent_ids``.

        Only ``parent_type="directory"`` is modelled in this fake
        (the only parent type the directory helper actually owns);
        shelf parents raise because the fake doesn't carry a
        shelf store.
        """
        if parent_type == "shelf":
            raise ValueError(
                "shelf bindings are owned by ShelfRepoABC; "
                "shelf parents not modelled in this fake"
            )
        child_id = str(child_id)
        if child_type == "note":
            self._directory_note = {
                (parent, note) for (parent, note) in self._directory_note
                if note != child_id
            }
            for parent in parent_ids:
                self._directory_note.add((str(parent), child_id))
        elif child_type == "directory":
            self._directory_child = {
                (parent, ch) for (parent, ch) in self._directory_child
                if ch != child_id
            }
            for parent in parent_ids:
                self._directory_child.add((str(parent), child_id))

    async def get_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
    ) -> List[str]:
        """Return parent ids of ``child_id`` filtered by ``parent_type``."""
        if parent_type == "shelf":
            return []
        child_id = str(child_id)
        parents: set[str] = set()
        if child_type == "note":
            for parent, note_id in self._directory_note:
                if note_id == child_id:
                    parents.add(parent)
        elif child_type == "directory":
            for parent, ch in self._directory_child:
                if ch == child_id:
                    parents.add(parent)
        return sorted(parents)

    async def get_children_of(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> List[str]:
        """Return child ids of ``parent_id`` filtered by ``child_type``."""
        if depth <= 0:
            return []
        if parent_type == "shelf":
            # Shelf children aren't modelled here.  The caller
            # should ask the shelf repo for the books.
            if child_type in ("directory", "both"):
                return []
            raise ValueError(
                "shelves cannot have notes as children"
            )
        parent_id = str(parent_id)
        note_ids: set[str] = set()
        child_dir_ids: set[str] = set()
        visited: set[str] = {parent_id}
        queue: list[tuple[str, int]] = [(parent_id, 0)]
        while queue:
            current, current_depth = queue.pop(0)
            if child_type in ("note", "both"):
                for parent, note_id in self._directory_note:
                    if parent == current and current_depth + 1 <= depth:
                        note_ids.add(note_id)
            for parent, child in self._directory_child:
                if parent == current and child not in visited:
                    if child_type in ("directory", "both"):
                        child_dir_ids.add(child)
                    if current_depth + 1 < depth:
                        visited.add(child)
                        queue.append((child, current_depth + 1))
        if child_type == "note":
            return sorted(note_ids)
        if child_type == "directory":
            return sorted(child_dir_ids)
        return sorted(note_ids | child_dir_ids)

    async def get_children_for(
        self,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> Dict[str, List[str]]:
        """Return per-input-parent children from the in-memory store.

        Mirrors the production ``get_children_for`` semantics
        plus the permission-repo fallback for tests that only
        populate the SpiceDB side.
        """
        if depth <= 0:
            return {str(d): [] for d in parent_ids}
        result: Dict[str, List[str]] = {}
        for parent_id in parent_ids:
            parent_id = str(parent_id)
            note_ids: set[str] = set()
            child_dir_ids: set[str] = set()
            visited: set[str] = {parent_id}
            queue: list[tuple[str, int]] = [(parent_id, 0)]
            while queue:
                current, current_depth = queue.pop(0)
                if parent_type == "shelf":
                    # Shelf children aren't modelled here.
                    pass
                elif child_type in ("note", "both"):
                    for parent, note_id in self._directory_note:
                        if parent == current and current_depth + 1 <= depth:
                            note_ids.add(note_id)
                for parent, child in self._directory_child:
                    if parent == current and child not in visited:
                        if child_type in ("directory", "both") and parent_type == "directory":
                            child_dir_ids.add(child)
                        if current_depth + 1 < depth and parent_type == "directory":
                            visited.add(child)
                            queue.append((child, current_depth + 1))
            # Fallback: read from the permission repo for tests that
            # only populate the SpiceDB side.  Production keeps both
            # stores in sync, so this is purely a unit-test aid.
            if child_type in ("note", "both") and parent_type == "directory" and self._permission_repo is not None:
                note_ids.update(
                    await self._permission_repo.lookup(
                        Relationship(
                            resource=ObjectRef(
                                object_type=ObjectTypeEnum.NOTE,
                                object_id=UNDEFINED,
                            ),
                            relation=NoteRelationEnum.PARENT_DIRECTORY,
                            subject=SubjectRef(
                                object_type=ObjectTypeEnum.DIRECTORY,
                                object_id=parent_id,
                            ),
                        )
                    )
                )
            elif child_type in ("directory", "both") and parent_type == "directory" and self._permission_repo is not None:
                child_dir_ids.update(
                    await self._permission_repo.lookup(
                        Relationship(
                            resource=ObjectRef(
                                object_type=ObjectTypeEnum.DIRECTORY,
                                object_id=UNDEFINED,
                            ),
                            relation=DirectoryRelationEnum.PARENT,
                            subject=SubjectRef(
                                object_type=ObjectTypeEnum.DIRECTORY,
                                object_id=parent_id,
                            ),
                        )
                    )
                )
            if child_type == "note":
                result[parent_id] = sorted(note_ids)
            elif child_type == "directory":
                result[parent_id] = sorted(child_dir_ids)
            else:
                result[parent_id] = sorted(note_ids | child_dir_ids)
        return result

    async def get_parents_for(
        self,
        child_type: DirectoryChildType,
        child_ids: List[str],
        parent_type: DirectoryParentType,
    ) -> Dict[str, List[str]]:
        """Return per-input-id parents from the in-memory store."""
        result: Dict[str, List[str]] = {}
        if parent_type == "shelf":
            return {str(c): [] for c in child_ids}
        for child_id in child_ids:
            child_id = str(child_id)
            parents: set[str] = set()
            if child_type == "note":
                for parent, note_id in self._directory_note:
                    if note_id == child_id:
                        parents.add(parent)
            elif child_type == "directory":
                for parent, ch in self._directory_child:
                    if ch == child_id:
                        parents.add(parent)
            # Fallback: read from the permission repo for tests that
            # only populate the SpiceDB side.
            if child_type == "note" and self._permission_repo is not None:
                parents.update(
                    await self._permission_repo.lookup(
                        Relationship(
                            resource=ObjectRef(
                                object_type=ObjectTypeEnum.NOTE,
                                object_id=child_id,
                            ),
                            relation=NoteRelationEnum.PARENT_DIRECTORY,
                            subject=SubjectRef(
                                object_type=ObjectTypeEnum.DIRECTORY,
                                object_id=UNDEFINED,
                            ),
                        )
                    )
                )
            elif child_type == "directory" and self._permission_repo is not None:
                parents.update(
                    await self._permission_repo.lookup(
                        Relationship(
                            resource=ObjectRef(
                                object_type=ObjectTypeEnum.DIRECTORY,
                                object_id=child_id,
                            ),
                            relation=DirectoryRelationEnum.PARENT,
                            subject=SubjectRef(
                                object_type=ObjectTypeEnum.DIRECTORY,
                                object_id=UNDEFINED,
                            ),
                        )
                    )
                )
            result[child_id] = sorted(parents)
        return result

    async def add_child_to(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        """Add a note or child directory binding."""
        if parent_type == "shelf":
            raise ValueError(
                "shelf bindings are owned by ShelfRepoABC; "
                "shelf parents not modelled in this fake"
            )
        parent_id = str(parent_id)
        child_id = str(child_id)
        if child_type == "note":
            self._directory_note.add((parent_id, child_id))
        elif child_type == "directory":
            self._directory_child.add((parent_id, child_id))

    async def remove_child_from(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        """Remove a note or child directory binding."""
        if parent_type == "shelf":
            raise ValueError(
                "shelf bindings are owned by ShelfRepoABC; "
                "shelf parents not modelled in this fake"
            )
        parent_id = str(parent_id)
        child_id = str(child_id)
        if child_type == "note":
            self._directory_note.discard((parent_id, child_id))
        elif child_type == "directory":
            self._directory_child.discard((parent_id, child_id))


__all__ = [
    '_FakeSpyDirectoryRepo',
]
