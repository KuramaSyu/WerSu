from dataclasses import replace
from datetime import datetime
from typing import AsyncGenerator, Optional
from uuid import UUID
import pytest
from testcontainers.postgres import PostgresContainer
from tests.doubles import _DummyUserContext as UserContext
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.user.user import FilterUser, UserEntity
from src.api.facades.note_facade import NoteFacadeABC
from src.api.repos.user_repo import UserRepoABC
import src.api
from src.db.repos import UserPostgresRepo, Database
from src.utils import logging_provider

# import fixtures, otherise pytest will not detect them
from tests.fixtures import db, note_repo_facade, user_repo, note_repo_facade, dsn, test_user

pytestmark = pytest.mark.integration



async def test_create_user(user_repo: UserRepoABC, test_user: UserEntity):
    """Creates a test user and retrieves it by id."""
    inserted = await user_repo.insert(test_user)
    ret_user = await user_repo.select(FilterUser(user_id=inserted.id))
    assert ret_user
    assert ret_user.avatar == test_user.avatar

async def test_select_user_by_username(user_repo: UserRepoABC, test_user: UserEntity):
    """A user can be looked up by the unique username."""
    inserted = await user_repo.insert(test_user)
    ret_user = await user_repo.select(FilterUser(username="KuramaSyu"))
    assert ret_user is not None
    assert ret_user.id == inserted.id

async def test_update_user(db: Database, user_repo: UserRepoABC, test_user: UserEntity):
    """Creates a test user, updates it, and retrieves it twice by id."""
    inserted = await user_repo.insert(test_user)
    updated_user = replace(inserted, avatar="http://somewere")
    ret_user_update = await user_repo.update(updated_user)
    ret_user_by_id = await user_repo.select(FilterUser(user_id=ret_user_update.id))
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
    ret_user = await user_repo.select(FilterUser(user_id=test_user.id))
    assert ret_user is None

    # Deleting the user cascades the FK to note.content; the note
    # is therefore gone after the user is gone.
    ret_note = await note_repo_facade.select_by_id(note.note_id, ctx=ctx)
    assert ret_note is None

# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def test_search_users_by_username_fuzzy(
    user_repo: UserRepoABC, test_user: UserEntity
):
    """A partial username matches via ``pg_trgm`` similarity / ILIKE."""
    inserted = await user_repo.insert(test_user)

    results = await user_repo.search("Kurama")

    assert [user.id for user in results] == [inserted.id]


async def test_search_users_by_partial_id(
    user_repo: UserRepoABC, test_user: UserEntity
):
    """A fragment of the (uuid) id returns every user whose id contains it."""
    inserted = await user_repo.insert(test_user)

    results = await user_repo.search(str(inserted.id)[:8])

    assert inserted.id in [user.id for user in results]


async def test_search_users_by_email_is_exact(
    user_repo: UserRepoABC, test_user: UserEntity
):
    """Email queries match exactly and never fall back to partial matches."""
    inserted = await user_repo.insert(test_user)

    exact = await user_repo.search(test_user.email or "")
    partial = await user_repo.search("kuramaSyu@")

    assert [user.id for user in exact] == [inserted.id]
    assert partial == []


async def test_search_users_caps_results_at_limit(
    user_repo: UserRepoABC, test_user: UserEntity
):
    """The ``limit`` argument caps the result set."""
    for index in range(12):
        await user_repo.insert(
            replace(
                test_user,
                username=f"searcher{index:02d}",
                email=f"searcher{index:02d}@example.com",
            )
        )

    results = await user_repo.search("searcher", limit=10)

    assert len(results) == 10


async def test_search_users_blank_query_returns_nothing(
    user_repo: UserRepoABC, test_user: UserEntity
):
    """A blank query short-circuits to an empty list."""
    await user_repo.insert(test_user)

    assert await user_repo.search("") == []
    assert await user_repo.search("   ") == []


async def test_fetch_users_by_ids_returns_input_order(
    user_repo: UserRepoABC, test_user: UserEntity
):
    """A batch read returns each existing user once, in the input order."""
    first = await user_repo.insert(test_user)
    second = await user_repo.insert(
        replace(test_user, username="KuramaSyu2", email="other@example.com")
    )

    fetched = await user_repo.fetch_users_by_ids(
        [str(second.id), "does-not-exist", str(first.id), str(second.id)]
    )

    assert [str(user.id) for user in fetched] == [str(second.id), str(first.id)]


async def test_fetch_users_by_ids_empty_input(
    user_repo: UserRepoABC, test_user: UserEntity
):
    """An empty id list short-circuits to an empty result."""
    await user_repo.insert(test_user)

    assert await user_repo.fetch_users_by_ids([]) == []
