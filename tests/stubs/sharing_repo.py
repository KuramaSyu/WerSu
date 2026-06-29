"""In-memory sharing repo fake for unit tests.

Records each CRUD call so tests can assert on the order, shape, and
arguments that the service under test passes through.
"""

from __future__ import annotations

from typing import List, Optional

from src.api.user_context import UserContextABC
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity


class _FakeSharingRepo:
    """In-memory sharing repo that records calls made by the service."""

    def __init__(self, shares: Optional[List[NoteShareEntity]] = None) -> None:
        self.shares: List[NoteShareEntity] = list(shares or [])
        self.created_share: Optional[NoteShareEntity] = None
        self.updated_share: Optional[NoteShareEntity] = None
        self.deleted_ids: Optional[List[str]] = None
        self.last_filter: Optional[FilterShareNote] = None
        self.get_shares_by_id_calls: List[List[str]] = []
        self.get_shares_calls: List[FilterShareNote] = []

    async def create_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        self.created_share = share
        return share

    async def update_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        self.updated_share = share
        return share

    async def get_share_by_id(self, share_id: str, ctx: UserContextABC) -> NoteShareEntity:
        shares = await self.get_shares_by_id([share_id], ctx)
        if not shares:
            raise ValueError(f"Share not found: {share_id}")
        return shares[0]

    async def get_shares_by_id(
        self,
        share_ids: List[str],
        ctx: UserContextABC,
    ) -> List[NoteShareEntity]:
        self.get_shares_by_id_calls.append(share_ids)
        return [share for share in self.shares if share.id in share_ids]

    async def get_shares(
        self,
        filter: FilterShareNote,
        ctx: UserContextABC,
    ) -> List[NoteShareEntity]:
        self.last_filter = filter
        self.get_shares_calls.append(filter)
        return self.shares

    async def delete_shares(self, share_ids: List[str], ctx: UserContextABC) -> None:
        self.deleted_ids = share_ids


__all__ = ["_FakeSharingRepo"]