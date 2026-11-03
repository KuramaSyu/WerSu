"""Minimal existence stubs for shelf/role permission-service tests."""

from __future__ import annotations

from src.api.other.user_context import UserContextABC


class _StubPermissionShelfRepo:
    """Implements only ``fetch_shelf`` for permission-service existence checks."""

    def __init__(self, shelf_ids: set[str]) -> None:
        self._shelf_ids = shelf_ids

    async def fetch_shelf(self, shelf_id: str, *, include_books: bool = False):
        return object() if shelf_id in self._shelf_ids else None


class _StubPermissionRoleRepo:
    """Implements only ``get_role`` for permission-service existence checks."""

    def __init__(self, role_ids: set[str]) -> None:
        self._role_ids = role_ids

    async def get_role(self, role_id: str, ctx: UserContextABC):
        return object() if role_id in self._role_ids else None


__all__ = [
    "_StubPermissionRoleRepo",
    "_StubPermissionShelfRepo",
]