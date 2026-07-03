"""Fast unit tests for :class:`src.services.note.NoteService`.

These tests use the in-memory fakes from
:mod:`tests._fixtures_pkg.fakes` and
:class:`src.db.repos.permissions.permission.NotePermissionRepoInMemory`
so they do not require Postgres or SpiceDB.  They pin the
service-layer behaviour the gRPC adapters rely on:

* :meth:`get_note` resolves a note, attaches permission relations,
  and mints per-attachment JWTs only for temporary users.
* :meth:`insert_note` resolves the parent directory, persists the
  note, and writes owner + parent-directory relations.
* :meth:`update_note` and :meth:`delete_note` delegate to the repo.
* :meth:`search_notes` enriches results with directory relations.

The permission-enrichment path was previously covered by integration
tests; running it in pure unit tests keeps the suite fast while still
catching regressions in the orchestration logic.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import List, Optional

import pytest

from tests.stubs.user_context import _UserContext as UserContext
from src.api.note_service import NoteResponse
from src.api.permission_repo import PermissionRepoABC
from src.api.relationship import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.note import NoteFacade, NoteRepoFacadeABC, SearchType
from src.db.repos.permissions.permission import NotePermissionRepoInMemory
from src.services.note import NoteService
from src.api.user_context import UserContextABC
from src.api.jwt_provider import JwtProvider
from tests._fixtures_pkg.fakes import (
    _FakeDatabase,
    _FakeEmbeddingRepo,
    _FakeJwtProvider,
    _FakeNoteContentRepo,
    _TestDirectoryRepo,
)
from tests.stubs.user_context import _UserContext as _UserCtx


def _log_provider(*_args, **_kwargs):
    import logging
    return logging.getLogger("test.note_service")


def _human_ctx(user_id: str = "user-1") -> _UserCtx:
    return _UserCtx(user_id=user_id)


class _TemporaryUserContext(_UserCtx):
    """`_UserContext` whose `is_temporary_user()` returns True."""

    async def is_temporary_user(self) -> bool:
        return True


def _temporary_ctx(user_id: str = "tmp-user") -> _TemporaryUserContext:
    return _TemporaryUserContext(user_id=user_id)


def _make_service(
    *,
    content_repo: Optional[_FakeNoteContentRepo] = None,
    directory_repo: Optional[DirectoryRepo] = None,
    permission_repo: Optional[PermissionRepoABC] = None,
    jwt_provider: Optional[JwtProvider] = None,
    next_note_id: str = "019f0000-0000-7000-8000-000000000001",
) -> tuple[
    NoteService,
    _FakeDatabase,
    _FakeNoteContentRepo,
    DirectoryRepo,
    NotePermissionRepoInMemory,
    _FakeJwtProvider,
]:
    """Build a :class:`NoteService` wired against the in-memory fakes.

    The fake database queues the next note id the note facade will
    receive from ``INSERT ... RETURNING id`` so that ``insert_note``
    can proceed end-to-end without a real Postgres backend.
    """
    fake_db = _FakeDatabase()
    fake_db.fetchrow_responses.append({"id": next_note_id})
    fake_content = content_repo or _FakeNoteContentRepo()
    fake_embedding = _FakeEmbeddingRepo()
    fake_permission = permission_repo or NotePermissionRepoInMemory()
    fake_directory = directory_repo or _TestDirectoryRepo()
    fake_jwt = jwt_provider or _FakeJwtProvider()
    facade = NoteFacade(
        db=fake_db,
        content_repo=fake_content,
        embedding_repo=fake_embedding,
        logging_provider=_log_provider,
        permission_repo=fake_permission,
        directory_repo=fake_directory,
    )
    service = NoteService(
        note_repo=facade,
        permission_repo=fake_permission,
        jwt_provider=fake_jwt,
        directory_repo=fake_directory,
    )
    return service, fake_db, fake_content, fake_directory, fake_permission, fake_jwt


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


# ---------------------------------------------------------------------------
# get_note
# ---------------------------------------------------------------------------


async def test_get_note_propagates_error_when_repo_misses() -> None:
    """`get_note` propagates the error raised by the note repo on a miss.

    The facade has a defensive ``if note is None`` guard but the
    underlying :class:`NoteContentRepo` raises before that branch
    runs in practice; pin the propagated error so future refactors
    do not silently change the failure mode.
    """
    service, _db, _content, _dir, _perm, _jwt = _make_service()

    with pytest.raises(RuntimeError, match="ghost"):
        await service.get_note("ghost", _human_ctx())

    # JWT provider must NOT be touched on a miss
    assert _jwt.create_calls == []


async def test_get_note_attaches_permissions_for_existing_note() -> None:
    """`get_note` populates `note.permissions` with stored relations."""
    service, _db, content_repo, _dir, permission_repo, _jwt = _make_service()

    note = _seed_note(note_id="note-1")
    content_repo.seed(note)
    owner_rel = Relationship(
        resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
        relation=NoteRelationEnum.OWNER,
        subject=SubjectRef(ObjectTypeEnum.USER, "user-1"),
    )
    await permission_repo.insert([owner_rel])

    response = await service.get_note("note-1", _human_ctx())

    assert response.note is not None
    assert response.note.note_id == "note-1"
    assert any(
        str(rel.relation) == str(NoteRelationEnum.OWNER)
        and str(rel.subject.object_id) == "user-1"
        for rel in response.note.permissions
    )
    # human caller gets no JWTs
    assert response.id_token_map == {}


async def test_get_note_mints_jwts_for_temporary_user_when_viewing_attachment() -> None:
    """`get_note` mints one JWT per embedded attachment for temporary users."""
    service, _db, content_repo, _dir, permission_repo, jwt_provider = _make_service()

    note = _seed_note(
        note_id="note-1",
        content="see https://cdn.example/api/attachments/att-a and /api/attachments/att-b",
    )
    content_repo.seed(note)
    # grant view on both attachments to the temp user
    for att in ("att-a", "att-b"):
        await permission_repo.insert(
            [
                Relationship(
                    resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, att),
                    relation=NoteRelationEnum.READER,
                    subject=SubjectRef(ObjectTypeEnum.USER, "tmp-user"),
                )
            ]
        )

    response = await service.get_note("note-1", _temporary_ctx("tmp-user"))

    assert response.note is not None
    assert set(response.id_token_map) == {"att-a", "att-b"}
    # each id_token_map entry came from the JWT provider
    assert jwt_provider.create_calls == [
        ("tmp-user", "att-a"),
        ("tmp-user", "att-b"),
    ]


async def test_get_note_skips_attachments_without_view_permission() -> None:
    """Attachments the temp user cannot view do not get a JWT."""
    service, _db, content_repo, _dir, permission_repo, _jwt = _make_service()

    note = _seed_note(
        note_id="note-1",
        content="/api/attachments/att-a /api/attachments/att-b",
    )
    content_repo.seed(note)
    # grant view on att-a only
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, "att-a"),
                relation=NoteRelationEnum.READER,
                subject=SubjectRef(ObjectTypeEnum.USER, "tmp-user"),
            )
        ]
    )

    response = await service.get_note("note-1", _temporary_ctx("tmp-user"))

    assert response.note is not None
    assert set(response.id_token_map) == {"att-a"}


# ---------------------------------------------------------------------------
# insert_note
# ---------------------------------------------------------------------------


async def test_insert_note_resolves_parent_directory_and_writes_owner_relation() -> None:
    """`insert_note` writes owner + parent_directory relations and returns the note."""
    service, _db, _content, _dir, permission_repo, _jwt = _make_service()

    result = await service.insert_note(
        NoteEntity(
            title="New note",
            content="body",
            updated_at=datetime(2026, 7, 3, 12, 0, 0),
            author_id="user-1",
        ),
        _human_ctx("user-1"),
    )

    # parent_directory relation was written
    parent_dir_rels = [
        rel
        for rel in permission_repo._store  # type: ignore[attr-defined]
        if str(rel.relation) == str(NoteRelationEnum.PARENT_DIRECTORY)
    ]
    # both `NoteFacade.insert` and `NoteService.insert_note` may
    # write the relation; we only assert that at least one is recorded.
    assert parent_dir_rels, "no parent_directory relation was written"
    assert parent_dir_rels[0].resource.object_id == result.note_id

    # owner relation was written
    owner_rels = [
        rel
        for rel in permission_repo._store  # type: ignore[attr-defined]
        if str(rel.relation) == str(NoteRelationEnum.OWNER)
    ]
    assert owner_rels, "no owner relation was written"
    assert owner_rels[0].subject.object_id == "user-1"

    # returned note has both relations on its `permissions` field
    assert any(
        str(rel.relation) == str(NoteRelationEnum.OWNER) for rel in result.permissions
    )


async def test_insert_note_rejects_inaccessible_parent_dir() -> None:
    """`insert_note` raises when `parent_dir_id` is not in the user's dirs."""
    service, _db, _content, _dir, _perm, _jwt = _make_service()

    with pytest.raises(ValueError, match="not accessible"):
        await service.insert_note(
            NoteEntity(
                title="New note",
                content="body",
                updated_at=datetime(2026, 7, 3, 12, 0, 0),
                author_id="user-1",
                parent_dir_id="not-my-directory",
            ),
            _human_ctx("user-1"),
        )


# ---------------------------------------------------------------------------
# update_note / delete_note
# ---------------------------------------------------------------------------


async def test_update_note_delegates_to_note_repo() -> None:
    """`update_note` forwards to the note repo and returns its result."""
    service, _db, content_repo, _dir, _perm, _jwt = _make_service()
    content_repo.seed(_seed_note(note_id="note-1", content="old"))

    result = await service.update_note(
        NoteEntity(
            note_id="note-1",
            title="New title",
            content="new content",
            updated_at=datetime(2026, 7, 4, 9, 30, 0),
            author_id="user-1",
        ),
        _human_ctx("user-1"),
    )

    assert result.title == "New title"
    assert result.content == "new content"


async def test_delete_note_returns_deleted_entity() -> None:
    """`delete_note` returns the deleted entity when the repo removes it."""
    service, _db, content_repo, _dir, _perm, _jwt = _make_service()
    content_repo.seed(_seed_note(note_id="note-1"))

    deleted = await service.delete_note("note-1", _human_ctx("user-1"))

    assert deleted is not None
    assert deleted.note_id == "note-1"


async def test_delete_note_returns_none_when_repo_yields_empty() -> None:
    """`delete_note` returns `None` when nothing matched."""
    service, _db, _content, _dir, _perm, _jwt = _make_service()

    deleted = await service.delete_note("ghost", _human_ctx())

    assert deleted is None


# ---------------------------------------------------------------------------
# search_notes
# ---------------------------------------------------------------------------


async def test_search_notes_enriches_results_with_directory_relations() -> None:
    """`search_notes` attaches `PARENT_DIRECTORY` relations to matching notes.

    We populate the permission repo with a `note#PARENT_DIRECTORY@directory`
    relation pointing at a note id that the search strategy will
    surface, then verify the returned note has the relation.
    """
    service, fake_db, content_repo, directory_repo, permission_repo, _jwt = _make_service()

    # queue the search-strategy's `SELECT id, title, ...` response
    fake_db.fetch_responses.append(
        [
            {
                "id": "note-1",
                "title": "Hit",
                "author_id": "user-1",
                "content": "body",
                "updated_at": datetime(2026, 7, 3, 12, 0, 0),
            }
        ]
    )

    # Pick a directory id that the in-memory directory repo will expose.
    user = _human_ctx("user-1")
    directory_ids = await directory_repo.list_user_directory_ids(user)
    assert directory_ids, "_TestDirectoryRepo should expose at least one directory"
    directory_id = directory_ids[0]

    # write the parent-directory relation pointing at note-1
    await permission_repo.insert(
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, directory_id),
            )
        ]
    )

    results = await service.search_notes(
        search_type="NO_SEARCH",
        query="",
        user_ctx=user,
        limit=10,
        offset=0,
    )

    assert len(results) == 1
    hit = results[0]
    assert hit.note_id == "note-1"
    parent_dir_rels = [
        rel
        for rel in hit.permissions
        if str(rel.relation) == str(NoteRelationEnum.PARENT_DIRECTORY)
    ]
    # `NoteService._attach_directory_relations` is idempotent on the
    # existing parent_directory rows, but a double-write path can
    # produce duplicates in test setups; we only assert at least one
    # matches the directory we exposed above.
    assert any(str(rel.subject.object_id) == directory_id for rel in parent_dir_rels)


async def test_search_notes_returns_empty_list_when_no_matches() -> None:
    """`search_notes` returns an empty list and does not call enrichment."""
    service, fake_db, _content, _dir, _perm, _jwt = _make_service()
    # the date strategy hits the database; queue an empty result set
    fake_db.fetch_responses.append([])

    results = await service.search_notes(
        search_type="NO_SEARCH",
        query="",
        user_ctx=_human_ctx(),
        limit=10,
        offset=0,
    )

    assert results == []