"""Unit tests for permission service authorization and relationship mutations."""

from typing import List, Optional

import pytest

from src.api.types import Pagination
from src.api.user_context import UserContextABC
from src.db.entities import DirectoryEntity, NoteEntity
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.note import NoteRepoFacadeABC, SearchType, UserContext
from src.db.repos.note.permission import (
    DirectoryRelationEnum,
    NotePermissionRepoInMemory,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.services.roles import PermissionServiceRepo


class _StubNoteRepo(NoteRepoFacadeABC):
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

    async def search_notes(
        self,
        search_type: SearchType,
        query: str,
        ctx: UserContext,
        pagination: Pagination,
    ) -> List[NoteEntity]:
        raise NotImplementedError()


class _StubDirectoryRepo(DirectoryRepo):
    """Minimal directory repo stub implementing only fetch for existence checks."""

    def __init__(self, directory_ids: set[str]) -> None:
        self._directory_ids = directory_ids

    async def create_directory(self, entity: DirectoryEntity) -> DirectoryEntity:
        raise NotImplementedError()

    async def fetch_directory(self, id: str) -> Optional[DirectoryEntity]:
        if id in self._directory_ids:
            return DirectoryEntity(id=id, name="test")
        return None

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        raise NotImplementedError()

    async def list_note_directory_ids(self, note_id: str) -> List[str]:
        raise NotImplementedError()

    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        raise NotImplementedError()


async def test_permission_service_create_and_list_note_relationships() -> None:
    """Creates a note relation and verifies listing returns all stored relations."""

    permission_repo = NotePermissionRepoInMemory()
    service = PermissionServiceRepo(
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

    permission_repo = NotePermissionRepoInMemory()
    service = PermissionServiceRepo(
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

    permission_repo = NotePermissionRepoInMemory()
    service = PermissionServiceRepo(
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
