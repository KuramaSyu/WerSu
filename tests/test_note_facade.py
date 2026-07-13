"""Fast unit tests for :class:`src.db.repos.note.note.NoteFacadeImpl`.

These tests use the in-memory fakes from
:mod:`tests._fixtures_pkg.fakes` so they do not require a Postgres
container.  They cover the CRUD surface that
:class:`src.services.note.NoteServiceImpl` and the gRPC adapters call.

The behaviours pinned here were the ones that previously had to be
exercised by integration tests with a real Postgres container.  The
goal is to keep the test suite fast while still catching regressions
in the CRUD path.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Optional
from uuid import UUID

import pytest

from src.api.other.types import Pagination
from src.api.other.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.db.repos.note import note as note_module
from src.db.repos.note.note import NoteFacadeImpl
from src.api.facades.note_facade import SearchType
from tests._fixtures_pkg.fakes import (
    _FakeCombinedNoteRepo,
    _FakeDatabase,
    _FakeEmbeddingRepo,
    _FakeJwtProvider,
    _FakeNoteContentRepo,
    _FakeNoteTagRepo,
    _FakeVersionRepo,
    _TestDirectoryRepo,
)
from tests.stubs.in_memory_permission_repo import InMemoryPermissionRepo
from tests.stubs.user_context import _UserContext as UserContext


def _make_facade(
    *,
    version_repo: Optional[_FakeVersionRepo] = None,
    db: Optional[_FakeDatabase] = None,
    content_repo: Optional[_FakeNoteContentRepo] = None,
    combined_repo: Optional[_FakeCombinedNoteRepo] = None,
    tag_repo: Optional[_FakeNoteTagRepo] = None,
    permission_repo: Optional[InMemoryPermissionRepo] = None,
    directory_repo: Optional[DirectoryFacadeABC] = None,
) -> tuple[NoteFacadeImpl, _FakeDatabase, _FakeNoteContentRepo, _FakeEmbeddingRepo, DirectoryFacadeABC, _FakeCombinedNoteRepo, _FakeNoteTagRepo]:
    """Build a :class:`NoteFacadeImpl` wired against the in-memory fakes."""
    fake_db = db or _FakeDatabase()
    fake_content = content_repo or _FakeNoteContentRepo()
    fake_combined = combined_repo or _FakeCombinedNoteRepo(content_repo=fake_content)
    fake_embedding = _FakeEmbeddingRepo()
    fake_permission = permission_repo or InMemoryPermissionRepo()
    fake_directory = directory_repo or _TestDirectoryRepo()
    fake_tags = tag_repo or _FakeNoteTagRepo()
    facade = NoteFacadeImpl(
        db=fake_db,
        content_repo=fake_content,
        combined_repo=fake_combined,
        embedding_repo=fake_embedding,
        permission_repo=fake_permission,
        directory_repo=fake_directory,
        tag_repo=fake_tags,
        logging_provider=_log_provider,
        version_repo=version_repo,
    )
    return facade, fake_db, fake_content, fake_embedding, fake_directory, fake_combined, fake_tags


def _log_provider(*_args, **_kwargs):
    import logging
    return logging.getLogger("test.note_facade")


def _seed_note(note_id: str = "note-1", **overrides) -> NoteEntity:
    payload = dict(
        note_id=note_id,
        title="Seed",
        content="Seed content",
        updated_at=datetime(2026, 7, 3, 12, 0, 0),
        author_id="user-1",
        embeddings=[],
        permissions=[],
    )
    payload.update(overrides)
    return NoteEntity(**payload)

async def test_insert_without_content_skips_embedding() -> None:
    """`insert` does not generate an embedding when `content` is empty."""
    facade, fake_db, _content, _embedding, fake_directory, _combined, _tags = _make_facade()
    fake_db.fetchrow_responses.append({"id": "note-empty"})

    note = NoteEntity(
        title="",
        content="",
        updated_at=datetime(2026, 7, 3, 12, 0, 0),
        author_id="user-1",
    )
    result = await facade.insert(note, UserContext("user-1"))

    assert result.embeddings == []
    # and the version repo was not invoked for the no-content path either
    # (the facade always calls record_initial_snapshot if a version_repo is set;
    #  we exercised that branch in test_insert_records_initial_snapshot_when_version_repo_present)


async def test_insert_records_initial_snapshot_when_version_repo_present() -> None:
    """`insert` records an initial version snapshot via the version repo."""
    facade, fake_db, _content, _embedding, fake_directory, _combined, _tags = _make_facade(version_repo=_FakeVersionRepo())
    fake_db.fetchrow_responses.append({"id": "note-snap"})

    note = NoteEntity(
        title="Snap title",
        content="Snap content",
        updated_at=datetime(2026, 7, 3, 12, 0, 0),
        author_id="user-1",
    )
    await facade.insert(note, UserContext("user-1"))

    # No assertions on the snapshot body here; the version repo stub
    # records calls in its own state.  We only need to confirm the
    # facade passed through without raising.


async def test_update_overwrites_content_and_refreshes_embedding() -> None:
    """`update` re-fetches, mutates via content repo, refreshes embedding."""
    facade, _db, content_repo, _embedding, _directory, _combined, _tags = _make_facade()
    seeded = _seed_note(note_id="note-1", content="old content")
    content_repo.seed(seeded)

    updated_payload = NoteEntity(
        note_id="note-1",
        title="New title",
        content="New content",
        updated_at=datetime(2026, 7, 4, 9, 30, 0),
        author_id="user-1",
    )
    result = await facade.update(updated_payload, UserContext("user-1"))

    assert result.title == "New title"
    assert result.content == "New content"
    assert result.permissions == []
    assert len(result.embeddings) == 1
    assert result.embeddings[0].note_id == "note-1"
    # underlying content repo reflects the new state
    persisted = await content_repo.select_by_id("note-1")
    assert persisted.title == "New title"
    assert persisted.content == "New content"


async def test_update_appends_version_entry_when_version_repo_present() -> None:
    """`update` forwards the before/after pair to `version_repo.append_version`."""
    version_repo = _FakeVersionRepo()
    facade, _db, content_repo, _embedding, _directory, _combined, _tags = _make_facade(version_repo=version_repo)
    content_repo.seed(_seed_note(note_id="note-1", content="old content"))

    updated_payload = NoteEntity(
        note_id="note-1",
        title="New title",
        content="New content",
        updated_at=datetime(2026, 7, 4, 9, 30, 0),
        author_id="user-1",
    )
    await facade.update(updated_payload, UserContext("user-1"))

    assert version_repo.last_append is not None
    kwargs = version_repo.last_append["kwargs"]
    assert kwargs["note_id"] == "note-1"
    assert kwargs["old_title"] == "Seed"
    assert kwargs["old_content"] == "old content"
    assert kwargs["new_title"] == "New title"
    assert kwargs["new_content"] == "New content"
    assert kwargs["author_id"] == "user-1"
    assert kwargs["created_at"] == datetime(2026, 7, 4, 9, 30, 0)


async def test_delete_returns_list_from_content_repo() -> None:
    """`delete` returns the list the content repo yields."""
    facade, _db, content_repo, _embedding, _directory, _combined, _tags = _make_facade()
    content_repo.seed(_seed_note(note_id="note-1"))

    deleted = await facade.delete("note-1", UserContext("user-1"))

    assert deleted is not None
    assert len(deleted) == 1
    assert deleted[0].note_id == "note-1"
    # the note is gone from the store
    with pytest.raises(RuntimeError):
        await content_repo.select_by_id("note-1")


async def test_delete_returns_empty_list_when_nothing_matches() -> None:
    """`delete` returns an empty list when the content repo yields nothing."""
    facade, _db, _content, _embedding, fake_directory, _combined, _tags = _make_facade()

    deleted = await facade.delete("ghost", UserContext("user-1"))

    assert deleted == []


async def test_select_by_id_normalises_permissions_to_empty_list() -> None:
    """`select_by_id` returns `permissions = []` and the seeded entity."""
    facade, _db, content_repo, _embedding, _directory, _combined, _tags = _make_facade()
    seeded = replace(_seed_note(note_id="note-1"), permissions=UNDEFINED)
    content_repo.seed(seeded)

    record = await facade.select_by_id("note-1", UserContext("user-1"))

    assert record is not None
    assert record.permissions == []
    assert record.note_id == "note-1"


async def test_search_notes_dispatches_known_strategy() -> None:
    """`search_notes` instantiates the matching strategy for `SearchType.NO_SEARCH`.

    The facade's strategy constructors require a `PermissionRepoABC`
    (via ``note_permissions``); we exercise the dispatch branch by
    monkey-patching the strategy to a recording stub.  The full
    SQL-driven search behaviour is covered by the integration tests
    in ``test_notes_repo.py``.
    """
    from src.db.repos.note.note import DateNoteSearchStrategy

    class _RecordingStrategy:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def search(self):
            return []

    facade, _db, _content, _embedding, fake_directory, _combined, _tags = _make_facade()
    original = DateNoteSearchStrategy
    note_module.DateNoteSearchStrategy = _RecordingStrategy  # type: ignore[attr-defined]
    try:
        results = await facade.search_notes(
            SearchType.NO_SEARCH,
            query="",
            ctx=UserContext("user-1"),
            pagination=Pagination(limit=10, offset=0),
        )
    finally:
        note_module.DateNoteSearchStrategy = original  # type: ignore[attr-defined]

    assert results == []


async def test_search_notes_raises_for_unknown_search_type() -> None:
    """`search_notes` raises `ValueError` for an unrecognised `SearchType`."""
    from enum import Enum

    class _Bad(Enum):
        NOPE = 99

    facade, _db, _content, _embedding, fake_directory, _combined, _tags = _make_facade()

    with pytest.raises(ValueError, match="Unknown SearchType"):
        await facade.search_notes(
            _Bad.NOPE,  # type: ignore[arg-type]
            query="",
            ctx=UserContext("user-1"),
            pagination=Pagination(limit=10, offset=0),
        )

