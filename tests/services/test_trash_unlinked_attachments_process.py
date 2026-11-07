"""Tests for :class:`TrashUnlinkedAttachmentsProcessImpl`.

The trash repo is faked in memory
(:class:`tests.doubles.InMemoryAttachmentTrashRepo`); attachment
storage uses :class:`tests.doubles.InMemoryAttachmentRepo`; the
permission repo is the in-memory fake with a recording subclass so we
can assert purge drops the SpiceDB tuples.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.api.other.relationship import ObjectTypeEnum
from src.db.repos.attachments.attachments import Attachment
from src.services.background_process.processes.trash_unlinked_attachments_process import (
    TrashUnlinkedAttachmentsProcessImpl,
)
from tests.doubles import (
    FailingAttachmentRepo,
    InMemoryAttachmentRepo,
    InMemoryAttachmentTrashRepo,
    RecordingAttachmentPermissionRepo,
    silent_logger,
)

pytestmark = pytest.mark.timeout(10)

NOW = datetime(2026, 1, 10, 12, 0, 0)
GRACE = timedelta(days=1)
RETENTION = timedelta(days=30)


def _make_process(
    trash_repo: InMemoryAttachmentTrashRepo,
    attachment_repo: InMemoryAttachmentRepo | None = None,
    permission_repo: RecordingAttachmentPermissionRepo | None = None,
    **kwargs,
) -> TrashUnlinkedAttachmentsProcessImpl:
    return TrashUnlinkedAttachmentsProcessImpl(
        trash_repo=trash_repo,
        attachment_repo=attachment_repo or InMemoryAttachmentRepo(),
        permission_repo=permission_repo or RecordingAttachmentPermissionRepo(),
        get_now=lambda: NOW,
        log=silent_logger,
        grace_period=GRACE,
        retention_period=RETENTION,
        **kwargs,
    )


async def _store_content(repo: InMemoryAttachmentRepo, key: str) -> None:
    await repo.post_attachment(
        Attachment(key=key, filepath=f"/{key}", content=b"data")
    )


# -- trash step ----------------------------------------------------------


async def test_trashes_old_unlinked_attachment() -> None:
    repo = InMemoryAttachmentTrashRepo()
    repo.add("orphan", NOW - timedelta(days=2))

    await _make_process(repo).run()

    assert repo.is_trashed("orphan")


async def test_keeps_note_linked_attachment() -> None:
    repo = InMemoryAttachmentTrashRepo()
    repo.add("note-att", NOW - timedelta(days=2))
    repo.link_note("note-att", "note-1")

    await _make_process(repo).run()

    assert not repo.is_trashed("note-att")


async def test_keeps_user_linked_attachment() -> None:
    repo = InMemoryAttachmentTrashRepo()
    repo.add("avatar", NOW - timedelta(days=2))
    repo.link_user("avatar", "user-1")

    await _make_process(repo).run()

    assert not repo.is_trashed("avatar")


async def test_keeps_recent_unlinked_attachment_inside_grace() -> None:
    repo = InMemoryAttachmentTrashRepo()
    repo.add("fresh", NOW - timedelta(hours=1))

    await _make_process(repo).run()

    assert not repo.is_trashed("fresh")


async def test_restores_relinked_trashed_attachment() -> None:
    repo = InMemoryAttachmentTrashRepo()
    repo.add("relinked", NOW - timedelta(days=2))
    await repo.mark_trashed(["relinked"], NOW - timedelta(days=5))
    repo.link_note("relinked", "note-1")

    await _make_process(repo).run()

    assert not repo.is_trashed("relinked")


async def test_trash_respects_batch_limit() -> None:
    repo = InMemoryAttachmentTrashRepo()
    for i in range(5):
        repo.add(f"a{i}", NOW - timedelta(days=2))

    await _make_process(repo, batch_limit=2).run()

    trashed = [k for k in repo.rows if repo.is_trashed(k)]
    assert len(trashed) == 2


# -- purge step ----------------------------------------------------------


async def test_purges_expired_trash_everywhere() -> None:
    repo = InMemoryAttachmentTrashRepo()
    repo.add("gone", NOW - timedelta(days=90))
    await repo.mark_trashed(["gone"], NOW - timedelta(days=45))

    attachment_repo = InMemoryAttachmentRepo()
    await _store_content(attachment_repo, "gone")
    permission_repo = RecordingAttachmentPermissionRepo()

    await _make_process(repo, attachment_repo, permission_repo).run()

    assert not repo.exists("gone")
    assert not attachment_repo.contains("gone")
    assert len(permission_repo.deleted) == 1
    rel = permission_repo.deleted[0]
    assert rel.resource.object_type == ObjectTypeEnum.ATTACHMENT
    assert rel.resource.object_id == "gone"


async def test_does_not_purge_trash_inside_retention() -> None:
    repo = InMemoryAttachmentTrashRepo()
    repo.add("young-trash", NOW - timedelta(days=2))
    await repo.mark_trashed(["young-trash"], NOW - timedelta(days=2))

    await _make_process(repo).run()

    assert repo.exists("young-trash")


async def test_storage_failure_keeps_metadata_for_retry() -> None:
    repo = InMemoryAttachmentTrashRepo()
    repo.add("stuck", NOW - timedelta(days=90))
    await repo.mark_trashed(["stuck"], NOW - timedelta(days=45))

    permission_repo = RecordingAttachmentPermissionRepo()

    await _make_process(repo, FailingAttachmentRepo(), permission_repo).run()

    # Metadata stays so the idempotent storage delete is retried next run.
    assert repo.exists("stuck")
    assert permission_repo.deleted == []


# -- scheduling ----------------------------------------------------------


async def test_first_wakeup_is_now_then_interval() -> None:
    process = _make_process(
        InMemoryAttachmentTrashRepo(), interval=timedelta(hours=6)
    )

    assert await process.next_wakeup() == NOW
    assert await process.next_wakeup() == NOW + timedelta(hours=6)
