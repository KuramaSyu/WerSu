from dataclasses import replace
from datetime import datetime
from typing import AsyncGenerator, Optional
import pytest
from testcontainers.postgres import PostgresContainer
from src.api.types import Pagination
from src.api.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.note.content import NoteContentPostgresRepo, NoteContentRepo
from src.db.repos.note.note import NoteRepoFacade, NoteRepoFacadeABC, SearchType
from src.db.table import Table
from src.db.entities.user.user import UserEntity
from src.db.repos.user.user import UserRepoABC
import src.api
from src.db.repos import UserPostgresRepo, Database, note
from src.utils import logging_provider
from .fixtures import db, note_repo_facade, user_repo, dsn

# each test recreates user and note to keep readability per test

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

async def test_create_and_remove_note(
    db: Database, 
    note_repo_facade: NoteRepoFacadeABC, 
    user_repo: UserRepoABC
):
    """Creates a test user, and creates a note for this user, then removes the note"""
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
    test_note_insert = await note_repo_facade.insert(test_note)
    assert isinstance(test_note_insert.note_id, int)  # inserted note should have an ID

    test_note_select = await note_repo_facade.select_by_id(note_id=test_note_insert.note_id)
    assert test_note_select  # select should return a note
    assert test_note_select == test_note_insert  # selected note should equal inserted note

    test_notes_delete = await note_repo_facade.delete(test_note_insert)
    
    # deleted note should equal inserted note. Embeddings and permissions are left out, 
    # since they get cleard by SQL constraints and are not returned in the delete statement
    assert test_notes_delete == [replace(test_note_insert, embeddings=[], permissions=[])]

    with pytest.raises(RuntimeError, match=f"Note with ID {test_note_insert.note_id} not found"):
        # select should raise RuntimeError, that note with ID is not found
        test_note_select_after_delete = await note_repo_facade.select_by_id(
            note_id=test_note_insert.note_id
        )

async def test_search_by_context(
    note_repo_facade: NoteRepoFacadeABC, 
    user_repo: UserRepoABC
):
    """Creates a test user, and creates multiple notes for this user, then searches by context"""
    user = UserEntity(
        discord_id=123455,
        avatar_url="test",
    )
    user = await user_repo.insert(user)

    notes_contents = [
        "Python is a nice language which makes programming and life easier.",
        "Another note discussing gRPC services.",
        "This note is about database repositories.",
        "A random note without relevant content.",
        "Citron is used to emulate Mario Kart 8 or Zelda tears of the kingdom.",
    ]

    for content in notes_contents:
        test_note = NoteEntity(
            title="Test Note", 
            content=content, 
            updated_at=datetime.now(), 
            author_id=user.id
        )
        await note_repo_facade.insert(test_note)

    async def search(search_query: str, should_contain: str, negative_search: bool = False) -> bool:
        """Small helper function to make a positive or negative search"""
        search_results = await note_repo_facade.search_notes(
            search_type=SearchType.CONTEXT,
            query=search_query,
            pagination=Pagination(limit=10, offset=0)
        )
        assert search_results[0].content
        if negative_search:
            return should_contain not in search_results[0].content
        else:
            return should_contain in search_results[0].content

    # gRPC test search
    assert await search(
        search_query="REST alternatives to connect services",
        should_contain="discussing gRPC"
    ) == True

    # Python test search
    assert await search(
        search_query="simple language",
        should_contain="Python is a nice language"
    ) == True

    # Emulator test search should not return the random note
    assert await search(
        search_query="play games on Nintendo Switch",
        should_contain="A random note",
        negative_search=True
    ) == True

async def test_search_by_web_lexme_matching(
    note_repo_facade: NoteRepoFacadeABC, 
    user_repo: UserRepoABC
):
    """Creates a test user, and creates multiple notes for this user, then searches by fuzzy matching"""
    user = UserEntity(
        discord_id=123455,
        avatar_url="test",
    )
    user = await user_repo.insert(user)

    note_titles = [
        "Zelda totk means Tears of the Kingdom.",
        "Deep learning is a subset of machine learning.",
        "Neural networks are used in deep learning.",
        "Support vector machines are a type of machine learning algorithm.",
        "Decision trees are another type of machine learning algorithm.",
        "Tears contain water and salt.",
        "Kingdoms are ruled by kings and queens.",
    ]

    for content in note_titles:
        test_note = NoteEntity(
            title=content, 
            content=content, 
            updated_at=datetime.now(), 
            author_id=user.id
        )
        await note_repo_facade.insert(test_note)

    async def search(search_query: str, should_contain: str, negative_search: bool = False) -> bool:
        """Small helper function to make a positive search"""
        search_results = await note_repo_facade.search_notes(
            search_type=SearchType.FULL_TEXT_TITLE,
            query=search_query,
            pagination=Pagination(limit=10, offset=0)
        )
        assert search_results[0].content
        if negative_search:
            return should_contain not in search_results[0].content
        else:
            return should_contain in search_results[0].content

    # normal exact title search
    await search(
        search_query="Zelda",
        should_contain="Zelda totk means Tears of the Kingdom"
    )

    # Fuzzy matching a Zelda search should fail
    with pytest.raises(RuntimeError, match="Failed to fetch notes by exact title"):
        assert await search(
            search_query="Yelda totk",
            should_contain="Zelda totk means Tears of the Kingdom"
        ) == True

    # matching things excluding Zelda
    assert await search(
        search_query="Kingdom -Zelda",
        should_contain="Zelda totk means Tears of the Kingdom",
        negative_search=True
    ) == True

    # Fuzzy matching vector + machine search
    assert await search(
        search_query="vector algorithm machine",
        should_contain="Support vector machines"
    ) == True


async def test_search_by_similarity(
    note_repo_facade: NoteRepoFacadeABC, 
    user_repo: UserRepoABC
):
    """
    Creates a test user, 
    and creates multiple notes for this user, 
    then searches by similarity
    """
    user = UserEntity(
        discord_id=123455,
        avatar_url="test",
    )
    user = await user_repo.insert(user)

    note_titles = [
        "Tears of the Kingdom is a game for Nintendo Switch.",
        "Mario Kart 8 is a racing game for Nintendo Switch.",
        "The Legend of Zelda is an action-adventure game series.",
    ]

    for content in note_titles:
        test_note = NoteEntity(
            title=content, 
            content=content, 
            updated_at=datetime.now(), 
            author_id=user.id
        )
        await note_repo_facade.insert(test_note)

    async def search(search_query: str, should_contain: str) -> bool:
        """Small helper function to make a positive search"""
        search_results = await note_repo_facade.search_notes(
            search_type=SearchType.FUZZY,
            query=search_query,
            pagination=Pagination(limit=10, offset=0)
        )
        assert search_results[0].content
        return should_contain in search_results[0].content
    
    assert await search(
        search_query="Mario Card 9",
        should_contain="Mario Kart 8"
    ) == True
    
    assert await search(
        search_query="Selda",
        should_contain="The Legend of Zelda"
    ) == True

async def test_search_no_filter(
    note_repo_facade: NoteRepoFacadeABC, 
    user_repo: UserRepoABC
):
    """Creates a test user, 
    and creates multiple notes for this user, 
    then searches without filter
    which should return notes in creation order
    """
    user = UserEntity(
        discord_id=123455,
        avatar_url="test",
    )
    user = await user_repo.insert(user)

    note_titles = [
        "First note content.",
        "Second note content.",
        "Third note content.",
    ]

    for content in note_titles:
        test_note = NoteEntity(
            title=content, 
            content=content, 
            updated_at=datetime.now(), 
            author_id=user.id
        )
        await note_repo_facade.insert(test_note)

    search_results = await note_repo_facade.search_notes(
        search_type=SearchType.NO_SEARCH,
        query="",
        pagination=Pagination(limit=10, offset=0)
    )
    assert len(search_results) >= 3
    assert search_results[2].content == "First note content."
    assert search_results[1].content == "Second note content."
    assert search_results[0].content == "Third note content."







    



