"""Postgres-backed implementation of :class:`CalculatedStatsServiceABC`.

For every target user the service resolves the user's visible notes
and directories with SpiceDB wildcard lookups::

    note:UNDEFINED#view@user:<id>
    directory:UNDEFINED#view@user:<id>

and then asks :class:`CalculatedStatsRepoABC` to aggregate those ids
into counts.  The result is persisted in ``calculated_stats``.

The daily background process calls :meth:`recalculate` with no filter.
A future note-update trigger can call it with a single-user filter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List, Optional, Sequence

from src.api.other.relationship import (
    PAGE_ROOT_ID,
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    PageRelationEnum,
    Relationship,
    SubjectRef,
)
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import UserContextABC
from src.api.repos.calculated_stats_repo import CalculatedStatsRepoABC
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.services.calculated_stats_service import CalculatedStatsServiceABC
from src.db.entities.calculated_stats import (
    CalculatedStatsEntity,
    CalculatedStatsFilter,
    DirectoryAggregate,
    NoteAggregate,
)
from src.utils import logging_provider as default_logging_provider


_NOTE_VIEW = NoteRelationEnum.VIEW.value
_DIRECTORY_VIEW = DirectoryRelationEnum.VIEW.value


class CalculatedStatsServiceImpl(CalculatedStatsServiceABC):
    """Recompute and read precomputed per-user stats.

    Args:
        stats_repo: storage + aggregate queries.
        permission_repo: permission backend used for wildcard
            ``view`` lookups and page-administrator checks.
        get_now: clock used for ``calculated_at``; injected so tests
            can pin time.
        logging_provider: optional logger factory.
    """

    def __init__(
        self,
        stats_repo: CalculatedStatsRepoABC,
        permission_repo: PermissionRepoABC,
        get_now: Optional[Callable[[], datetime]] = None,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._stats_repo = stats_repo
        self._permission_repo = permission_repo
        self._get_now: Callable[[], datetime] = get_now or (
            lambda: datetime.now(timezone.utc)
        )
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    async def get_stats(
        self,
        actor: UserContextABC,
        user_id: Optional[str] = None,
    ) -> Optional[CalculatedStatsEntity]:
        target_user_id = str(user_id or actor.user_id)
        if target_user_id != str(actor.user_id):
            allowed = await self._permission_repo.has_permission(
                actor,
                PageRelationEnum.ADMIN.value,
                ObjectRef(ObjectTypeEnum.PAGE, PAGE_ROOT_ID),
            )
            if not allowed:
                raise PermissionError(
                    f"actor {actor.user_id} cannot read stats for "
                    f"user {target_user_id}"
                )
        return await self._stats_repo.get(target_user_id)

    async def recalculate(
        self,
        filter: Optional[CalculatedStatsFilter] = None,
    ) -> List[CalculatedStatsEntity]:
        target_user_ids = await self._resolve_target_user_ids(filter)
        if not target_user_ids:
            self.log.info("calculated stats: no users to recalculate")
            return []

        self.log.info(
            f"calculated stats: recalculating for {len(target_user_ids)} user(s)"
        )
        now = self._get_now()
        updated: List[CalculatedStatsEntity] = []
        for user_id in target_user_ids:
            try:
                stats = await self._recalculate_one(user_id, now)
            except Exception as exc:  # noqa: BLE001
                # One bad user must not abort the whole daily sweep.
                self.log.exception(
                    f"calculated stats: failed for user_id={user_id}: {exc!r}"
                )
                continue
            updated.append(stats)
            self.log.debug(
                f"calculated stats: user_id={user_id} "
                f"notes={stats.total_notes} chars={stats.total_chars} "
                f"books={stats.total_books} chapters={stats.total_chapters}"
            )

        self.log.info(
            f"calculated stats: updated {len(updated)}/{len(target_user_ids)} "
            f"user(s)"
        )
        return updated

    async def _resolve_target_user_ids(
        self,
        filter: Optional[CalculatedStatsFilter],
    ) -> List[str]:
        if filter is not None and filter.user_ids is not None:
            requested = _dedupe_non_empty(filter.user_ids)
            if not requested:
                return []
            # The full-sweep path already enumerates human users; an
            # explicit filter (e.g. one actor woken by a note change)
            # must be intersected with that set so system / temporary
            # users never receive a row.
            human_ids = set(await self._stats_repo.list_user_ids())
            return [user_id for user_id in requested if user_id in human_ids]
        return await self._stats_repo.list_user_ids()

    async def _recalculate_one(
        self,
        user_id: str,
        now: datetime,
    ) -> CalculatedStatsEntity:
        note_ids = await self._lookup_visible_ids(
            ObjectTypeEnum.NOTE, _NOTE_VIEW, user_id
        )
        directory_ids = await self._lookup_visible_ids(
            ObjectTypeEnum.DIRECTORY, _DIRECTORY_VIEW, user_id
        )

        note_aggregate: NoteAggregate = await self._stats_repo.aggregate_notes(
            note_ids
        )
        directory_aggregate: DirectoryAggregate = (
            await self._stats_repo.aggregate_directories(directory_ids)
        )

        stats = CalculatedStatsEntity(
            user_id=user_id,
            total_chars=note_aggregate.total_chars,
            total_notes=note_aggregate.total_notes,
            total_chapters=directory_aggregate.total_chapters,
            total_books=directory_aggregate.total_books,
            calculated_at=now,
        )
        return await self._stats_repo.upsert(stats)

    async def _lookup_visible_ids(
        self,
        object_type: ObjectTypeEnum,
        permission: str,
        user_id: str,
    ) -> List[str]:
        return await self._permission_repo.lookup(
            Relationship(
                resource=ObjectRef(object_type, UNDEFINED),
                relation=permission,
                subject=SubjectRef(ObjectTypeEnum.USER, user_id),
            )
        )


def _dedupe_non_empty(values: Sequence[str]) -> List[str]:
    """Drop empty / falsy ids while preserving the caller's order."""
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        user_id = str(value) if value else ""
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        out.append(user_id)
    return out


__all__ = ["CalculatedStatsServiceImpl"]
