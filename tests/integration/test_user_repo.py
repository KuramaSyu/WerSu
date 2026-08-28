from dataclasses import replace
from datetime import datetime
from typing import AsyncGenerator, Optional
from uuid import UUID
import pytest
from testcontainers.postgres import PostgresContainer
from tests.stubs.user_context import _UserContext as UserContext
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.user.user import UserEntity
from src.api.facades.note_facade import NoteFacadeABC
from src.db.repos.user.user import UserRepoABC
import src.api
from src.db.repos import UserPostgresRepo, Database
from src.utils import logging_provider

# import fixtures, otherise pytest will not detect them
from tests.fixtures import db, note_repo_facade, user_repo, note_repo_facade, dsn, test_user

pytestmark = pytest.mark.integration



async def test_create_user(user_repo: UserRepoABC, test_user: UserEntity):
    """Creates a test user and retrieves it by id."""
    inserted = await user_repo.insert(test_user)
    ret_user = await user_repo.select(inserted.id)
    assert ret_user
    assert ret_user.avatar == test_user.avatar

async def test_update_user(db: Database, user_repo: UserRepoABC, test_user: UserEntity):
    """Creates a test user, updates it, and retrieves it twice by id."""
    inserted = await user_repo.insert(test_user)
    updated_user = replace(inserted, avatar="http://somewere")
    ret_user_update = await user_repo.update(updated_user)
    ret_user_by_id = await user_repo.select(ret_user_update.id)
    assert ret_user_by_id and ret_user_by_id.id
    assert ret_user_by_id == ret_user_update  # assert that update returns same as select
    assert ret_user_by_id.avatar == "http://somewere"

async def test_create_user_with_note_and_delete(user_repo: UserRepoABC, note_repo_facade: NoteFacadeABC, user_service, test_user: UserEntity):
    """
    - Creates a user
    - Creates a note for that user
    - Deletes the user
    - Asserts that both user and note are deleted (cascade delete)
    """
    # ``user_service.create_user`` is used instead of
    # ``user_repo.insert`` so the user goes through the full
    # bootstrap (default directories + shelf + rule).  Without
    # this, the note facade's default-fleeting rule lookup has
    # nothing to resolve and the note insert raises before the
    # cascade path is exercised.
    test_user = await user_service.create_user(test_user)
    assert isinstance(test_user.id, str)
    assert UUID(test_user.id).version == 7
    ctx = UserContext(user_id=test_user.id)

    test_note = NoteEntity(
        title="Pauls secret note",
        content="This is a secret note.",
        updated_at=datetime.now(),
        author_id=test_user.id
    )
    note = await note_repo_facade.insert(test_note, ctx)
    assert isinstance(note.note_id, str)
    assert UUID(note.note_id).version == 7

    await user_repo.delete(test_user.id)
    ret_user = await user_repo.select(test_user.id)
    assert ret_user is None

    # Deleting the user cascades the FK to note.content; the note
    # is therefore gone after the user is gone.
    ret_note = await note_repo_facade.select_by_id(note.note_id, ctx=ctx)
    assert ret_note is None