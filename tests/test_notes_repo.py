from dataclasses import replace
from datetime import datetime
from typing import AsyncGenerator, Optional
import pytest
from testcontainers.postgres import PostgresContainer
from src.api.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.note.content import NoteContentPostgresRepo, NoteContentRepo
from src.db.repos.note.note import NoteRepoFacade, NoteRepoFacadeABC
from src.db.table import Table
from src.db.entities.user.user import UserEntity
from src.db.repos.user.user import UserRepoABC
import src.api
from src.db.repos import UserPostgresRepo, Database
from src.utils import logging_provider
from .fixtures import db, note_repo_facade, user_repo



async def test_create_note(db: Database, note_repo_facade: NoteRepoFacadeABC, user_repo: UserRepoABC):
    """Creates a test user, and creates a note for this user"""
    log = logging_provider(__name__)
    user = UserEntity(
        discord_id=123455,
        avatar_url="test",
    )
    user = await user_repo.insert(user)

    updated_at = datetime(2024, 1, 1, 12, 0, 0)
    test_note = NoteEntity(
        title="Test Note", 
        content="This is a test note.", 
        updated_at=updated_at, 
        author_id=user.id
    )
    ret_note = await note_repo_facade.insert(test_note)
    assert ret_note.note_id is not UNDEFINED
    test_note = replace(test_note, note_id=ret_note.note_id)
    log.debug(f"Created note: {ret_note}; expected: {test_note}")
    assert ret_note == test_note

async def test_update_note(db: Database, note_repo_facade: NoteRepoFacadeABC, user_repo: UserRepoABC):
    """Creates a test user, and creates a note for this user"""
    user = UserEntity(
        discord_id=123455,
        avatar_url="test",
    )
    user = await user_repo.insert(user)

    updated_at = datetime(2024, 1, 1, 12, 0, 0)
    test_note = NoteEntity(
        title="Test Note", 
        content="This is a test note.", 
        updated_at=updated_at, 
        author_id=user.id
    )
    test_note = await note_repo_facade.insert(test_note)
    updated_note = replace(
        test_note, 
        title="Updated Test Note", 
        content="This is an updated test note.", 
        updated_at=datetime(2024, 1, 2, 12, 0, 0)
    )
    ret_note = await note_repo_facade.update(updated_note)
    print(f"Updated note: {ret_note}; expected: {updated_note}")
    assert ret_note == updated_note
    




