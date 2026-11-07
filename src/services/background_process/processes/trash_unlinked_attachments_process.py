"""Background process that trashes and purges unlinked attachments.

The process runs on a fixed interval and performs three idempotent
steps:

1. **Untrash** every trashed attachment that has a link again.  A
   note edit can unlink and re-link an attachment, so a restore must
   win over a previous trash stamp.
2. **Trash** every attachment not yet in the trash that is linked to
   neither a note nor a user and was created before
   ``now - grace_period``.  The
   grace period protects the window between uploading an attachment
   and linking it to its note.
3. **Purge** every attachment trashed before ``now - retention``:
   delete the object from storage, drop the metadata row (which
   cascades the link rows) and remove its SpiceDB relationships.

Each attachment is processed independently so one storage failure
does not abort the whole sweep; the failed key stays trashed and is
retried on the next run.  The storage delete happens before the
metadata delete: if metadata deletion then fails, the object is
already gone and the next run only retries the idempotent delete.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from src.api.other.relationship import (
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.services.background_process.background_process import (
    BackgroundProcessABC,
)
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.db.repos.attachments.attachments import AttachmentRepoABC
from src.api.repos.attachment_trash_repo import AttachmentTrashRepoABC


class TrashUnlinkedAttachmentsProcessImpl(BackgroundProcessABC):
    """Move unlinked attachments to the trash and purge expired trash.
    
    Args:
        trash_repo: persistence for the trash marker (``trashed_at``)
            and the queries that select candidates for trash and
            purge.
        attachment_repo: storage backend used to delete the binary
            when a trashed attachment expires.
        permission_repo: SpiceDB repo whose attachment relationships
            are removed during the purge.
        get_now: clock returning the current time; injected so tests
            can pin time instead of sleeping.
        log: logging-provider factory used to build this process's
            logger.
        interval: how often the sweep runs after the first
            catch-up run at boot.
        grace_period: how long an attachment may stay unlinked
            before it is moved to the trash.  Must cover the window
            between uploading an attachment and linking it to its
            note.
        retention_period: how long a trashed attachment is kept
            before its storage object, metadata row, and permission
            relationships are purged.
        batch_limit: maximum number of candidates handled per trash
            and purge query, bounding the memory and S3 work per
            run.
    """

    def __init__(
        self,
        trash_repo: AttachmentTrashRepoABC,
        attachment_repo: AttachmentRepoABC,
        permission_repo: PermissionRepoABC,
        get_now: Callable[[], datetime],
        log: LoggingProvider,
        *,
        interval: timedelta = timedelta(hours=1),
        grace_period: timedelta = timedelta(days=1),
        retention_period: timedelta = timedelta(days=30),
        batch_limit: int = 500,
    ) -> None:
        """Wire the process and its tunables.


        """
        self._trash_repo = trash_repo
        self._attachment_repo = attachment_repo
        self._permission_repo = permission_repo
        self._get_now = get_now
        self.log = log(__name__, self)
        self._interval = interval
        self._grace_period = grace_period
        self._retention_period = retention_period
        self._batch_limit = batch_limit
        self._handle: Optional[SchedulerHandleABC] = None
        self._first_wakeup = True

    def attach_handle(self, handle: SchedulerHandleABC) -> None:
        self._handle = handle

    async def next_wakeup(self) -> Optional[datetime]:
        """Run once at boot for catch-up, then every ``interval``."""
        now = self._get_now()
        if self._first_wakeup:
            self._first_wakeup = False
            return now
        return now + self._interval

    async def run(self) -> None:
        """One full trash + purge sweep.  Idempotent."""
        now = self._get_now()
        await self._untrash_relinked(now)
        await self._trash_unlinked(now)
        await self._purge_expired(now)

    # -- steps -----------------------------------------------------------

    async def _untrash_relinked(self, now: datetime) -> None:
        try:
            restored = await self._trash_repo.untrash_linked(now)
        except Exception as exc:  # noqa: BLE001
            self.log.exception(f"trash attachments: untrash step failed: {exc!r}")
            return
        if restored:
            self.log.info(
                f"trash attachments: restored {len(restored)} relinked "
                f"attachment(s)"
            )

    async def _trash_unlinked(self, now: datetime) -> None:
        cutoff = now - self._grace_period
        try:
            candidates = await self._trash_repo.find_unlinked_before(
                cutoff, self._batch_limit
            )
            if not candidates:
                self.log.debug("trash attachments: no unlinked candidates")
                return
            trashed = await self._trash_repo.mark_trashed(candidates, now)
        except Exception as exc:  # noqa: BLE001
            self.log.exception(f"trash attachments: trash step failed: {exc!r}")
            return
        self.log.info(
            f"trash attachments: moved {len(trashed)} unlinked attachment(s) "
            f"to trash"
        )

    async def _purge_expired(self, now: datetime) -> None:
        cutoff = now - self._retention_period
        try:
            expired = await self._trash_repo.find_trashed_before(
                cutoff, self._batch_limit
            )
        except Exception as exc:  # noqa: BLE001
            self.log.exception(f"trash attachments: purge scan failed: {exc!r}")
            return

        if not expired:
            self.log.debug("trash attachments: nothing to purge")
            return

        purged = 0
        for key in expired:
            if await self._purge_one(key):
                purged += 1
        self.log.info(
            f"trash attachments: purged {purged}/{len(expired)} attachment(s)"
        )

    async def _purge_one(self, key: str) -> bool:
        """Purge a single trashed attachment.  Returns True on success."""
        try:
            await self._attachment_repo.delete_attachment(key)
        except Exception as exc:  # noqa: BLE001
            self.log.exception(
                f"trash attachments: storage delete failed for key={key}: "
                f"{exc!r}; will retry next run"
            )
            return False

        try:
            await self._trash_repo.delete_metadata([key])
        except Exception as exc:  # noqa: BLE001
            self.log.exception(
                f"trash attachments: metadata delete failed for key={key}: "
                f"{exc!r}; will retry next run"
            )
            return False

        try:
            await self._permission_repo.delete(
                Relationship(
                    ObjectRef(ObjectTypeEnum.ATTACHMENT, key),
                    relation=UNDEFINED,
                    subject=SubjectRef(UNDEFINED, UNDEFINED),
                )
            )
        except Exception as exc:  # noqa: BLE001
            # Permissions are gone with the metadata anyway; log and
            # keep going rather than let one orphan tuple stall the sweep.
            self.log.exception(
                f"trash attachments: permission cleanup failed for key={key}: "
                f"{exc!r}"
            )

        self.log.debug(f"trash attachments: purged key={key}")
        return True


__all__ = ["TrashUnlinkedAttachmentsProcessImpl"]
