"""Unit tests for permission service authorization and relationship mutations."""

from typing import List, Optional, Tuple

import pytest

from tests.stubs.user_context import _UserContext as UserContext
from src.api.other.types import Pagination
from src.api.other.user_context import UserContextABC
from src.db.entities import DirectoryEntity, NoteEntity
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.repos.directory_repo import (
    DirectoryChildType,
    DirectoryHierarchyType,
)
from src.api.facades.note_facade import NoteFacadeABC, SearchType
from src.api import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.services.permissions import PermissionServiceImpl
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo


class _StubNoteRepo(NoteFacadeABC):
    """Minimal note repo stub implementing only selection for existence checks."""

    def __init__(self, note_ids: set[str]) -> None:
        self._note_ids = note_ids

    async def insert(self, note: NoteEntity, user: UserContextABC) -> NoteEntity:
        raise NotImplementedError()

    async def update(self, note: NoteEntity, ctx: UserContext) -> NoteEntity:
        raise NotImplementedError()

    async def delete(self, note_id: str, ctx: UserContext) -> Optional[List[NoteEntity]]:
        raise NotImplementedError()

    async def select_by_id(self, note_id: str, ctx: UserContext) -> Optional[NoteEntity]:
        if note_id in self._note_ids:
            return NoteEntity(note_id=note_id)
        return None

    async def select_by_ids(
        self,
        note_ids: List[str],
        ctx: UserContext,
    ) -> List[NoteEntity]:
        return [
            NoteEntity(note_id=nid)
            for nid in note_ids
            if nid in self._note_ids
        ]

    async def search_notes(
        self,
        search_type: SearchType,
        query: str,
        ctx: UserContext,
        pagination: Pagination,
    ) -> List[NoteEntity]:
        raise NotImplementedError()


class _StubDirectoryRepo(DirectoryFacadeABC):
    """Minimal directory repo stub implementing only fetch for existence checks."""

    def __init__(self, directory_ids: set[str]) -> None:
        self._directory_ids = directory_ids

    async def create_directory(self, entity: DirectoryEntity) -> DirectoryEntity:
        raise NotImplementedError()

    async def fetch_directory(self, id: str) -> Optional[DirectoryEntity]:
        if id in self._directory_ids:
            return DirectoryEntity(id=id, slug="test")
        return None

    async def update_directory(self, entity: DirectoryEntity) -> Optional[DirectoryEntity]:
        raise NotImplementedError()

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        raise NotImplementedError()

    async def fetch_all_directories(self) -> List[DirectoryEntity]:
        raise NotImplementedError()

    async def list_note_directory_ids(self, note_id: str) -> List[str]:
        raise NotImplementedError()

    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        raise NotImplementedError()

    async def resolve_files_of_directory(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
    ) -> List[str]:
        return []

    async def resolve_subtree(
        self,
        directory_id: str,
        max_depth: int = 10,
    ) -> Tuple[List[str], List[str]]:
        return ([], [directory_id])

    async def add_note_to_directory(self, note_id: str, directory_id: str) -> None:
        """No-op recording stub."""
        return None

    async def remove_note_from_directory(self, note_id: str, directory_id: str) -> None:
        """No-op recording stub."""
        return None

    # ---- DirectoryHelperMixin: hierarchy helpers (no-op stubs) ------

    async def set_parent_directories_of(
        self,
        directory_id: str,
        parent_ids: List[str],
    ) -> None:
        return None

    async def get_parent_of(
        self,
        type: DirectoryHierarchyType,
        child_id: str,
    ) -> List[str]:
        return []

    async def get_children_of(
        self,
        type: DirectoryHierarchyType,
        directory_id: str,
        depth: int = 1,
    ) -> List[str]:
        return []

    async def get_children_for(
        self,
        type: DirectoryHierarchyType,
        directory_ids: List[str],
        depth: int = 1,
    ) -> List[str]:
        return []

    async def get_parent_for(
        self,
        type: DirectoryHierarchyType,
        child_ids: List[str],
    ) -> List[str]:
        return []

    async def add_child_to_directory(
        self,
        type: DirectoryChildType,
        directory_id: str,
        child_id: str,
    ) -> None:
        return None

    async def remove_child_from_directory(
        self,
        type: DirectoryChildType,
        directory_id: str,
        child_id: str,
    ) -> None:
        return None


async def test_permission_service_create_and_list_note_relationships() -> None:
    """Creates a note relation and verifies listing returns all stored relations."""

    permission_repo = InMemoryPermissionRepo()
    service = PermissionServiceImpl(
        permission_repo=permission_repo,
        note_repo=_StubNoteRepo(note_ids={"note-1"}),
        directory_repo=_StubDirectoryRepo(directory_ids=set()),
    )

    actor = UserContext("owner")
    resource = ObjectRef(object_type=ObjectTypeEnum.NOTE, object_id="note-1")

    # relation, that actor is owner of note-1
    owner_relationship = Relationship(
        resource=resource,
        relation=NoteRelationEnum.OWNER,
        subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id=actor.user_id),
    )
    await permission_repo.insert([owner_relationship])

    # Add a second relation through the service API.
    created = await service.create_relationship(
        relationship=Relationship(
            resource=resource,
            relation=NoteRelationEnum.READER,
            subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id="reader-user"),
        ),
        actor=actor,
    )

    actual = {
        (str(rel.relation), str(rel.subject.object_type), str(rel.subject.object_id))
        for rel in created
    }
    assert actual == {
        ("owner", "user", "owner"),
        ("reader", "user", "reader-user"),
    }

    # Listing should return the same effective direct relationship set.
    listed = await service.list_relationships(resource=resource, actor=actor)
    listed_tuples = {
        (str(rel.relation), str(rel.subject.object_type), str(rel.subject.object_id))
        for rel in listed
    }
    assert listed_tuples == actual


async def test_permission_service_denies_actor_without_manage_permission() -> None:
    """Rejects relation changes when actor has only read-level access."""

    permission_repo = InMemoryPermissionRepo()
    service = PermissionServiceImpl(
        permission_repo=permission_repo,
        note_repo=_StubNoteRepo(note_ids={"note-2"}),
        directory_repo=_StubDirectoryRepo(directory_ids=set()),
    )

    actor = UserContext("reader-user")
    resource = ObjectRef(object_type=ObjectTypeEnum.NOTE, object_id="note-2")

    # Reader can view but must not be allowed to mutate permissions.
    await permission_repo.insert(
        [
            Relationship(
                resource=resource,
                relation=NoteRelationEnum.READER,
                subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id=actor.user_id),
            )
        ]
    )

    with pytest.raises(PermissionError):
        await service.create_relationship(
            relationship=Relationship(
                resource=resource,
                relation=NoteRelationEnum.WRITER,
                subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id="another-user"),
            ),
            actor=actor,
        )


async def test_permission_service_replace_directory_relationships() -> None:
    """Replaces directory relations by removing stale entries and adding new ones."""

    permission_repo = InMemoryPermissionRepo()
    service = PermissionServiceImpl(
        permission_repo=permission_repo,
        note_repo=_StubNoteRepo(note_ids=set()),
        directory_repo=_StubDirectoryRepo(directory_ids={"dir-1"}),
    )

    actor = UserContext("dir-admin")
    resource = ObjectRef(object_type=ObjectTypeEnum.DIRECTORY, object_id="dir-1")

    admin_rel = Relationship(
        resource=resource,
        relation=DirectoryRelationEnum.ADMIN,
        subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id=actor.user_id),
    )
    reader_rel = Relationship(
        resource=resource,
        relation=DirectoryRelationEnum.READER,
        subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id="member"),
    )
    # Initial state: admin + reader.
    await permission_repo.insert([admin_rel, reader_rel])

    # Desired state replaces reader with writer for the same member.
    updated = await service.replace_relationships(
        resource=resource,
        relationships=[
            admin_rel,
            Relationship(
                resource=resource,
                relation=DirectoryRelationEnum.WRITER,
                subject=SubjectRef(object_type=ObjectTypeEnum.USER, object_id="member"),
            ),
        ],
        actor=actor,
    )

    actual = {
        (str(rel.relation), str(rel.subject.object_type), str(rel.subject.object_id))
        for rel in updated
    }
    assert actual == {
        ("admin", "user", "dir-admin"),
        ("writer", "user", "member"),
    }


async def test_create_relationship_rejects_note_parent_directory_tuple() -> None:
    """``note#parent_directory@directory`` is a hierarchy write, not a permission."""
    permission_repo = InMemoryPermissionRepo()
    service = PermissionServiceImpl(
        permission_repo=permission_repo,
        note_repo=_StubNoteRepo(note_ids={"note-1"}),
        directory_repo=_StubDirectoryRepo(directory_ids={"dir-1"}),
    )

    actor = UserContext("admin")
    await permission_repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
            relation=NoteRelationEnum.ADMIN,
            subject=SubjectRef(ObjectTypeEnum.USER, actor.user_id),
        )
    ])

    with pytest.raises(ValueError, match="directory or note patch"):
        await service.create_relationship(
            relationship=Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            actor=actor,
        )


async def test_create_relationship_rejects_directory_parent_tuple() -> None:
    """``directory#parent@directory`` is a hierarchy write, not a permission."""
    permission_repo = InMemoryPermissionRepo()
    service = PermissionServiceImpl(
        permission_repo=permission_repo,
        note_repo=_StubNoteRepo(note_ids=set()),
        directory_repo=_StubDirectoryRepo(directory_ids={"dir-2"}),
    )

    actor = UserContext("admin")
    await permission_repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
            relation=DirectoryRelationEnum.ADMIN,
            subject=SubjectRef(ObjectTypeEnum.USER, actor.user_id),
        )
    ])

    with pytest.raises(ValueError, match="directory or note patch"):
        await service.create_relationship(
            relationship=Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
                relation=DirectoryRelationEnum.PARENT,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            actor=actor,
        )


async def test_delete_relationship_rejects_structural_tuples() -> None:
    """``delete_relationship`` also rejects structural tuples."""
    permission_repo = InMemoryPermissionRepo()
    service = PermissionServiceImpl(
        permission_repo=permission_repo,
        note_repo=_StubNoteRepo(note_ids={"note-1"}),
        directory_repo=_StubDirectoryRepo(directory_ids={"dir-1"}),
    )

    actor = UserContext("admin")
    await permission_repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
            relation=NoteRelationEnum.ADMIN,
            subject=SubjectRef(ObjectTypeEnum.USER, actor.user_id),
        )
    ])

    with pytest.raises(ValueError, match="directory or note patch"):
        await service.delete_relationship(
            relationship=Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            actor=actor,
        )


async def test_replace_relationships_rejects_structural_tuple_in_desired_set() -> None:
    """Forbidden tuple in the desired set fails the whole replace."""
    permission_repo = InMemoryPermissionRepo()
    service = PermissionServiceImpl(
        permission_repo=permission_repo,
        note_repo=_StubNoteRepo(note_ids={"note-1"}),
        directory_repo=_StubDirectoryRepo(directory_ids={"dir-1"}),
    )

    actor = UserContext("admin")
    admin_rel = Relationship(
        resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
        relation=NoteRelationEnum.ADMIN,
        subject=SubjectRef(ObjectTypeEnum.USER, actor.user_id),
    )
    await permission_repo.insert([admin_rel])

    forbidden = Relationship(
        resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
        relation=NoteRelationEnum.PARENT_DIRECTORY,
        subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
    )

    with pytest.raises(ValueError, match="directory or note patch"):
        await service.replace_relationships(
            resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
            relationships=[admin_rel, forbidden],
            actor=actor,
        )


async def test_replace_relationships_rejects_structural_tuple_already_stored() -> None:
    """Forbidden tuple already on the resource also fails the whole replace.

    Even when the desired set doesn't contain the structural tuple,
    its presence in storage should make the call fail.  Otherwise a
    caller could silently drop it via a diff.
    """
    permission_repo = InMemoryPermissionRepo()
    service = PermissionServiceImpl(
        permission_repo=permission_repo,
        note_repo=_StubNoteRepo(note_ids={"note-1"}),
        directory_repo=_StubDirectoryRepo(directory_ids={"dir-1"}),
    )

    actor = UserContext("admin")
    admin_rel = Relationship(
        resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
        relation=NoteRelationEnum.ADMIN,
        subject=SubjectRef(ObjectTypeEnum.USER, actor.user_id),
    )
    structural = Relationship(
        resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
        relation=NoteRelationEnum.PARENT_DIRECTORY,
        subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
    )
    await permission_repo.insert([admin_rel, structural])

    with pytest.raises(ValueError, match="directory or note patch"):
        await service.replace_relationships(
            resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
            relationships=[admin_rel],
            actor=actor,
        )


async def test_replace_relationships_directory_parent_rejected() -> None:
    """``directory#parent@directory`` is rejected in replace too."""
    permission_repo = InMemoryPermissionRepo()
    service = PermissionServiceImpl(
        permission_repo=permission_repo,
        note_repo=_StubNoteRepo(note_ids=set()),
        directory_repo=_StubDirectoryRepo(directory_ids={"dir-2"}),
    )

    actor = UserContext("admin")
    admin_rel = Relationship(
        resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
        relation=DirectoryRelationEnum.ADMIN,
        subject=SubjectRef(ObjectTypeEnum.USER, actor.user_id),
    )
    forbidden = Relationship(
        resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
        relation=DirectoryRelationEnum.PARENT,
        subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
    )
    await permission_repo.insert([admin_rel])

    with pytest.raises(ValueError, match="directory or note patch"):
        await service.replace_relationships(
            resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
            relationships=[admin_rel, forbidden],
            actor=actor,
        )


async def test_create_relationship_still_allows_user_roles() -> None:
    """Sanity: the regular permission tuples (admin/reader/writer/owner) still pass."""
    permission_repo = InMemoryPermissionRepo()
    service = PermissionServiceImpl(
        permission_repo=permission_repo,
        note_repo=_StubNoteRepo(note_ids={"note-1"}),
        directory_repo=_StubDirectoryRepo(directory_ids=set()),
    )

    actor = UserContext("admin")
    await permission_repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
            relation=NoteRelationEnum.ADMIN,
            subject=SubjectRef(ObjectTypeEnum.USER, actor.user_id),
        )
    ])

    created = await service.create_relationship(
        relationship=Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
            relation=NoteRelationEnum.READER,
            subject=SubjectRef(ObjectTypeEnum.USER, "alice"),
        ),
        actor=actor,
    )

    assert any(
        str(rel.relation) == "reader" and str(rel.subject.object_id) == "alice"
        for rel in created
    )
