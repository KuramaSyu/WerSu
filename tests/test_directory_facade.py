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
:class:`DirectoryRepoABC` contract (typed parent/child args on
:meth:`set_parents_of`) so any mismatch between the facade's
call signature and the repo contract surfaces as a
:exc:`TypeError` at test time.
"""

from __future__ import annotations
from dataclasses import dataclass
from src.api.other.relationship import DirectoryRelationEnum, NoteRelationEnum, ObjectRef, ObjectTypeEnum, Relationship, SubjectRef
from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.api.repos.directory_repo import DirectoryChildType, DirectoryHierarchyType, DirectoryParentType, DirectoryRepoABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.api.repos.tag_repo import TagRepoABC
from src.api.services.directory_service import DirectoryIncludeOptions
from src.db.entities.directory.directory import DirectoryEntity
from src.db.repos.directory.directory_facade import DirectoryFacadeImpl
from tests.doubles import InMemoryPermissionRepo
from tests.doubles import _DummyShelfRepo
from tests.doubles import _DummyUserContext
from typing import Any, Dict, List, Optional, Set, Tuple
import pytest

from tests.doubles.spys.directory_facade import _RecordingDirectoryRepo, _RecordingTagRepo, _InsertCall, _UpdateCall, _SetParentCall, _ChildCall
from tests.doubles.mocks.directory_facade import _expect_value_error


# ---------------------------------------------------------------------------
# Recording fakes
# ---------------------------------------------------------------------------














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
    shelf_repo: Optional[ShelfRepoABC] = None,
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
    shelves = shelf_repo if shelf_repo is not None else _DummyShelfRepo()
    facade = DirectoryFacadeImpl(
        directory_repo=dir_repo,
        permission_repo=perm_repo,
        tag_repo=tags,
        log=lambda *_args, **_kwargs: None,
        shelf_repo=shelves,
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
    await facade.create_directory(entity, _DummyUserContext("alice"))

    assert len(dir_repo.insert_calls) == 1
    call = dir_repo.insert_calls[0]
    assert call.slug == "inbox"
    assert call.display_name == "Inbox"
    assert call.description == "notes that need a home"


async def test_create_directory_inserts_admin_relation_in_perm_repo() -> None:
    """`create_directory` writes a `dir#admin@user` relation for the creator."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    entity = DirectoryEntity(slug="inbox", display_name="Inbox")
    created = await facade.create_directory(entity, _DummyUserContext("alice"))

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
    created = await facade.create_directory(entity, _DummyUserContext("alice"))

    # dir_repo: row insert + parent hierarchy write via set_parents_of
    assert len(dir_repo.insert_calls) == 1
    assert len(dir_repo.set_parent_calls) == 1
    parent_write = dir_repo.set_parent_calls[0]
    assert parent_write.child_type == "directory"
    assert parent_write.child_id == str(created.id)
    assert parent_write.parent_type == "directory"
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
    await facade.create_directory(entity, _DummyUserContext("alice"))

    assert len(tags.replace_calls) == 1
    subject_type, subject_id, tag_ids = tags.replace_calls[0]
    assert subject_type == "directory"
    # The tag repo gets the id the dir_repo assigned on insert.
    assert subject_id == list(dir_repo.entities.keys())[0]
    assert tag_ids == ["t-1", "t-2"]


# ---------------------------------------------------------------------------
# update_directory
# ---------------------------------------------------------------------------


async def test_update_directory_writes_scalar_fields_to_dir_repo() -> None:
    """Scalar updates reach `dir_repo.update_directory` with the right kwargs."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    # seed a directory to update
    created = await facade.create_directory(
        DirectoryEntity(slug="inbox", display_name="Inbox"),
        _DummyUserContext("alice"),
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

    * the dir_repo received `set_parents_of("directory",
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
        _DummyUserContext("alice"),
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
    assert rewrite.child_type == "directory"
    assert rewrite.child_id == str(created.id)
    assert rewrite.parent_type == "directory"
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
        _DummyUserContext("alice"),
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
        _DummyUserContext("alice"),
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


    with pytest.raises(ValueError, match="Directory ID is required for update"):
        await facade.update_directory(DirectoryEntity(slug="x"))

    assert dir_repo.update_calls == []
    assert _perm_relations(perm_repo) == []


# ---------------------------------------------------------------------------
# add_child_to / remove_child_from
# ---------------------------------------------------------------------------


async def test_add_child_note_writes_to_both_repos() -> None:
    """Adding a child note touches both stores with `parent_directory` semantics."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    await facade.add_child_to("directory", "dir-1", "note", "note-7")

    assert dir_repo.add_child_calls == [
        _ChildCall(parent_type="directory", parent_id="dir-1", child_type="note", child_id="note-7")
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

    await facade.add_child_to("directory", "dir-parent", "directory", "dir-child")

    assert dir_repo.add_child_calls == [
        _ChildCall(
            parent_type="directory",
            parent_id="dir-parent",
            child_type="directory",
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

    await facade.add_child_to("directory", "dir-1", "note", "note-7")
    await facade.remove_child_from("directory", "dir-1", "note", "note-7")

    assert dir_repo.remove_child_calls == [
        _ChildCall(parent_type="directory", parent_id="dir-1", child_type="note", child_id="note-7")
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

    await facade.add_child_to("directory", "dir-parent", "directory", "dir-child")
    await facade.remove_child_from("directory", "dir-parent", "directory", "dir-child")

    assert dir_repo.remove_child_calls == [
        _ChildCall(
            parent_type="directory",
            parent_id="dir-parent",
            child_type="directory",
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
# set_parents_of (low-level facade helper)
# ---------------------------------------------------------------------------


async def test_set_parents_of_note_writes_to_both_repos() -> None:
    """`set_parents_of` for a note drives both the hierarchy and the perm repo."""
    facade, dir_repo, perm_repo, _tags = _build_facade()

    await facade.set_parents_of(
        "note", "note-1", "directory", ["dir-a", "dir-b"]
    )

    # dir_repo: full parent rewrite on (note, note-1)
    assert len(dir_repo.set_parent_calls) == 1
    call = dir_repo.set_parent_calls[0]
    assert call.child_type == "note"
    assert call.child_id == "note-1"
    assert call.parent_type == "directory"
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


async def test_set_parents_of_clears_existing_relations() -> None:
    """Replacing with an empty list removes every prior `parent_directory` relation.

    Seeds two parents first, then replaces them with `[]`; the
    perm_repo ends up with zero `parent_directory` relations for
    the subject note.
    """
    facade, dir_repo, perm_repo, _tags = _build_facade()

    await facade.set_parents_of("note", "note-1", "directory", ["dir-a", "dir-b"])
    assert len(_relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.NOTE,
        relation=str(NoteRelationEnum.PARENT_DIRECTORY),
    )) == 2

    await facade.set_parents_of("note", "note-1", "directory", [])

    rels = _relations_of(
        perm_repo,
        resource_type=ObjectTypeEnum.NOTE,
        relation=str(NoteRelationEnum.PARENT_DIRECTORY),
    )
    assert rels == []

    last_write = dir_repo.set_parent_calls[-1]
    assert last_write.child_id == "note-1"
    assert last_write.child_type == "note"
    assert last_write.parent_type == "directory"
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
        _DummyUserContext("alice"),
    )
    assert len(_perm_relations(perm_repo)) == 1  # admin relation from create

    await facade.delete_directory(DirectoryEntity(id=created.id))

    assert dir_repo.delete_calls == [str(created.id)]
    # admin relation survives -- caller is responsible for cleanup
    assert len(_perm_relations(perm_repo)) == 1


async def test_delete_directory_requires_id() -> None:
    """Validation: no id, no writes."""
    facade, dir_repo, perm_repo, _tags = _build_facade()


    with pytest.raises(ValueError, match="Directory ID is required for deletion"):
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
        _DummyUserContext("alice"),
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
# shelf_ids -- read path enrichment
# ---------------------------------------------------------------------------


async def test_fetch_directory_default_omits_shelf_ids() -> None:
    """Without ``include_shelves`` the shelf repo is not consulted."""
    class _CountingShelfRepo(_DummyShelfRepo):
        def __init__(self) -> None:
            super().__init__()
            self.get_shelves_of_book_calls = 0

        async def get_shelves_of_book(self, book_id):
            self.get_shelves_of_book_calls += 1
            return await super().get_shelves_of_book(book_id)

    shelf_repo = _CountingShelfRepo()
    facade, dir_repo, _perm, _tags = _build_facade(shelf_repo=shelf_repo)

    created = await facade.create_directory(
        DirectoryEntity(slug="x", display_name="X"),
        _DummyUserContext("alice"),
    )

    # Default include options: shelf lookup must NOT happen.
    result = await facade.fetch_directory(created.id)
    assert result is not None
    assert shelf_repo.get_shelves_of_book_calls == 0
    # The entity's shelf_ids stays at the UNDEFINED default;
    # gRPC visitors check `if entity.shelf_ids:` so an
    # undefined placeholder doesn't leak into the proto.
    assert result.shelf_ids in (UNDEFINED, None) or not result.shelf_ids


async def test_fetch_directory_with_include_shelves_populates_shelf_ids() -> None:
    """With ``include_shelves=True`` the facade asks the shelf repo."""
    class _StubShelfRepo(_DummyShelfRepo):
        def __init__(self, shelves_for: dict[str, list[str]]) -> None:
            super().__init__()
            self._shelves_for = shelves_for
            self.calls: list[str] = []

        async def get_shelves_of_book(self, book_id):
            self.calls.append(book_id)
            return list(self._shelves_for.get(book_id, []))

    shelves_by_book = {
        "book-1": ["shelf-A", "shelf-B"],
        "book-2": ["shelf-C"],
    }
    shelf_repo = _StubShelfRepo(shelves_by_book)
    facade, _dir_repo, _perm, _tags = _build_facade(shelf_repo=shelf_repo)

    # Insert the directory.
    created = await facade.create_directory(
        DirectoryEntity(slug="x", display_name="X"),
        _DummyUserContext("alice"),
    )
    # Map the dir_repo's auto-id to "book-1" via the recording
    # fake: simpler to just patch the shelf repo after the
    # fact.  We mimic that by re-pointing the fake's dict.
    shelves_by_book[str(created.id)] = shelves_by_book.pop("book-1")

    result = await facade.fetch_directory(
        created.id,
        include={"include_shelves": True},
    )
    assert result is not None
    assert result.shelf_ids == ["shelf-A", "shelf-B"]
    assert shelf_repo.calls == [str(created.id)]


async def test_fetch_directory_shelf_lookup_failure_yields_empty_list() -> None:
    """When the shelf repo raises, the entity gets an empty shelf_ids list."""

    class _RaisingShelfRepo(_DummyShelfRepo):
        async def get_shelves_of_book(self, book_id):
            raise RuntimeError("kaboom")

    shelf_repo = _RaisingShelfRepo()
    facade, _dir_repo, _perm, _tags = _build_facade(shelf_repo=shelf_repo)

    created = await facade.create_directory(
        DirectoryEntity(slug="x", display_name="X"),
        _DummyUserContext("alice"),
    )

    result = await facade.fetch_directory(
        created.id,
        include={"include_shelves": True},
    )
    assert result is not None
    assert result.shelf_ids == []


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
