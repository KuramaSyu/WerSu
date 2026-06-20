from dataclasses import replace
from typing import List

from asyncpg import Record

from src.api.sharing import SharingRepo
from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.db.entities.note.sharing import FilterShareNoteEntity, NoteShareEntity
from src.db.table import TableABC
from src.utils import asdict


class SharingPostgresRepo(SharingRepo):
    """Postgres-backed storage for note share rows.

    The repo intentionally performs no permission checks. Callers are expected
    to use the service layer for authorization and pass already-valid entities.
    """

    _returning = (
        "id, description, note_id, created_at, created_by, "
        "online_since, online_until, access_as"
    )

    def __init__(self, table: TableABC[List[Record]]):
        self._table = table

    async def create_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        if share.note_id in (UNDEFINED, None):
            raise ValueError("share.note_id is required")
        if share.access_as in (UNDEFINED, None):
            raise ValueError("share.access_as is required")
        if share.created_at in (UNDEFINED, None):
            raise ValueError("share.created_at is required")
        if share.created_by in (UNDEFINED, None):
            raise ValueError("share.created_by is required")

        records = await self._table.insert(asdict(share), returning=self._returning)
        if not records:
            raise ValueError("Failed to create share")
        return self._from_record(records[0])

    async def update_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        if share.id in (UNDEFINED, None):
            raise ValueError("share.id is required")

        # Only mutable fields are passed to SET; UNDEFINED fields are omitted by
        # `asdict`, while explicit None values remain and clear nullable columns.
        set_values = asdict(
            replace(
                share,
                id=UNDEFINED,
                note_id=UNDEFINED,
                created_at=UNDEFINED,
                created_by=UNDEFINED,
                access_as=UNDEFINED,
            )
        )
        if not set_values:
            raise ValueError("At least one share field must be set for update")

        record = await self._table.update(
            set=set_values,
            where={"id": share.id},
            returning=self._returning,
        )
        if not record:
            raise ValueError(f"Share not found: {share.id}")
        return self._from_record(record)

    async def get_shares_by_id(self, share_ids: List[str], ctx: UserContextABC) -> List[NoteShareEntity]:
        if not share_ids:
            raise ValueError("At least one share_id is required")

        shares = []
        for share_id in share_ids:
            if not share_id:
                raise ValueError("share_id is required")

            record = await self._table.select_row(
                where={"id": share_id},
                select=self._returning,
            )
            if not record:
                raise ValueError(f"Share not found: {share_id}")
            shares.append(self._from_record(record))

        return shares

    async def get_shares(self, filter: FilterShareNoteEntity, ctx: UserContextABC) -> List[NoteShareEntity]:
        conditions = []
        values = []

        def add_value_condition(column: str, operator: str, value: object) -> None:
            values.append(value)
            conditions.append(f"{column} {operator} ${len(values)}")

        if filter.note_id is not UNDEFINED:
            add_value_condition("note_id", "=", filter.note_id)
        if filter.created_by is not UNDEFINED:
            add_value_condition("created_by", "=", filter.created_by)
        if filter.access_as is not UNDEFINED:
            add_value_condition("access_as", "=", filter.access_as)

        # `None` is a real filter value for nullable date columns.
        if filter.online_since is None:
            conditions.append("online_since IS NULL")
        elif filter.online_since is not UNDEFINED:
            add_value_condition("online_since", ">=", filter.online_since)

        if filter.online_until is None:
            conditions.append("online_until IS NULL")
        elif filter.online_until is not UNDEFINED:
            add_value_condition("online_until", "<=", filter.online_until)

        where = " AND ".join(conditions) if conditions else "TRUE"
        records = await self._table.fetch(
            f"""
            SELECT {self._returning}
            FROM {self._table.name}
            WHERE {where}
            ORDER BY created_at DESC
            """,
            *values,
        )
        return [self._from_record(record) for record in records or []]

    async def delete_shares(self, share_ids: List[str], ctx: UserContextABC) -> None:
        if not share_ids:
            raise ValueError("At least one share_id is required")

        for share_id in share_ids:
            if not share_id:
                raise ValueError("share_id is required")

            deleted = await self._table.delete(where={"id": share_id}, returning="id")
            if not deleted:
                raise ValueError(f"Share not found: {share_id}")

    @staticmethod
    def _from_record(record: Record) -> NoteShareEntity:
        """Convert an asyncpg record into the sharing entity."""
        return NoteShareEntity(**dict(record))
