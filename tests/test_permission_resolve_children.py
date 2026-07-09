"""Unit tests for :meth:`PermissionRepoABC.resolve_children`.

Pins the algorithm that walks a directory subtree and collects the
ids of every exclusively-owned child directory / note / attachment.
The algorithm runs on top of both SpiceDB and the in-memory permission
repo; this file covers the in-memory path because the live backend
requires Docker containers (see ``tests/integration/`` for the
SpiceDB-level coverage).
"""

from __future__ import annotations

import pytest

from src.api.relationship import (
    AttachmentRelationEnum,
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.db.repos.permissions.permission import NotePermissionRepoInMemory


@pytest.mark.asyncio
async def test_resolve_children_walks_subdirectories_recursively() -> None:
    """``directory#parent`` is followed recursively."""
    repo = NotePermissionRepoInMemory()
    # root -> child -> grandchild
    await repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
                relation=DirectoryRelationEnum.PARENT,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-3"),
                relation=DirectoryRelationEnum.PARENT,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
            ),
        ]
    )

    result = await repo.resolve_children("dir-1", exclusive=False)
    assert result.sub_directory_ids == ["dir-1", "dir-2", "dir-3"]
    assert result.note_ids == []
    assert result.attachment_ids == []


@pytest.mark.asyncio
async def test_resolve_children_collects_notes_per_directory() -> None:
    """Notes linked via ``note#parent_directory`` are returned."""
    repo = NotePermissionRepoInMemory()
    await repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-2"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
            ),
        ]
    )
    await repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
                relation=DirectoryRelationEnum.PARENT,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            )
        ]
    )

    result = await repo.resolve_children("dir-1", exclusive=False)
    assert result.sub_directory_ids == ["dir-1", "dir-2"]
    assert sorted(result.note_ids) == ["note-1", "note-2"]


@pytest.mark.asyncio
async def test_resolve_children_collects_attachments_via_notes() -> None:
    """Attachments under any note in the subtree are returned."""
    repo = NotePermissionRepoInMemory()
    await repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, "att-1"),
                relation=AttachmentRelationEnum.PARENT_NOTE,
                subject=SubjectRef(ObjectTypeEnum.NOTE, "note-1"),
            ),
        ]
    )

    result = await repo.resolve_children("dir-1", exclusive=False)
    assert result.sub_directory_ids == ["dir-1"]
    assert result.note_ids == ["note-1"]
    assert result.attachment_ids == ["att-1"]


@pytest.mark.asyncio
async def test_resolve_children_exclusive_drops_notes_with_parents_outside() -> None:
    """A note parented under both the subtree and an outside directory
    is dropped because it is not exclusively in this subtree."""
    repo = NotePermissionRepoInMemory()
    await repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "shared"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "shared"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "outside"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "exclusive"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
        ]
    )

    exclusive = await repo.resolve_children("dir-1", exclusive=True)
    assert exclusive.note_ids == ["exclusive"]

    nonex = await repo.resolve_children("dir-1", exclusive=False)
    assert sorted(nonex.note_ids) == ["exclusive", "shared"]


@pytest.mark.asyncio
async def test_resolve_children_exclusive_drops_attachments_with_parents_outside() -> None:
    """An attachment parented under both an in-subtree note and an
    outside note is dropped."""
    repo = NotePermissionRepoInMemory()
    await repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "in-tree"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "outside-note"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),  # outside subtree
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, "shared"),
                relation=AttachmentRelationEnum.PARENT_NOTE,
                subject=SubjectRef(ObjectTypeEnum.NOTE, "in-tree"),
            ),
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, "shared"),
                relation=AttachmentRelationEnum.PARENT_NOTE,
                subject=SubjectRef(ObjectTypeEnum.NOTE, "outside-note"),
            ),
        ]
    )

    exclusive = await repo.resolve_children("dir-1", exclusive=True)
    assert exclusive.attachment_ids == []

    nonex = await repo.resolve_children("dir-1", exclusive=False)
    assert nonex.attachment_ids == ["shared"]


@pytest.mark.asyncio
async def test_resolve_children_max_depth_caps_walk() -> None:
    """``max_depth=0`` returns only the root directory."""
    repo = NotePermissionRepoInMemory()
    await repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-2"),
                relation=DirectoryRelationEnum.PARENT,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
            ),
        ]
    )

    result = await repo.resolve_children("dir-1", max_depth=0)
    assert result.sub_directory_ids == ["dir-1"]
    assert result.note_ids == []
    assert result.attachment_ids == []


@pytest.mark.asyncio
async def test_resolve_children_negative_depth_raises() -> None:
    repo = NotePermissionRepoInMemory()
    with pytest.raises(ValueError, match="max_depth"):
        await repo.resolve_children("dir-1", max_depth=-1)