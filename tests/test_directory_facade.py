"""Unit tests for :class:`src.db.repos.directory.directory.DirectoryFacadeImpl`.

The facade composes a low-level :class:`DirectoryRepoABC` (Postgres
storage) with a :class:`PermissionRepoABC` (SpiceDB) and a
:class:`TagRepoABC` (taxonomy).  Every public method that mutates
state MUST write through BOTH stores in lockstep so visibility checks
remain consistent with the row graph.

These tests wire the real :class:`DirectoryFacadeImpl` against
in-memory fakes that **record every method call**, so the assertions
are about *which* calls were made on which repo -- not about
implementation details of the underlying Postgres or SpiceDB.

The :class:`_RecordingDirectoryRepo` follows the
:class:`DirectoryRepoABC` contract (3-arg
:meth:`set_parent_directories_of`) so any mismatch between the
facade's call signature and the repo contract surfaces as a
:exc:`TypeError` at test time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from src.api.other.relationship import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.api.repos.directory_repo import (
    DirectoryChildType,
    DirectoryHierarchyType,
    DirectoryRepoABC,
)
from src.api.repos.tag_repo import TagRepoABC
from src.api.services.directory_service import DirectoryIncludeOptions
from src.db.entities.directory.directory import DirectoryEntity
from src.db.repos.directory.directory import DirectoryFacadeImpl
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.user_context import _UserContext


# ---------------------------------------------------------------------------
# Recording fakes
# ---------------------------------------------------------------------------


@dataclass
class _InsertCall:
    """Recorded :meth:`DirectoryRepoABC.insert_directory` invocation."""

    slug: str
    display_name: Any
    description: Any
    image_url: Any
    readme_note_id: Any


@dataclass
class _UpdateCall:
    """Recorded :meth:`DirectoryRepoABC.update_directory` invocation."""

    directory_id: str
    slug: Any
    display_name: Any
    description: Any
    image_url: Any
    readme_note_id: Any


@dataclass
class _SetParentCall:
    """Recorded :meth:`DirectoryRepoABC.set_parent_directories_of` invocation."""

    subject_type: DirectoryChildType
    subject_id: str
    parent_ids: List[str]


@dataclass
class _ChildCall:
    """Recorded :meth:`DirectoryRepoABC.add/remove_child_to_directory` invocation."""

    type: DirectoryChildType
    directory_id: str
    child_id: str


class _RecordingDirectoryRepo(DirectoryRepoABC):
    """In-memory :class:`DirectoryRepoABC` that records every public call.

    Stores enough state to round-trip the methods the facade depends on
    (insert / update / fetch / hierarchy) so behaviour-driven tests can
    assert both *what was called* and *what the resulting state looks
    like*.

    The hierarchy helpers are implemented against an internal
    ``(child_type, child_id) -> {parent_id}`` map and a
    ``(parent_type, parent_id) -> {child_id}`` map so
    :meth:`get_parent_of` and :meth:`get_children_of` mirror the
    production Postgres semantics used by the facade.
    """

    def __init__(self) -> None:
        # ---- row store -------------------------------------------------
        self.entities: Dict[str, DirectoryEntity] = {}
        self._next_id = 0

        # ---- hierarchy store -------------------------------------------
        # (child_type, child_id) -> set of parent ids
        self._parents: Dict[Tuple[str, str], Set[str]] = {}
        # (parent_type, parent_id) -> set of child ids
        self._children: Dict[Tuple[str, str], Set[str]] = {}

        # ---- recorded calls --------------------------------------------
        self.insert_calls: List[_InsertCall] = []
        self.fetch_calls: List[Tuple[str, Optional[DirectoryIncludeOptions]]] = []
        self.fetch_by_ids_calls: List[List[str]] = []
        self.update_calls: List[_UpdateCall] = []
        self.delete_calls: List[str] = []
        self.set_parent_calls: List[_SetParentCall] = []
        self.get_parent_calls: List[Tuple[DirectoryHierarchyType, str]] = []
        self.get_children_calls: List[Tuple[DirectoryHierarchyType, str, int]] = []
        self.get_children_for_calls: List[Tuple[DirectoryHierarchyType, List[str], int]] = []
        self.get_parent_for_calls: List[Tuple[DirectoryHierarchyType, List[str]]] = []
        self.add_child_calls: List[_ChildCall] = []
        self.remove_child_calls: List[_ChildCall] = []

    # ---- helpers --------------------------------------------------------

    def _next_id_str(self, hint: Optional[str] = None) -> str:
        if hint:
            return str(hint)
        self._next_id += 1
        return f"dir-{self._next_id}"

    def _parents_of(
        self, child_type: DirectoryHierarchyType, child_id: str
    ) -> Set[str]:
        return set(self._parents.get((child_type, str(child_id)), set()))

    def _children_of(
        self, parent_type: DirectoryHierarchyType, parent_id: str
    ) -> Set[str]:
        return set(self._children.get((parent_type, str(parent_id)), set()))

    # ---- DirectoryRepoABC ----------------------------------------------

    async def insert_directory(
        self,
        *,
        slug: str,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
    ) -> DirectoryEntity:
        new_id = self._next_id_str()
        entity = DirectoryEntity(
            id=new_id,
            slug=slug,
            display_name=display_name,
            description=description,
            image_url=image_url,
            readme_note_id=readme_note_id,
        )
        self.entities[new_id] = entity
        self.insert_calls.append(
            _InsertCall(
                slug=slug,
                display_name=display_name,
                description=description,
                image_url=image_url,
                readme_note_id=readme_note_id,
            )
        )
        return entity

    async def fetch_directory(
        self,
        id: str,
        *,
        include: Optional[DirectoryIncludeOptions] = None,
    ) -> Optional[DirectoryEntity]:
        self.fetch_calls.append((str(id), include))
        return self.entities.get(str(id))

    async def fetch_directories_by_ids(self, ids: List[str]) -> List[DirectoryEntity]:
        ids = [str(i) for i in ids]
        self.fetch_by_ids_calls.append(ids)
        return [self.entities[i] for i in ids if i in self.entities]

    async def update_directory(
        self,
        id: str,
        *,
        slug: UndefinedOr[str] = UNDEFINED,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
    ) -> Optional[DirectoryEntity]:
        self.update_calls.append(
            _UpdateCall(
                directory_id=str(id),
                slug=slug,
                display_name=display_name,
                description=description,
                image_url=image_url,
                readme_note_id=readme_note_id,
            )
        )
        existing = self.entities.get(str(id))
        if existing is None:
            return None

        def _pick(new: Any, old: Any) -> Any:
            """UNDEFINED -> keep, None -> clear, value -> overwrite."""
            if new is UNDEFINED:
                return old
            return new

        updated = DirectoryEntity(
            id=existing.id,
            slug=_pick(slug, existing.slug),
            display_name=_pick(display_name, existing.display_name),
            description=_pick(description, existing.description),
            image_url=_pick(image_url, existing.image_url),
            readme_note_id=_pick(readme_note_id, existing.readme_note_id),
        )
        self.entities[str(id)] = updated
        return updated

    async def delete_directory(self, id: str) -> bool:
        self.delete_calls.append(str(id))
        existed = self.entities.pop(str(id), None) is not None
        # Drop any hierarchy edges that referenced this id on either side.
        for key in list(self._parents):
            if str(id) in self._parents[key]:
                self._parents[key].discard(str(id))
        for key in list(self._children):
            if str(id) in self._children[key]:
                self._children[key].discard(str(id))
        return existed

    # ---- DirectoryHelperMixin -----------------------------------------

    async def set_parent_directories_of(
        self,
        subject_type: DirectoryChildType,
        subject_id: str,
        parent_ids: List[str],
    ) -> None:
        self.set_parent_calls.append(
            _SetParentCall(
                subject_type=subject_type,
                subject_id=str(subject_id),
                parent_ids=list(parent_ids),
            )
        )
        # Replace the parent set on this (subject_type, subject_id) row.
        cleaned = {str(p) for p in parent_ids if p}
        self._parents[(subject_type, str(subject_id))] = cleaned
        # Mirror the inverse edge: parent -> child.
        for existing_parent in list(self._children):
            if (
                existing_parent[0] == subject_type
                and str(subject_id) in self._children[existing_parent]
                and existing_parent[1] not in cleaned
            ):
                self._children[existing_parent].discard(str(subject_id))
        for parent_id in cleaned:
            self._children.setdefault(
                (subject_type, parent_id), set()
            ).add(str(subject_id))

    async def get_parent_of(
        self,
        type: DirectoryHierarchyType,
        child_id: str,
    ) -> List[str]:
        self.get_parent_calls.append((type, str(child_id)))
        if type == "both":
            union = set()
            for child_type in ("note", "directory"):
                union |= self._parents_of(child_type, child_id)  # type: ignore[arg-type]
            return sorted(union)
        return sorted(self._parents_of(type, child_id))  # type: ignore[arg-type]

    async def get_children_of(
        self,
        type: DirectoryHierarchyType,
        directory_id: str,
        depth: int = 1,
    ) -> List[str]:
        if depth < 0:
            raise ValueError("depth must be >= 0")
        self.get_children_calls.append((type, str(directory_id), depth))
        return sorted(self._children_of(type, directory_id))  # type: ignore[arg-type]

    async def get_children_for(
        self,
        type: DirectoryHierarchyType,
        directory_ids: List[str],
        depth: int = 1,
    ) -> List[str]:
        if depth < 0:
            raise ValueError("depth must be >= 0")
        self.get_children_for_calls.append((type, [str(d) for d in directory_ids], depth))
        union: Set[str] = set()
        for d in directory_ids:
            union |= self._children_of(type, str(d))  # type: ignore[arg-type]
        return sorted(union)

    async def get_parent_for(
        self,
        type: DirectoryHierarchyType,
        child_ids: List[str],
    ) -> List[str]:
        self.get_parent_for_calls.append((type, [str(c) for c in child_ids]))
        union: Set[str] = set()
        for c in child_ids:
            union |= self._parents_of(type, str(c))  # type: ignore[arg-type]
        return sorted(union)

    async def add_child_to_directory(
        self,
        type: DirectoryChildType,
        directory_id: str,
        child_id: str,
    ) -> None:
        self.add_child_calls.append(
            _ChildCall(type=type, directory_id=str(directory_id), child_id=str(child_id))
        )
        self._parents.setdefault((type, str(child_id)), set()).add(str(directory_id))
        self._children.setdefault((type, str(directory_id)), set()).add(str(child_id))

    async def remove_child_from_directory(
        self,
        type: DirectoryChildType,
        directory_id: str,
        child_id: str,
    ) -> None:
        self.remove_child_calls.append(
            _ChildCall(type=type, directory_id=str(directory_id), child_id=str(child_id))
        )
        if (type, str(child_id)) in self._parents:
            self._parents[(type, str(child_id))].discard(str(directory_id))
        if (type, str(directory_id)) in self._children:
            self._children[(type, str(directory_id))].discard(str(child_id))


class _RecordingTagRepo(TagRepoABC):
    """Bare-bones :class:`TagRepoABC` that only records calls.

    The facade only ever calls :meth:`replace_tags_of` on the tag
    repo, so we keep this fake minimal: tag identity is not checked,
    only the recorded call arguments.
    """

    def __init__(self) -> None:
        self.replace_calls: List[Tuple[str, str, List[str]]] = []

    async def replace_tags_of(
        self,
        subject_type,
        subject_id,
        tag_ids,
    ) -> None:  # type: ignore[override]
        self.replace_calls.append(
            (subject_type, str(subject_id), [str(t) for t in tag_ids])
        )

    # ---- Not exercised by these tests; stub to keep the ABC satisfied -

    async def create_tag(self, slug, display_name):  # type: ignore[override]
        raise NotImplementedError

    async def get_tag_by_id(self, tag_id):  # type: ignore[override]
        raise NotImplementedError

    async def list_tags(self):  # type: ignore[override]
        raise NotImplementedError

    async def update_tag(self, tag_id, *, slug=None, display_name=None):  # type: ignore[override]
        raise NotImplementedError

    async def delete_tag(self, tag_id):  # type: ignore[override]
        raise NotImplementedError

    async def list_tags_for(self, subject_type, subject_ids):  # type: ignore[override]
        raise NotImplementedError

    async def assign_tag_to(self, subject_type, subject_id, tag_id):  # type: ignore[override]
        raise NotImplementedError

    async def replace_tags_for(self, subject_type, subject_ids, tag_ids):  # type: ignore[override]
        raise NotImplementedError

    async def remove_tag_from(self, subject_type, subject_id, tag_id):  # type: ignore[override]
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _perm_relations(permission_repo: InMemoryPermissionRepo) -> List[Relationship]:
    """Snapshot every relationship currently stored in the perm repo."""
    return list(permission_repo._store)


def _relations_of(
    permission_repo: InMemoryPermissionRepo,
    *,
    resource_type: Optional[ObjectTypeEnum] = None,
    relation: Optional[str] = None,
    subject_type: Optional[ObjectTypeEnum] = None,
    subject_id: Optional[str] = None,
) -> List[Relationship]:
    """Filter the stored relationships by every provided field."""
    out: List[Relationship] = []
    for stored in permission_repo._store:
        if resource_type is not None and str(stored.resource.object_type) != str(resource_type):
            continue
        if relation is not None and str(stored.relation) != relation:
            continue
        if subject_type is not None and str(stored.subject.object_type) != str(subject_type):
            continue
        if subject_id is not None and str(stored.subject.object_id) != subject_id:
            continue
        out.append(stored)
    return out


def _build_facade(
    *,
    directory_repo: Optional[_RecordingDirectoryRepo] = None,
    permission_repo: Optional[InMemoryPermissionRepo] = None,
    tag_repo: Optional[TagRepoABC] = None,
) -> Tuple[
    DirectoryFacadeImpl,
    _RecordingDirectoryRepo,
    InMemoryPermissionRepo,
    TagRepoABC,
]:
    """Wire a :class:`DirectoryFacadeImpl` against the recording fakes."""
    dir_repo = directory_repo or _RecordingDirectoryRepo()
    perm_repo = permission_repo or InMemoryPermissionRepo()
    tags = tag_repo if tag_repo is not None else _RecordingTagRepo()
    facade = DirectoryFacadeImpl(
        directory_repo=dir_repo,
        permission_repo=perm_repo,
        tag_repo=tags,
        log=lambda *_args, **_kwargs: None,
    )
    return facade, dir_repo, perm_repo, tags


# ---------------------------------------------------------------------------
# create_directory
# ---------------------------------------------------------------------------


async def test_create_directory_writes_to_dir_repo() -> None:
    """`create_directory` persists the row via the directory repo."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    entity = DirectoryEntity(
        slug="inbox",
        display_name="Inbox",
        description="notes that need a home",
    )
    await facade.create_directory(entity, _UserContext("alice"))

    assert len(dir_repo.insert_calls) == 1
    call = dir_repo.insert_calls[0]
    assert call.slug == "inbox"
    assert call.display_name == "Inbox"
    assert call.description == "notes that need a home"


async def test_create_directory_inserts_admin_relation_in_perm_repo() -> None:
    """`create_directory` writes a `dir#admin@user` relation for the creator."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    entity = DirectoryEntity(slug="inbox", display_name="Inbox")
    created = await facade.create_directory(entity, _UserContext("alice"))

    admins = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.DIRECTORY,
        relation=str(DirectoryRelationEnum.ADMIN),
        subject_type=ObjectTypeEnum.USER,
        subject_id="alice",
    )
    assert len(admins) == 1
    assert str(admins[0].resource.object_id) == str(created.id)


async def test_create_directory_with_parents_writes_to_both_repos() -> None:
    """Parent ids flow into both the dir_repo (hierarchy) and the perm_repo.

    Without the parent the dir_repo sees only the row insert;
    with the parent both stores receive a write so the parent
    `dir#parent@dir` graph stays consistent.
    """
    facade, dir_repo, perm_repo, _tags = _build_facade()

    entity = DirectoryEntity(
        slug="child",
        display_name="Child",
        parent_directory_ids=["parent-1", "parent-2"],
    )
    created = await facade.create_directory(entity, _UserContext("alice"))

    # dir_repo: row insert + parent hierarchy write via set_parent_directories_of
    assert len(dir_repo.insert_calls) == 1
    assert len(dir_repo.set_parent_calls) == 1
    parent_write = dir_repo.set_parent_calls[0]
    assert parent_write.subject_type == "directory"
    assert parent_write.subject_id == str(created.id)
    assert sorted(parent_write.parent_ids) == ["parent-1", "parent-2"]

    # perm_repo: admin relation + two `dir#parent@dir` relations
    parents = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.DIRECTORY,
        relation=str(DirectoryRelationEnum.PARENT),
        subject_type=ObjectTypeEnum.DIRECTORY,
    )
    assert {str(p.subject.object_id) for p in parents} == {"parent-1", "parent-2"}
    assert all(str(p.resource.object_id) == str(created.id) for p in parents)


async def test_create_directory_with_tags_calls_tag_repo() -> None:
    """Non-empty `tag_ids` propagate to the tag repo as a `replace_tags_of`."""
    recording_tags = _RecordingTagRepo()
    facade, dir_repo, _perm_repo, tags = _build_facade(tag_repo=recording_tags)

    entity = DirectoryEntity(
        slug="inbox",
        display_name="Inbox",
        tag_ids=["t-1", "t-2"],
    )
    await facade.create_directory(entity, _UserContext("alice"))

    assert len(tags.replace_calls) == 1
    subject_type, subject_id, tag_ids = tags.replace_calls[0]
    assert subject_type == "directory"
    # The tag repo gets the id the dir_repo assigned on insert.
    assert subject_id == list(dir_repo.entities.keys())[0]
    assert tag_ids == ["t-1", "t-2"]


# ---------------------------------------------------------------------------
# add_note_to_directory / remove_note_from_directory
# ---------------------------------------------------------------------------


async def test_add_note_to_directory_writes_to_both_repos() -> None:
    """Adding a note writes the hierarchy row AND a `note#parent_directory@dir`."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    await facade.add_note_to_directory("note-7", "dir-1")

    # dir_repo side
    assert dir_repo.add_child_calls == [
        _ChildCall(type="note", directory_id="dir-1", child_id="note-7")
    ]

    # perm_repo side
    relations = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.NOTE,
        relation=str(NoteRelationEnum.PARENT_DIRECTORY),
        subject_type=ObjectTypeEnum.DIRECTORY,
        subject_id="dir-1",
    )
    assert len(relations) == 1
    assert str(relations[0].resource.object_id) == "note-7"


async def test_remove_note_from_directory_writes_to_both_repos() -> None:
    """Removing a note drops the hierarchy row AND the `parent_directory` relation.

    Seeds the relation first so the test can assert the relation
    actually went away (the in-memory perm repo does not log
    deletes -- only the *current* state -- so we rely on
    `_perm_relations` snapshots taken before/after).
    """
    facade, dir_repo, perm_repo, _tags = _build_facade()

    await facade.add_note_to_directory("note-7", "dir-1")
    before = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.NOTE,
        relation=str(NoteRelationEnum.PARENT_DIRECTORY),
    )
    assert len(before) == 1

    await facade.remove_note_from_directory("note-7", "dir-1")

    # dir_repo side
    assert dir_repo.remove_child_calls == [
        _ChildCall(type="note", directory_id="dir-1", child_id="note-7")
    ]

    # perm_repo side: the relation is gone
    after = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.NOTE,
        relation=str(NoteRelationEnum.PARENT_DIRECTORY),
    )
    assert after == []


async def test_add_note_to_directory_rejects_undefined_ids() -> None:
    """Both `note_id` and `directory_id` are validated before any write.

    Asserts that *neither* repo is touched when validation fails.
    """
    facade, dir_repo, perm_repo, _tags = _build_facade()

    for bad_note in (None, UNDEFINED):
        with _expect_value_error("note_id is required"):
            await facade.add_note_to_directory(bad_note, "dir-1")  # type: ignore[arg-type]
    for bad_dir in (None, UNDEFINED):
        with _expect_value_error("directory_id is required"):
            await facade.add_note_to_directory("note-1", bad_dir)  # type: ignore[arg-type]

    assert dir_repo.add_child_calls == []
    assert _perm_relations(perm_repo) == []


async def test_remove_note_from_directory_rejects_undefined_ids() -> None:
    """Same validation as `add_note_to_directory`, on the remove path."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    for bad_note in (None, UNDEFINED):
        with _expect_value_error("note_id is required"):
            await facade.remove_note_from_directory(bad_note, "dir-1")  # type: ignore[arg-type]
    for bad_dir in (None, UNDEFINED):
        with _expect_value_error("directory_id is required"):
            await facade.remove_note_from_directory("note-1", bad_dir)  # type: ignore[arg-type]

    assert dir_repo.remove_child_calls == []
    assert _perm_relations(perm_repo) == []


# ---------------------------------------------------------------------------
# update_directory
# ---------------------------------------------------------------------------


async def test_update_directory_writes_scalar_fields_to_dir_repo() -> None:
    """Scalar updates reach `dir_repo.update_directory` with the right kwargs."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    # seed a directory to update
    created = await facade.create_directory(
        DirectoryEntity(slug="inbox", display_name="Inbox"),
        _UserContext("alice"),
    )
    perm_repo._store.clear()  # clear admin relation so we can isolate later
    dir_repo.update_calls.clear()

    update = DirectoryEntity(
        id=created.id,
        slug="inbox-renamed",
        display_name="Inbox Renamed",
        description="new description",
        image_url=None,  # explicit None -> cleared
    )
    await facade.update_directory(update)

    assert len(dir_repo.update_calls) == 1
    call = dir_repo.update_calls[0]
    assert call.directory_id == str(created.id)
    assert call.slug == "inbox-renamed"
    assert call.display_name == "Inbox Renamed"
    assert call.description == "new description"
    assert call.image_url is None


async def test_update_directory_with_parents_replaces_at_both_repos() -> None:
    """Updating `parent_directory_ids` rewrites both stores consistently.

    Seeds an existing parent (`old-parent`), then updates the entity
    to point at a different parent (`new-parent`).  After the call:

    * the dir_repo received `set_parent_directories_of("directory",
      created.id, ["new-parent"])`,
    * the perm_repo dropped the `old-parent` relation and added the
      `new-parent` relation,
    * the original `new-parent` row stays intact.
    """
    facade, dir_repo, perm_repo, _tags = _build_facade()

    created = await facade.create_directory(
        DirectoryEntity(
            slug="child",
            display_name="Child",
            parent_directory_ids=["old-parent"],
        ),
        _UserContext("alice"),
    )

    # Snapshot pre-update: admin + one parent relation.
    pre_parents = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.DIRECTORY,
        relation=str(DirectoryRelationEnum.PARENT),
    )
    assert {str(p.subject.object_id) for p in pre_parents} == {"old-parent"}

    dir_repo.set_parent_calls.clear()
    dir_repo.update_calls.clear()

    update = DirectoryEntity(
        id=created.id,
        parent_directory_ids=["new-parent"],
    )
    await facade.update_directory(update)

    # dir_repo saw the scalar update + the parent rewrite
    assert len(dir_repo.update_calls) == 1
    assert len(dir_repo.set_parent_calls) == 1
    rewrite = dir_repo.set_parent_calls[0]
    assert rewrite.subject_type == "directory"
    assert rewrite.subject_id == str(created.id)
    assert rewrite.parent_ids == ["new-parent"]

    # perm_repo dropped old-parent, added new-parent
    post_parents = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.DIRECTORY,
        relation=str(DirectoryRelationEnum.PARENT),
    )
    assert {str(p.subject.object_id) for p in post_parents} == {"new-parent"}


async def test_update_directory_with_empty_parent_list_clears_at_both_repos() -> None:
    """Setting `parent_directory_ids=[]` clears both the dir_repo and the perm_repo."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    created = await facade.create_directory(
        DirectoryEntity(
            slug="child",
            display_name="Child",
            parent_directory_ids=["p-1", "p-2"],
        ),
        _UserContext("alice"),
    )
    assert len(_relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.DIRECTORY,
        relation=str(DirectoryRelationEnum.PARENT),
    )) == 2

    await facade.update_directory(
        DirectoryEntity(id=created.id, parent_directory_ids=[])
    )

    # dir_repo: parent set rewritten to the empty list
    rewrite = dir_repo.set_parent_calls[-1]
    assert rewrite.parent_ids == []

    # perm_repo: every parent relation for this directory is gone
    post = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.DIRECTORY,
        relation=str(DirectoryRelationEnum.PARENT),
    )
    assert all(str(p.resource.object_id) != str(created.id) for p in post)


async def test_update_directory_with_tags_calls_tag_repo() -> None:
    """Tags set on an update flow into the tag repo as `replace_tags_of`."""
    recording_tags = _RecordingTagRepo()
    facade, dir_repo, _perm_repo, tags = _build_facade(tag_repo=recording_tags)

    created = await facade.create_directory(
        DirectoryEntity(slug="inbox", display_name="Inbox"),
        _UserContext("alice"),
    )
    tags.replace_calls.clear()

    await facade.update_directory(
        DirectoryEntity(id=created.id, tag_ids=["t-9"])
    )

    assert len(tags.replace_calls) == 1
    subject_type, subject_id, tag_ids = tags.replace_calls[0]
    assert subject_type == "directory"
    assert subject_id == str(created.id)
    assert tag_ids == ["t-9"]


async def test_update_directory_requires_id() -> None:
    """Validation: no id, no writes."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    import pytest

    with pytest.raises(ValueError):
        await facade.update_directory(DirectoryEntity(slug="x"))

    assert dir_repo.update_calls == []
    assert _perm_relations(perm_repo) == []


# ---------------------------------------------------------------------------
# add_child_to_directory / remove_child_from_directory
# ---------------------------------------------------------------------------


async def test_add_child_note_writes_to_both_repos() -> None:
    """Adding a child note touches both stores with `parent_directory` semantics."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    await facade.add_child_to_directory("note", "dir-1", "note-7")

    assert dir_repo.add_child_calls == [
        _ChildCall(type="note", directory_id="dir-1", child_id="note-7")
    ]
    rels = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.NOTE,
        relation=str(NoteRelationEnum.PARENT_DIRECTORY),
    )
    assert len(rels) == 1
    assert str(rels[0].resource.object_id) == "note-7"
    assert str(rels[0].subject.object_id) == "dir-1"


async def test_add_child_directory_writes_to_both_repos() -> None:
    """Adding a child directory touches both stores with `directory#parent` semantics."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    await facade.add_child_to_directory("directory", "dir-parent", "dir-child")

    assert dir_repo.add_child_calls == [
        _ChildCall(
            type="directory",
            directory_id="dir-parent",
            child_id="dir-child",
        )
    ]
    rels = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.DIRECTORY,
        relation=str(DirectoryRelationEnum.PARENT),
    )
    assert len(rels) == 1
    assert str(rels[0].resource.object_id) == "dir-child"
    assert str(rels[0].subject.object_id) == "dir-parent"


async def test_remove_child_note_writes_to_both_repos() -> None:
    """Removing a child note drops both the hierarchy row and the perm relation."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    await facade.add_child_to_directory("note", "dir-1", "note-7")
    await facade.remove_child_from_directory("note", "dir-1", "note-7")

    assert dir_repo.remove_child_calls == [
        _ChildCall(type="note", directory_id="dir-1", child_id="note-7")
    ]
    rels = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.NOTE,
        relation=str(NoteRelationEnum.PARENT_DIRECTORY),
    )
    assert rels == []


async def test_remove_child_directory_writes_to_both_repos() -> None:
    """Removing a child directory drops the perm relation with `directory#parent`."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    await facade.add_child_to_directory("directory", "dir-parent", "dir-child")
    await facade.remove_child_from_directory("directory", "dir-parent", "dir-child")

    assert dir_repo.remove_child_calls == [
        _ChildCall(
            type="directory",
            directory_id="dir-parent",
            child_id="dir-child",
        )
    ]
    rels = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.DIRECTORY,
        relation=str(DirectoryRelationEnum.PARENT),
    )
    assert rels == []


# ---------------------------------------------------------------------------
# set_parent_directories_of (low-level facade helper)
# ---------------------------------------------------------------------------


async def test_set_parent_directories_of_note_writes_to_both_repos() -> None:
    """`set_parent_directories_of` for a note drives both the hierarchy and the perm repo."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    await facade.set_parent_directories_of(
        "note", "note-1", ["dir-a", "dir-b"]
    )

    # dir_repo: full parent rewrite on (note, note-1)
    assert len(dir_repo.set_parent_calls) == 1
    call = dir_repo.set_parent_calls[0]
    assert call.subject_type == "note"
    assert call.subject_id == "note-1"
    assert sorted(call.parent_ids) == ["dir-a", "dir-b"]

    # perm_repo: one bulk delete (clearing prior) + two inserts
    rels = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.NOTE,
        relation=str(NoteRelationEnum.PARENT_DIRECTORY),
    )
    assert len(rels) == 2
    assert {str(r.subject.object_id) for r in rels} == {"dir-a", "dir-b"}
    assert all(str(r.resource.object_id) == "note-1" for r in rels)


async def test_set_parent_directories_of_clears_existing_relations() -> None:
    """Replacing with an empty list removes every prior `parent_directory` relation.

    Seeds two parents first, then replaces them with `[]`; the
    perm_repo ends up with zero `parent_directory` relations for
    the subject note.
    """
    facade, dir_repo, perm_repo, _tags = _build_facade()

    await facade.set_parent_directories_of("note", "note-1", ["dir-a", "dir-b"])
    assert len(_relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.NOTE,
        relation=str(NoteRelationEnum.PARENT_DIRECTORY),
    )) == 2

    await facade.set_parent_directories_of("note", "note-1", [])

    rels = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.NOTE,
        relation=str(NoteRelationEnum.PARENT_DIRECTORY),
    )
    assert rels == []

    last_write = dir_repo.set_parent_calls[-1]
    assert last_write.subject_id == "note-1"
    assert last_write.parent_ids == []


# ---------------------------------------------------------------------------
# delete_directory
# ---------------------------------------------------------------------------


async def test_delete_directory_only_touches_dir_repo() -> None:
    """`delete_directory` does not clean up SpiceDB relations itself.

    The facade contract is explicit: "delete the directory row
    (cleanup is the caller's job)".  This test pins that contract
    so the dual-write semantics of `create_directory` are not
    accidentally mirrored on the delete path.
    """
    facade, dir_repo, perm_repo, _tags = _build_facade()

    created = await facade.create_directory(
        DirectoryEntity(slug="x", display_name="X"),
        _UserContext("alice"),
    )
    assert len(_perm_relations(perm_repo)) == 1  # admin relation from create

    await facade.delete_directory(DirectoryEntity(id=created.id))

    assert dir_repo.delete_calls == [str(created.id)]
    # admin relation survives -- caller is responsible for cleanup
    assert len(_perm_relations(perm_repo)) == 1


async def test_delete_directory_requires_id() -> None:
    """Validation: no id, no writes."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    import pytest

    with pytest.raises(ValueError):
        await facade.delete_directory(DirectoryEntity(slug="x"))

    assert dir_repo.delete_calls == []
    assert _perm_relations(perm_repo) == []


# ---------------------------------------------------------------------------
# fetch_directory -- read path, no writes
# ---------------------------------------------------------------------------


async def test_fetch_directory_only_reads_dir_repo() -> None:
    """Read-only path: no perm_repo or tag_repo traffic."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    created = await facade.create_directory(
        DirectoryEntity(slug="x", display_name="X"),
        _UserContext("alice"),
    )
    perm_snapshot = _perm_relations(perm_repo)

    result = await facade.fetch_directory(created.id)

    assert result is not None
    assert str(result.id) == str(created.id)
    # fetch is the only dir_repo call after create
    assert len(dir_repo.fetch_calls) == 1
    assert dir_repo.fetch_calls[0][0] == str(created.id)
    # perm_repo unchanged
    assert _perm_relations(perm_repo) == perm_snapshot


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _expect_value_error:
    """Tiny context manager that asserts a :exc:`ValueError` with a substring.

    Equivalent to ``pytest.raises(ValueError, match=...)`` but lets
    the assertions read top-to-bottom without an extra import in the
    helper section above.
    """

    def __init__(self, needle: str) -> None:
        self._needle = needle

    def __enter__(self) -> "_expect_value_error":
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        assert exc_type is ValueError, f"expected ValueError, got {exc_type!r}: {exc!r}"
        assert self._needle in str(exc), (
            f"expected {self._needle!r} in {str(exc)!r}"
        )
        return True