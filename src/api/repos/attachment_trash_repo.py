"""Storage contract for the attachment trash lifecycle.

The trash lifecycle is owned by
:class:`src.services.background_process.processes.trash_unlinked_attachments_process.TrashUnlinkedAttachmentsProcessImpl`.
It needs five primitives, all expressed here so the process stays
free of SQL and is testable with an in-memory fake:

* :meth:`AttachmentTrashRepoABC.untrash_linked` -- bring re-linked
  attachments back out of the trash.
* :meth:`AttachmentTrashRepoABC.find_unlinked_before` -- pick the
  candidates that should be trashed.
* :meth:`AttachmentTrashRepoABC.mark_trashed` -- stamp the candidates.
* :meth:`AttachmentTrashRepoABC.find_trashed_before` /
  :meth:`AttachmentTrashRepoABC.delete_metadata` -- drive the purge.

An attachment counts as *linked* when it has a row in either
``note.attachment_note_link`` or ``note.attachment_user_link``.

Implementations:
* :class:`src.db.repos.attachments.trash.PostgresAttachmentTrashRepo`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class AttachmentTrashRepoABC(ABC):
    """Persistence primitives for the attachment trash lifecycle.

    The repo only reads and writes the trash marker; authorisation,
    storage deletes, and permission cleanup stay in the calling
    process.  A ``trashed_at`` of ``None`` means the attachment is
    still in normal use; a timestamp means it is in the trash and
    eligible for purge once it ages out.

    Implementations:
    * :class:`src.db.repos.attachments.trash.PostgresAttachmentTrashRepo`
    """

    @abstractmethod
    async def untrash_linked(self, now: datetime) -> list[str]:
        """Clear ``trashed_at`` on every trashed attachment that has a link.

        A trashed attachment that got re-linked (e.g. the user edited
        the note and saved the same image again) must come back to
        life so the purge step does not destroy it.  Idempotent: a
        second call with no re-linked rows returns ``[]``.

        Args:
            now: timestamp written to ``updated_at`` on restored rows.

        Returns:
            list[str]: keys that were restored, in unspecified order.
        """
        ...

    @abstractmethod
    async def find_unlinked_before(
        self, cutoff: datetime, limit: int
    ) -> list[str]:
        """Return keys of unlinked attachments not yet in the trash.

        Selects attachments whose ``trashed_at`` is ``NULL``, whose
        ``created_at`` is older than ``cutoff``, and that have no row
        in ``note.attachment_note_link`` or
        ``note.attachment_user_link``.  The caller's grace period is
        folded into ``cutoff`` so a freshly uploaded (but not yet
        linked) attachment is never selected.

        Args:
            cutoff: exclusive upper bound on ``created_at``.
            limit: maximum number of keys to return.

        Returns:
            list[str]: candidate keys ordered oldest first.
        """
        ...

    @abstractmethod
    async def mark_trashed(self, keys: list[str], at: datetime) -> list[str]:
        """Stamp ``trashed_at`` on the given keys and return the ones changed.

        Idempotent: keys already trashed are skipped, so re-running
        the sweep does not move the purge deadline forward.

        Args:
            keys: attachment keys to move to the trash.  An empty
                list is a no-op.
            at: timestamp written to ``trashed_at`` and ``updated_at``.

        Returns:
            list[str]: keys that transitioned from not-trashed to trashed.
        """
        ...

    @abstractmethod
    async def find_trashed_before(
        self, cutoff: datetime, limit: int
    ) -> list[str]:
        """Return keys trashed before ``cutoff``, oldest first.

        Args:
            cutoff: exclusive upper bound on ``trashed_at``.
            limit: maximum number of keys to return.

        Returns:
            list[str]: expired trash keys ordered by ``trashed_at``.
        """
        ...

    @abstractmethod
    async def delete_metadata(self, keys: list[str]) -> int:
        """Delete the metadata rows for ``keys``; cascades link rows.

        Called only after the storage object has been deleted, so a
        failure here leaves the row in place and the next run retries.

        Args:
            keys: attachment keys whose ``note.attachment`` rows
                should be removed.  An empty list is a no-op.

        Returns:
            int: number of rows actually deleted.
        """
        ...


__all__ = ["AttachmentTrashRepoABC"]
