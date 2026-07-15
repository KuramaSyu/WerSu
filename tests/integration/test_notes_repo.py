from dataclasses import replace
from datetime import datetime
from typing import AsyncGenerator, Optional
from uuid import UUID
import pytest
from testcontainers.postgres import PostgresContainer
from torch import embedding
from src.db.repos.note import note_facade
from tests.stubs.user_context import _UserContext as UserContext
from src.api.other.types import Pagination
from src.api.other.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.directory.directory import DirectoryEntity
from src.db.repos.note.permission import NoteRelationEnum, ObjectTypeEnum
from src.db.repos.note.content import NoteContentPostgresRepo, NoteContentRepo
from src.db.repos.note.note_facade import NoteFacadeImpl
from src.api.facades.note_facade import NoteFacadeABC, SearchType
from src.api.repos.tag_repo import TagRepoABC
from src.db.table import Table
from src.db.entities.user.user import UserEntity
from src.db.repos.user.user import UserRepoABC
import src.api
from src.db.repos import UserPostgresRepo, Database
from src.utils import logging_provider
from tests.fixtures import db, note_repo_facade, tag_repo, user_repo, dsn, test_user

pytestmark = pytest.mark.integration

# each test recreates user and note to keep readability per test

async def test_create_note(db: Database, note_repo_facade: NoteFacadeABC, user_repo: UserRepoABC, test_user: UserEntity):
    """Creates a test user, and creates a note for this user"""
    log = logging_provider(__name__)
    user = await user_repo.insert(test_user)
    assert user.id
    ctx = UserContext(user_id=user.id)

    updated_at = datetime(2024, 1, 1, 12, 0, 0)
    test_note = NoteEntity(
        title="Test Note", 
        content="This is a test note.", 
        updated_at=updated_at, 
        author_id=user.id
    )
    ret_note = await note_repo_facade.insert(test_note, ctx)
    assert ret_note.note_id is not UNDEFINED
    test_note = replace(test_note, note_id=ret_note.note_id)
    log.debug(f"Created note: {ret_note}; expected: {test_note}")
    assert ret_note == test_note

async def test_update_note(db: Database, note_repo_facade: NoteFacadeABC, user_repo: UserRepoABC, test_user: UserEntity):
    """Creates a test user, and creates a note for this user"""
    user = await user_repo.insert(test_user)
    assert user.id
    ctx = UserContext(user_id=user.id)

    updated_at = datetime(2024, 1, 1, 12, 0, 0)
    test_note = NoteEntity(
        title="Test Note", 
        content="This is a test note.", 
        updated_at=updated_at, 
        author_id=user.id
    )
    test_note = await note_repo_facade.insert(test_note, ctx)
    updated_note = replace(
        test_note, 
        title="Updated Test Note", 
        content="This is an updated test note.", 
        updated_at=datetime(2024, 1, 2, 12, 0, 0)
    )
    ret_note = await note_repo_facade.update(updated_note, ctx)

    # assert, that embedding was updated
    assert isinstance(ret_note.embeddings, list) and len(ret_note.embeddings[0].embedding) > 0  # type: ignore
    assert ret_note.embeddings[0].embedding != test_note.embeddings[0].embedding  # type: ignore
    updated_note = replace(updated_note, embeddings=ret_note.embeddings)  # type: ignore
    
    assert ret_note == updated_note

async def test_create_and_remove_note(
    db: Database,
    note_repo_facade: NoteFacadeABC,
    user_repo: UserRepoABC,
    tag_repo: TagRepoABC,
    test_user: UserEntity,
    directory_repo,
):
    """Creates a test user, and creates a note for this user, then removes the note"""
    user = await user_repo.insert(test_user)
    assert user.id
    ctx = UserContext(user_id=user.id)

    updated_at = datetime(2024, 1, 1, 12, 0, 0)
    # create a tag and inject it
    tag = await tag_repo.create_tag(slug="injected-tag", display_name="Injected")
    # create 2 directories and inject them
    injected_dir_ids = [f"dir-{i}" for i in range(2)]
    for dir_id in injected_dir_ids:
        directory_repo.directories_by_id[dir_id] = DirectoryEntity(
            id=dir_id, slug=f"slug-{dir_id}",
        )
        directory_repo.user_to_directory_ids.setdefault(user.id, []).append(dir_id)
    test_note = NoteEntity(
        title="Test Note",
        content="This is a test note.",
        updated_at=updated_at,
        author_id=user.id,
        directory_ids=list(injected_dir_ids),
        tag_ids=[str(tag.id)],
    )
    test_note_insert = await note_repo_facade.insert(test_note, ctx)
    assert isinstance(test_note_insert.note_id, str)  # inserted note should have an ID
    assert UUID(test_note_insert.note_id).version == 7

    # attach the injected tag via the new tag_repo surface and
    # assert both list_tags_for and list_tags_of see it.  Insert
    # already wrote the tag, so assign_tag_to here is the
    # idempotent no-op path.
    await tag_repo.assign_tag_to("note", str(test_note_insert.note_id), str(tag.id))
    assert (
        await tag_repo.list_tags_for("note", [str(test_note_insert.note_id)])
    )[str(test_note_insert.note_id)] == [tag.id]
    assert await tag_repo.list_tags_of("note", str(test_note_insert.note_id)) == [tag.id]

    test_note_select = await note_repo_facade.select_by_id(note_id=test_note_insert.note_id, ctx=ctx)
    assert test_note_select  # select should return a note

    assert replace(test_note_select, permissions=[], embeddings=[]) == replace(test_note_insert, permissions=[], embeddings=[])
    assert test_note_select.directory_ids == test_note_insert.directory_ids and len(test_note_select.directory_ids or []) == 2, "directory_ids should be assigned"
    assert test_note_select.tag_ids == test_note_insert.tag_ids and len(test_note_select.tag_ids or []) == 1, "tag_ids should be assigned"

    # assign_tag_to must raise when either side of the association
    # does not exist -- it is not a silent no-op.
    with pytest.raises(ValueError):
        await tag_repo.assign_tag_to("note", str(test_note_insert.note_id), "missing-tag-id")
    with pytest.raises(ValueError):
        await tag_repo.assign_tag_to("note", "missing-note-id", str(tag.id))

    # replace_tags_for / replace_tags_of / remove_tag_from are the
    # counterpart to assign_tag_to.  Build a second tag so we can
    # exercise the set-difference path (one tag kept, one removed,
    # one added).
    second_tag = await tag_repo.create_tag(slug="other-tag", display_name="Other")
    note_id = str(test_note_insert.note_id)

    # replace_tags_for: drop the first tag, add the second.  The
    # kept binding is unchanged (no-op insert) and the dropped
    # binding is deleted.
    await tag_repo.replace_tags_for("note", [note_id], [str(second_tag.id)])
    assert await tag_repo.list_tags_of("note", note_id) == [second_tag.id]

    # replace_tags_of (singular variant) -- restore the first tag
    # alongside the second so the note ends up with both.
    await tag_repo.replace_tags_of("note", note_id, [str(tag.id), str(second_tag.id)])
    assert await tag_repo.list_tags_of("note", note_id) == sorted([tag.id, second_tag.id])

    # replace_tags_for with an empty tag_ids clears every binding
    # on every owner in the bulk call.
    await tag_repo.replace_tags_for("note", [note_id], [])
    assert await tag_repo.list_tags_of("note", note_id) == []

    # remove_tag_from is the natural delete counterpart to
    # assign_tag_to: silently no-op when the binding, the tag, or
    # the subject is absent.
    await tag_repo.assign_tag_to("note", note_id, str(tag.id))
    await tag_repo.remove_tag_from("note", note_id, str(tag.id))
    assert await tag_repo.list_tags_of("note", note_id) == []
    # removing a non-existent binding does not raise.
    await tag_repo.remove_tag_from("note", note_id, "missing-tag-id")
    await tag_repo.remove_tag_from("note", "missing-note-id", str(tag.id))
    await tag_repo.remove_tag_from("note", note_id, str(tag.id))

    # re-attach the first tag so the rest of the test (select +
    # delete assertions) still sees the original `tag_ids == 1`
    # state.
    await tag_repo.assign_tag_to("note", note_id, str(tag.id))
    assert await tag_repo.list_tags_of("note", note_id) == [tag.id]

    # replace_tags_for must also raise on a bad tag_id -- same
    # rationale as assign_tag_to.
    with pytest.raises(ValueError):
        await tag_repo.replace_tags_for("note", [note_id], ["missing-tag-id"])
    with pytest.raises(ValueError):
        await tag_repo.replace_tags_for("note", ["missing-note-id"], [str(tag.id)])

    test_notes_delete = await note_repo_facade.delete(test_note_insert.note_id, ctx)

    # deleted note should equal inserted note. Embeddings, permissions,
    # directory_ids and tag_ids are left out since they get cleared by
    # SQL constraints and are not returned in the delete statement.
    assert test_notes_delete == [
        replace(
            test_note_insert,
            embeddings=[],
            permissions=[],
            directory_ids=UNDEFINED,
            tag_ids=UNDEFINED,
        )
    ]

    # select should return None
    test_note_select_after_delete = await note_repo_facade.select_by_id(
        note_id=test_note_insert.note_id,
        ctx=ctx
    )
    assert test_note_select_after_delete is None

async def test_search_by_context(
    note_repo_facade: NoteFacadeABC, 
    user_repo: UserRepoABC,
    test_user: UserEntity
):
    """Creates a test user, and creates multiple notes for this user, then searches by context"""
    user = await user_repo.insert(test_user)
    assert user.id
    ctx = UserContext(user_id=user.id)
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
        await note_repo_facade.insert(test_note, ctx)

    async def search(search_query: str, should_contain: str, negative_search: bool = False) -> bool:
        """Small helper function to make a positive or negative search"""
        assert user.id
        search_results = await note_repo_facade.search_notes(
            search_type=SearchType.CONTEXT,
            query=search_query,
            pagination=Pagination(limit=10, offset=0),
            ctx=UserContext(user_id=user.id)
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
    note_repo_facade: NoteFacadeABC, 
    user_repo: UserRepoABC,
    test_user: UserEntity
):
    """Creates a test user, and creates multiple notes for this user, then searches by fuzzy matching"""
    user = await user_repo.insert(test_user)
    assert user.id
    ctx = UserContext(user_id=user.id)

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
        await note_repo_facade.insert(test_note, ctx)

    async def search(search_query: str, should_contain: str, negative_search: bool = False) -> bool:
        """Small helper function to make a positive search"""
        assert user.id
        search_results = await note_repo_facade.search_notes(
            search_type=SearchType.FULL_TEXT_TITLE,
            query=search_query,
            pagination=Pagination(limit=10, offset=0),
            ctx=UserContext(user_id=user.id)
        )
        if not search_results:
            # No hits: a positive search returns False, a negative
            # search (which expects the term to be excluded) returns
            # True since the term couldn't match anything.
            return negative_search
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

    # Fuzzy matching a Zelda search returns no results (no raise)
    assert await search(
        search_query="Yelda totk",
        should_contain="Zelda totk means Tears of the Kingdom"
    ) == False

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
    note_repo_facade: NoteFacadeABC, 
    user_repo: UserRepoABC,
    test_user: UserEntity
):
    """
    Creates a test user, 
    and creates multiple notes for this user, 
    then searches by similarity
    """
    user = await user_repo.insert(test_user)
    assert user.id
    ctx = UserContext(user_id=user.id)

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
        await note_repo_facade.insert(test_note, ctx)

    async def search(search_query: str, should_contain: str) -> bool:
        """Small helper function to make a positive search"""
        assert user.id
        search_results = await note_repo_facade.search_notes(
            search_type=SearchType.FUZZY,
            query=search_query,
            pagination=Pagination(limit=10, offset=0),
            ctx=UserContext(user_id=user.id)
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
    note_repo_facade: NoteFacadeABC, 
    user_repo: UserRepoABC,
    test_user: UserEntity
):
    """Creates a test user, 
    and creates multiple notes for this user, 
    then searches without filter
    which should return notes in creation order
    """
    user = await user_repo.insert(test_user)
    assert user.id
    ctx = UserContext(user_id=user.id)

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
        await note_repo_facade.insert(test_note, ctx)

    search_results = await note_repo_facade.search_notes(
        search_type=SearchType.NO_SEARCH,
        query="",
        pagination=Pagination(limit=10, offset=0),
        ctx=UserContext(user_id=user.id)
    )
    assert len(search_results) >= 3
    assert search_results[2].content == "First note content."
    assert search_results[1].content == "Second note content."
    assert search_results[0].content == "Third note content."


async def test_search_assigns_parent_directories(
    note_repo_facade: NoteFacadeABC,
    user_repo: UserRepoABC,
    test_user: UserEntity,
):
    """Test if inserted notes, which should automatically get a directory, have 
    a directory relation within the permissions of the searched notes"""
    user = await user_repo.insert(test_user)
    ctx = UserContext(user_id=user.id)

    inserted = await note_repo_facade.insert(
        NoteEntity(
            title="Permission test",
            content="Search should contain parent directory relation.",
            updated_at=datetime.now(),
            author_id=user.id,
        ),
        ctx,
    )

    search_results = await note_repo_facade.search_notes(
        search_type=SearchType.NO_SEARCH,
        query="",
        pagination=Pagination(limit=10, offset=0),
        ctx=ctx,

    )

    found = next((n for n in search_results if n.note_id == inserted.note_id), None)
    assert found is not None

    parent_directories = found.directory_ids
    assert parent_directories
    # the default fleeting dir should be assgined to it
    assert len(parent_directories) == 1
    # note.permissions should not be populated
    assert found.permissions is [] or found.permissions is UNDEFINED, "note.permissions still get populated"


async def test_search_only_assigns_permissions_for_returned_notes(
    note_repo_facade: NoteFacadeABC,
    user_repo: UserRepoABC,
    test_user: UserEntity,
):
    """Tests that the permissions returned in the search results are related to the note id"""
    user = await user_repo.insert(test_user)
    ctx = UserContext(user_id=user.id)

    await note_repo_facade.insert(
        NoteEntity(
            title="First permission note",
            content="First note content",
            updated_at=datetime.now(),
            author_id=user.id,
        ),
        ctx,
    )
    await note_repo_facade.insert(
        NoteEntity(
            title="Second permission note",
            content="Second note content",
            updated_at=datetime.now(),
            author_id=user.id,
        ),
        ctx,
    )

    search_results = await note_repo_facade.search_notes(
        search_type=SearchType.NO_SEARCH,
        query="",
        pagination=Pagination(limit=1, offset=0),
        ctx=ctx,
    )

    assert len(search_results) == 1
    returned_note = search_results[0]

    for rel in (returned_note.permissions or []):
        assert rel.resource.object_id == returned_note.note_id

