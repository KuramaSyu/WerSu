from __future__ import annotations

from src.api.other.user_context import UserContextABC
from src.api.services.attachment_facade import AttachmentFacadeABC


class _FakeSpyAttachmentService(AttachmentFacadeABC):
    """Stub whose ``get_attachment_token`` behavior is preset per test."""

    def __init__(self, *, token: str = "", error: Exception | None = None) -> None:
        self._token = token
        self._error = error
        self.last_key: str | None = None
        self.last_user_ctx: UserContextABC | None = None

    async def get_attachment_token(self, attachment_key, user_ctx):
        self.last_key = attachment_key
        self.last_user_ctx = user_ctx
        if self._error is not None:
            raise self._error
        return self._token

    # The remaining ABC surface is unused by these tests.
    async def post_attachment(self, attachment, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def update_metadata(self, attachment, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def get_attachment(self, key, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def get_metadata(self, key, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def delete_attachment(self, key, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def link_attachment(self, attachment_key, sub_type, sub_id, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def unlink_attachment(self, attachment_key, sub_type, sub_id, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def list_attachments(self, sub_type, sub_id, user_ctx):  # pragma: no cover
        raise NotImplementedError


__all__ = [
    '_FakeSpyAttachmentService',
]
