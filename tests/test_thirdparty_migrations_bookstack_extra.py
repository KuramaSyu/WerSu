"""Unit tests pinning the two regressions that shipped in v1:

1. Chapter directories were created with ``display_name`` left as
   ``UNDEFINED``, so the production ``INSERT`` stored a NULL in the
   ``display_name`` column.  The frontend renders the directory name
   from that column, so chapters appeared without a name.  The fix
   is to pass ``display_name=chapter.name`` in the orchestrator.
2. The previous unit tests used hand-rolled stubs that did not
   exercise the real :meth:`NoteServiceImpl._resolve_parent_directory_id`
   visibility check, so any orchestrator-side regression on that
   code path passed silently.  The new
   :file:`tests/test_thirdparty_migrations_real_services.py` covers
   that path; this file pins the simpler, observable contract that
   the orchestrator sets ``display_name`` on every directory it
   creates.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import pytest

from src.api.services.directory_service import DirectoryServiceABC
from src.api.services.note_service import NoteServiceABC
from src.api.other.relationship import DirectoryRelationEnum
from src.api.other.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.attachments.attachments import Attachment
from src.services.attachment_facade import AttachmentFacadeABC
from src.services.thirdparty_migrations.bookstack import BookstackBookImport
from tests.stubs.directory_service import _StubDirectoryService
from tests.stubs.logging import silent_logger
from tests.stubs.user_context import _UserContext


class _StubNoteService(NoteServiceABC):
    def __init__(self) -> None:
        self._next_id = 0

    async def insert_note(self, note: NoteEntity, user_ctx: UserContextABC) -> NoteEntity:
        self._next_id += 1
        return NoteEntity(
            note_id=f"note-{self._next_id}",
            title=note.title,
            content=note.content,
            author_id=note.author_id,
            directory_ids=list(note.directory_ids or []),
            permissions=[],
        )

    async def update_note(self, note: NoteEntity, user_ctx: UserContextABC) -> NoteEntity:
        return note

    async def get_note(self, note_id, user_ctx):
        raise NotImplementedError

    async def delete_note(self, note_id, user_ctx):
        raise NotImplementedError

    async def search_notes(self, search_type, query, user_ctx, limit, offset):
        raise NotImplementedError

    async def get_notes(self, note_ids, user_ctx, options=None):
        raise NotImplementedError


class _StubAttachmentFacade(AttachmentFacadeABC):
    async def post_attachment(self, attachment: Attachment, user_ctx: UserContextABC) -> Attachment:
        return Attachment(
            key=f"att-{attachment.filename}",
            filename=attachment.filename,
            content=attachment.content,
            content_type=attachment.content_type,
            size=attachment.size,
        )

    async def update_metadata(self, attachment, user_ctx):
        raise NotImplementedError

    async def get_attachment(self, key, user_ctx):
        raise NotImplementedError

    async def get_metadata(self, key, user_ctx):
        raise NotImplementedError

    async def delete_attachment(self, key, user_ctx):
        raise NotImplementedError

    async def link_attachment_to_note(self, attachment_key, note_id, user_ctx):
        return None

    async def unlink_attachment_from_note(self, attachment_key, note_id, user_ctx):
        raise NotImplementedError

    async def list_attachments_for_note(self, note_id, user_ctx):
        raise NotImplementedError


def _build_importer() -> Tuple[BookstackBookImport, _StubDirectoryService]:
    """Wire the orchestrator with the project-level stubs."""
    ds = _StubDirectoryService()
    created: List[DirectoryEntity] = []

    async def create(entity: DirectoryEntity, user_ctx: UserContextABC) -> DirectoryEntity:
        ds.next_directory_id += 1
        new_id = f"dir-{ds.next_directory_id}"
        new = DirectoryEntity(
            id=new_id,
            slug=entity.slug,
            display_name=entity.display_name,
            description=entity.description,
            image_url=entity.image_url,
            parent_directory_ids=list(entity.parent_directory_ids or []),
            readme_note_id=entity.readme_note_id,
            relations=[],
        )
        ds.directories_by_id[new_id] = new
        created.append(new)
        return new

    ds.create_directory = create  # type: ignore[attr-defined]

    importer = BookstackBookImport(
        attachment_facade=_StubAttachmentFacade(),
        directory_service=ds,
        note_service=_StubNoteService(),
        log=silent_logger,
    )
    return importer, ds


def _created_dirs(ds: _StubDirectoryService) -> List[DirectoryEntity]:
    """Return the directory entities the stub created, in call order."""
    # The stub doesn't expose ``.created`` directly; iterate by
    # ``directories_by_id`` insertion order which preserves the order
    # the orchestrator created them in (Python dicts are
    # insertion-ordered).
    return list(ds.directories_by_id.values())


def _book_payload() -> dict:
    return {
        "book": {
            "name": "Tiny book",
            "chapters": [
                {"id": 1, "name": "Chapter 1", "priority": 0, "pages": []},
                {"id": 2, "name": "Chapter 2", "priority": 1, "pages": []},
            ],
            "pages": [],
        }
    }


def _build_zip(payload: dict) -> bytes:
    import io
    import json
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.json", json.dumps(payload))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_book_directory_has_display_name() -> None:
    """The book directory also carries ``display_name`` equal to its name."""
    importer, ds = _build_importer()
    user_ctx = _UserContext(user_id="u-1")

    await importer.migrate(_build_zip(_book_payload()), user_ctx)

    book_dir = next(d for d in _created_dirs(ds) if d.display_name == "Tiny book")
    assert book_dir.display_name == "Tiny book"