from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict
from src.api.other.undefined import UNDEFINED
from src.db.repos.attachments.attachments import (
    Attachment,
    AttachmentMetadataRepoABC,
    AttachmentRepoABC,
)
from src.api.repos.attachment_trash_repo import AttachmentTrashRepoABC
from tests.doubles.fakes.in_memory_permission_repo import InMemoryPermissionRepo
from src.db.table import TableABC


class InMemoryAttachmentRepo(AttachmentRepoABC):
    """In-memory attachment storage for unit tests."""

    def __init__(self) -> None:
        self._store: Dict[str, bytes] = {}

    def contains(self, key: str) -> bool:
        """True when an attachment with ``key`` is stored."""
        return key in self._store

    async def post_attachment(self, attachment: Attachment) -> str:
        if attachment.key is UNDEFINED or attachment.key is None:
            key = f"mem-{len(self._store) + 1}"
        else:
            key = attachment.key
        self._store[str(key)] = attachment.content
        return str(key)

    async def get_attachment(self, key: str) -> Attachment:
        if key not in self._store:
            raise KeyError(f"Attachment not found for key={key}")
        content = self._store[key]
        now = datetime.now(timezone.utc).isoformat()
        return Attachment(
            key=key,
            filename=key,
            filepath=key,
            content_type="application/octet-stream",
            size=len(content),
            created_at=now,
            updated_at=now,
            content=content,
        )

    async def delete_attachment(self, key: str) -> None:
        self._store.pop(key, None)


class InMemoryAttachmentTrashRepo(AttachmentTrashRepoABC):
    """In-memory trash-lifecycle repo for the cleanup process tests.

    Tracks the derived state (creation time, trash timestamp and link
    sets) that the production SQL queries compute on the fly.  Tests
    seed rows via :meth:`add` / :meth:`link_note` / :meth:`link_user`
    and inspect them via :meth:`is_trashed` / :meth:`exists`.
    """

    def __init__(self) -> None:
        self.rows: Dict[str, _FakeAttachmentRow] = {}
        self.deleted: list[str] = []

    # -- test setup helpers -------------------------------------------

    def add(self, key: str, created_at: datetime) -> None:
        self.rows[key] = _FakeAttachmentRow(created_at=created_at)

    def link_note(self, key: str, note_id: str) -> None:
        self.rows[key].note_links.add(note_id)

    def link_user(self, key: str, user_id: str) -> None:
        self.rows[key].user_links.add(user_id)

    # -- inspection helpers -------------------------------------------

    def exists(self, key: str) -> bool:
        return key in self.rows

    def is_trashed(self, key: str) -> bool:
        return self.rows[key].trashed_at is not None

    # -- AttachmentTrashRepoABC ---------------------------------------

    async def untrash_linked(self, now: datetime) -> list[str]:
        restored = []
        for key, row in self.rows.items():
            if row.trashed_at is not None and (row.note_links or row.user_links):
                row.trashed_at = None
                restored.append(key)
        return restored

    async def find_unlinked_before(
        self, cutoff: datetime, limit: int
    ) -> list[str]:
        keys = [
            key
            for key, row in self.rows.items()
            if row.trashed_at is None
            and row.created_at < cutoff
            and not row.note_links
            and not row.user_links
        ]
        return keys[:limit]

    async def mark_trashed(self, keys: list[str], at: datetime) -> list[str]:
        changed = []
        for key in keys:
            row = self.rows.get(key)
            if row is not None and row.trashed_at is None:
                row.trashed_at = at
                changed.append(key)
        return changed

    async def find_trashed_before(
        self, cutoff: datetime, limit: int
    ) -> list[str]:
        keys = [
            key
            for key, row in self.rows.items()
            if row.trashed_at is not None and row.trashed_at < cutoff
        ]
        return keys[:limit]

    async def delete_metadata(self, keys: list[str]) -> int:
        count = 0
        for key in keys:
            if key in self.rows:
                del self.rows[key]
                self.deleted.append(key)
                count += 1
        return count


class InMemoryAttachmentMetadataRepo(AttachmentMetadataRepoABC):
    """In-memory metadata store for unit tests."""

    def __init__(self) -> None:
        self._metadata: Dict[str, Attachment] = {}

    def contains(self, key: str) -> bool:
        """True when metadata for ``key`` is stored."""
        return key in self._metadata

    async def post_metadata(
        self, attachment: Attachment, user_ctx: UserContextABC | None = None
    ) -> None:
        del user_ctx
        if attachment.key is UNDEFINED or attachment.key is None:
            raise ValueError("Attachment key must be set before storing metadata")
        self._metadata[str(attachment.key)] = replace(attachment, content=b"")

    async def get_metadata(
        self, key: str, user_ctx: UserContextABC | None = None
    ) -> Attachment:
        del user_ctx
        if key not in self._metadata:
            raise KeyError(f"Attachment metadata not found for key={key}")
        return self._metadata[key]

    async def update_metadata(
        self, attachment: Attachment, user_ctx: UserContextABC | None = None
    ) -> Attachment:
        del user_ctx
        if attachment.key is UNDEFINED or attachment.key is None:
            raise ValueError("Attachment key must be given perform an update")
        self._metadata[str(attachment.key)] = replace(attachment, content=b"")
        return self._metadata[str(attachment.key)]

    async def delete_metadata(
        self, key: str, user_ctx: UserContextABC | None = None
    ) -> None:
        del user_ctx
        self._metadata.pop(key, None)


class _FakeAttachmentRow:
    """Internal row shape for :class:`InMemoryAttachmentTrashRepo`."""

    def __init__(self, created_at: datetime) -> None:
        self.created_at = created_at
        self.trashed_at: datetime | None = None
        self.note_links: set[str] = set()
        self.user_links: set[str] = set()


class FailingAttachmentRepo(InMemoryAttachmentRepo):
    """Storage repo whose delete always fails (retry-on-next-run tests)."""

    async def delete_attachment(self, key: str) -> None:
        raise RuntimeError("storage is down")


class RecordingAttachmentPermissionRepo(InMemoryPermissionRepo):
    """In-memory permission repo that records every ``delete`` filter."""

    def __init__(self) -> None:
        super().__init__()
        self.deleted = []

    async def delete(self, relationship):  # type: ignore[override]
        self.deleted.append(relationship)
        return await super().delete(relationship)


__all__ = [
    'InMemoryAttachmentRepo',
    'InMemoryAttachmentMetadataRepo',
    'InMemoryAttachmentTrashRepo',
    'FailingAttachmentRepo',
    'RecordingAttachmentPermissionRepo',
]
