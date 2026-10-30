from __future__ import annotations

from tests.doubles.fakespys.tag_repo import _FakeSpyTagRepo

from typing import Optional, cast
import asyncpg


class _MockUniqueViolationTagRepo(_FakeSpyTagRepo):
    """Repo whose writes always fail with a Postgres unique violation."""

    async def create_tag(self, slug: str, display_name: str):  # type: ignore[override]
        raise asyncpg.UniqueViolationError("duplicate key value")

    async def update_tag(  # type: ignore[override]
        self,
        tag_id: str,
        *,
        slug: Optional[str] = None,
        display_name: Optional[str] = None,
    ):
        raise asyncpg.UniqueViolationError("duplicate key value")


__all__ = [
    '_MockUniqueViolationTagRepo',
]
