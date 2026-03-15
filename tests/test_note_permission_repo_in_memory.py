from src.db.repos.note.note import UserContext
from src.db.repos.note.permission import (
    NotePermissionRepoInMemory,
    ObjectRef,
    Relationship,
    SubjectRef,
)


async def test_in_memory_has_permission_with_implied_permissions() -> None:
    repo = NotePermissionRepoInMemory()
    user = UserContext("emilia")
    note = ObjectRef(object_type="note", object_id="note-1")

    await repo.insert(
        [
            Relationship(
                resource=note,
                relation="admin",
                subject=SubjectRef(object_type="user", object_id=user.user_id),
            )
        ]
    )

    assert await repo.has_permission(user=user, permission="admin", resource=note)
    assert await repo.has_permission(user=user, permission="delete", resource=note)
    assert await repo.has_permission(user=user, permission="write", resource=note)
    assert await repo.has_permission(user=user, permission="view", resource=note)


async def test_in_memory_get_permissions_returns_unique_union() -> None:
    repo = NotePermissionRepoInMemory()
    user = UserContext("alfred")
    note = ObjectRef(object_type="note", object_id="note-2")

    await repo.insert(
        [
            Relationship(
                resource=note,
                relation="reader",
                subject=SubjectRef(object_type="user", object_id=user.user_id),
            ),
            Relationship(
                resource=note,
                relation="writer",
                subject=SubjectRef(object_type="user", object_id=user.user_id),
            ),
        ]
    )

    permissions = await repo.get_permissions(user=user, resource=note)

    # reader + writer should imply these effective permissions for notes.
    assert permissions == ["reader", "view", "write", "writer"]
