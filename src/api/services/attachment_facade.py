"""Abstract application service for attachment lifecycle.

Implementations:
* :class:`src.services.attachment_facade.AttachmentFacadeImpl`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from src.api.other.user_context import UserContextABC
from src.db.repos.attachments.attachments import Attachment


#: Sub-types an attachment can be linked to.
#:
#: Extend this union (and the dispatch in
#: :class:`src.services.attachment_facade.AttachmentFacadeImpl`) when a
#: new owning entity is introduced.  The matching SpiceDB relation
#: lives in :data:`~src.api.other.relationship.AttachmentRelationEnum`.
LinkTargetType = Literal["note", "user"]


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
                an attachment is not yet linked to any owner so no
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
    async def link_attachment(
        self,
        attachment_key: str,
        sub_type: LinkTargetType,
        sub_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        """Create a link between an attachment and an owning entity.

        Args:
            attachment_key: attachment to link.
            sub_type: kind of owner -- "note" or "user".
            sub_id: id of the note or user to link it to.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the actor cannot view the attachment
                and (for "note") the target note.
            PermissionError: for "user" when sub_id is not the
                caller user id.
        """
        ...

    @abstractmethod
    async def unlink_attachment(
        self,
        attachment_key: str,
        sub_type: LinkTargetType,
        sub_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        """Remove the link between an attachment and an owning entity.

        Args:
            attachment_key: attachment to unlink.
            sub_type: kind of owner -- "note" or "user".
            sub_id: id of the note or user to unlink it from.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the actor cannot view the attachment
                and (for "note") the target note.
            PermissionError: for "user" when sub_id is not the
                caller user id.
        """
        ...

    @abstractmethod
    async def list_attachments(
        self,
        sub_type: LinkTargetType,
        sub_id: str,
        user_ctx: UserContextABC,
    ) -> list[Attachment]:
        """List every attachment linked to an owning entity.

        Args:
            sub_type: kind of owner -- "note" or "user".
            sub_id: id of the note or user whose attachments to list.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the actor cannot view the target
                entity.

        Returns:
            list[Attachment]: every linked attachment, with content.
        """
        ...


__all__ = ["AttachmentFacadeABC", "LinkTargetType"]