"""Abstract application service for attachment lifecycle.

Implementations:
* :class:`src.services.attachment_facade.AttachmentFacadeImpl`
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.api.other.user_context import UserContextABC
from src.db.repos.attachments.attachments import Attachment


class AttachmentFacadeABC(ABC):
    """Application service for attachment lifecycle.

    Implementations:
    * :class:`src.services.attachment_facade.AttachmentFacadeImpl`
    """

    @abstractmethod
    async def post_attachment(
        self,
        attachment: Attachment,
        user_ctx: UserContextABC,
    ) -> Attachment:
        """Upload attachment contents and persist metadata.

        Args:
            attachment: the attachment to upload.  ``content`` and
                ``filepath`` are required; ``key``, ``created_at``
                and ``updated_at`` are filled in by the impl.
            user_ctx: caller identity (unused at upload time --
                an attachment is not yet linked to a note so no
                permission check applies).

        Returns:
            Attachment: the persisted attachment with its
            server-assigned key populated.
        """
        ...

    @abstractmethod
    async def update_metadata(
        self,
        attachment: Attachment,
        user_ctx: UserContextABC,
    ) -> Attachment:
        """Update an existing attachment's metadata.

        Args:
            attachment: the new metadata.  ``key`` identifies the
                target; every other field is overwritten iff it is
                not :obj:`~src.api.undefined.UNDEFINED`.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the actor cannot write the attachment.

        Returns:
            Attachment: the updated attachment.
        """
        ...

    @abstractmethod
    async def get_attachment(
        self,
        key: str,
        user_ctx: UserContextABC,
    ) -> Attachment:
        """Fetch attachment metadata and content by key.

        Args:
            key: attachment key to load.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the actor cannot view the attachment.

        Returns:
            Attachment: metadata merged with content payload.
        """
        ...

    @abstractmethod
    async def get_metadata(
        self,
        key: str,
        user_ctx: UserContextABC,
    ) -> Attachment:
        """Fetch attachment metadata without the content payload.

        Args:
            key: attachment key to load.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the actor cannot view the attachment.

        Returns:
            Attachment: metadata only.
        """
        ...

    @abstractmethod
    async def delete_attachment(
        self,
        key: str,
        user_ctx: UserContextABC,
    ) -> None:
        """Delete attachment content, metadata, and permissions.

        Args:
            key: attachment key to delete.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the actor cannot write the attachment.
        """
        ...

    @abstractmethod
    async def link_attachment_to_note(
        self,
        attachment_key: str,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        """Create a link between an attachment and a note.

        Args:
            attachment_key: attachment to link.
            note_id: note to link it to.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the actor cannot view either the
                attachment or the note.
        """
        ...

    @abstractmethod
    async def unlink_attachment_from_note(
        self,
        attachment_key: str,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        """Remove the link between an attachment and a note.

        Args:
            attachment_key: attachment to unlink.
            note_id: note to unlink it from.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the actor cannot view either the
                attachment or the note.
        """
        ...

    @abstractmethod
    async def list_attachments_for_note(
        self,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> list[Attachment]:
        """List every attachment linked to a note.

        Args:
            note_id: note whose attachments to list.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the actor cannot view the note.

        Returns:
            list[Attachment]: every linked attachment, with content.
        """
        ...


__all__ = ["AttachmentFacadeABC"]