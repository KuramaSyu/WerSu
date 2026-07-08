import pytest

pytestmark = pytest.mark.integration
from datetime import datetime

from tests.stubs.user_context import _UserContext as UserContext
from src.api.note_facade import NoteRepoFacadeABC
from src.db.entities.note.metadata import NoteEntity
from src.api import (
    ObjectRef,
    Relationship,
    SubjectRef,
)
from src.db.repos.user.user import UserRepoABC
from src.db.entities.user.user import UserEntity


async def test_global_sharelink_allows_access(note_repo_facade, user_repo, test_user):
    # create owner user
    owner = await user_repo.insert(test_user)

    # insert a note as owner
    note_entity = NoteEntity(
        title="Shared Note",
        content="secret",
        author_id=owner.id,
        updated_at=datetime.now()
    )
    created = await note_repo_facade.insert(note_entity, UserContext(owner.id))
    assert created.note_id is not None

    # create a global share user id and insert a sharelink relation (reader)
    global_user_id = "[global]public-1"

    share_rel = Relationship(
        resource=ObjectRef(object_type="note", object_id=created.note_id),
        relation="reader",
        subject=SubjectRef(object_type="user", object_id=global_user_id),
    )

    # public user can not yet access it
    can_view = await note_repo_facade._permission_repo.has_permission(
        user=UserContext(global_user_id),
        permission="view",
        resource=ObjectRef(object_type="note", object_id=created.note_id),
    )
    assert can_view is False

    # insert the share relation into the permission backend used by the facade
    await note_repo_facade._permission_repo.insert([share_rel])

    # the global user should be able to view the note
    can_view = await note_repo_facade._permission_repo.has_permission(
        user=UserContext(global_user_id),
        permission="view",
        resource=ObjectRef(object_type="note", object_id=created.note_id),
    )
    assert can_view is True
