"""Postgres-backed implementation of :class:`ActivityStatisticsServiceABC`.

Translates the keyword-driven service API into an
:class:`~src.db.entities.activity.ActivityFilterBuilder`, runs view
checks via :class:`~src.api.permission_repo.PermissionRepoABC`, and
delegates to :class:`~src.api.activity.ActivityRepoABC`.

When the caller passes neither ``note_id`` nor ``directory_id``, the
service asks :class:`~src.api.directory_repo.DirectoryRepo` for the
actor's visible directories and threads them into the builder one at a
time so each gets expanded to its subtree.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from src.api.repos.activity_repo import ActivityRepoABC
from src.api.services.activity_statistics_service import (
    ActivityStatisticsServiceABC,
    Algorithm,
)
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.other.relationship import ObjectRef
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, is_undefined, unwrap_undefined_or
from src.api.other.user_context import UserContextABC
from src.db.entities.activity import ActivityEntity, ActivityFilterBuilder, ActivityScore
from src.db.repos.note.content import NoteContentRepo
from src.utils import logging_provider as default_logging_provider


_VIEW_PERMISSION = "view"
# Default number of characters of note ``content`` to keep when
# enriching activity rows / scores.  Matches
# :data:`src.api.note_service._DEFAULT_STRIP_CONTENT_AT`; mirrored
# here to avoid an import cycle.
_STRIP_CONTENT_AT: int = 120


def _strip_content(content: Optional[str]) -> Optional[str]:
    """Truncate ``content`` to ``_STRIP_CONTENT_AT`` characters."""
    if not isinstance(content, str):
        return content
    if len(content) > _STRIP_CONTENT_AT:
        return content[:_STRIP_CONTENT_AT]
    return content


class ActivityStatisticsServiceImpl(ActivityStatisticsServiceABC):
    """Postgres-backed activity statistics service.

    Args:
        activity_repo: storage contract used to fetch rows.
        permission_repo: contract used to gate per-target access.
        directory_repo: contract used to resolve "all directories the
            actor can view" when neither ``note_id`` nor
            ``directory_id`` is supplied.
        note_content_repo: storage contract used to enrich returned
            rows with the note title and a stripped preview of the
            note body.  Used by :meth:`get_history` (only when the
            filter pins the query to a single note) and
            :meth:`get_most_used` (always).
        logging_provider: optional logger factory; falls back to
            :func:`src.utils.logging_provider`.
    """

    def __init__(
        self,
        activity_repo: ActivityRepoABC,
        permission_repo: PermissionRepoABC,
        directory_repo: DirectoryFacadeABC,
        note_content_repo: NoteContentRepo,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._activity_repo = activity_repo
        self._permission_repo = permission_repo
        self._directory_repo = directory_repo
        self._note_content_repo = note_content_repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    async def _resolve_visible_directory_ids(
        self, actor: UserContextABC,
    ) -> List[str]:
        """Return every directory id the actor can view."""
        return await self._directory_repo.list_user_directory_ids(actor)

    async def _assert_view_on_note(
        self, actor: UserContextABC, note_id: str,
    ) -> None:
        if not await self._permission_repo.has_permission(
            actor,
            _VIEW_PERMISSION,
            ObjectRef(object_type="note", object_id=note_id),
        ):
            raise PermissionError(
                f"actor {actor.user_id} cannot view note {note_id}"
            )

    async def _assert_view_on_directory(
        self, actor: UserContextABC, directory_id: str,
    ) -> None:
        if not await self._permission_repo.has_permission(
            actor,
            _VIEW_PERMISSION,
            ObjectRef(object_type="directory", object_id=directory_id),
        ):
            raise PermissionError(
                f"actor {actor.user_id} cannot view directory {directory_id}"
            )

    def _apply_kwargs_to_builder(
        self,
        builder: ActivityFilterBuilder,
        *,
        note_id: Optional[str],
        directory_id: Optional[str],
        actor_id: Optional[str] = None,
        actions: Optional[Sequence[str]] = None,
        role_id: Optional[str] = None,
        accessed_as: Optional[str] = None,
        days: Optional[int] = None,
    ) -> None:
        if note_id is not None:
            builder.set_note(note_id)
        if directory_id is not None:
            builder.set_directory(directory_id)
        if actor_id is not None:
            builder.set_user(actor_id)
        if actions:
            builder.set_action_set(*actions)
        if role_id is not None:
            builder.set_role_id(role_id)
        if accessed_as is not None:
            builder.set_accessed_as(accessed_as)  # type: ignore[arg-type]
        if days is not None:
            builder.set_days(days)

    async def get_history(
        self,
        actor: UserContextABC,
        *,
        note_id: Optional[str] = None,
        directory_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        actions: Optional[Sequence[str]] = None,
        role_id: Optional[str] = None,
        accessed_as: Optional[str] = None,
        days: Optional[int] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[ActivityEntity]:
        """See :meth:`api.ActivityStatisticsServiceABC.get_history`."""
        if note_id is not None:
            await self._assert_view_on_note(actor, note_id)
        if directory_id is not None:
            await self._assert_view_on_directory(actor, directory_id)

        builder = ActivityFilterBuilder().use_history()
        self._apply_kwargs_to_builder(
            builder,
            note_id=note_id,
            directory_id=directory_id,
            actor_id=actor_id,
            actions=actions,
            role_id=role_id,
            accessed_as=accessed_as,
            days=days,
        )
        if limit is not None:
            builder.set_limit(limit)
        if offset is not None:
            builder.set_offset(offset)

        if note_id is None and directory_id is None:
            # everything visible to actor -> expand visible dirs.
            for d_id in await self._resolve_visible_directory_ids(actor):
                builder.set_directory(d_id)

        rows = await self._activity_repo.get_activities(builder.build())

        if note_id:
            await self._add_title_and_content(rows, note_id)
        return rows

    async def _add_title_and_content(
        self,
        rows: List[ActivityEntity],
        note_id: str,
    ) -> None:
        """Add ``note_title`` / ``note_stripped_content`` onto each row INPLACE"""
        # get unique note ids from rows
        unique_ids: set[str] = set()
        for r in rows:
            if r.note_id:
                unique_ids.add(r.note_id)
        if not unique_ids:
            return
        notes = await self._note_content_repo.select_by_ids(list(unique_ids))

        # make a map for easy lookup in for loop below
        id_to_note: Dict[str, ActivityEntity] = {
            str(n.note_id): n for n in notes
        }

        # enrich each row with note contents
        for r in rows:
            if not r.note_id:
                continue
            note = id_to_note.get(r.note_id)
            if not note:
                continue
            r.note_title = note.title
            r.note_stripped_content = _strip_content(note.content)
        

    async def _enrich_scores_with_notes(
        self,
        scores: List[ActivityScore],
    ) -> None:
        """Add ``title`` / ``stripped_content`` onto each score row"""
        if not scores:
            return
    
        unique_ids: set[str] = set()
        for s in scores:
            unique_ids.add(s.note_id)

        if not unique_ids:
            return
        try:
            notes = await self._note_content_repo.select_by_ids(list(unique_ids))
        except ValueError:
            return
        title_by_id: Dict[str, Optional[str]] = {
            str(n.note_id): unwrap_undefined_or(n.title, None)
            for n in notes
        }
        content_by_id: Dict[str, Optional[str]] = {
            str(n.note_id): _strip_content(n.content) for n in notes
        }
        for s in scores:
            s.title = title_by_id.get(s.note_id, UNDEFINED)
            s.stripped_content = content_by_id.get(s.note_id, UNDEFINED)

    async def get_most_used(
        self,
        actor: UserContextABC,
        *,
        note_id: Optional[str] = None,
        directory_id: Optional[str] = None,
        actions: Optional[Sequence[str]] = None,
        role_id: Optional[str] = None,
        accessed_as: Optional[str] = None,
        days: Optional[int] = None,
        algorithm: Algorithm = "count",
        unique_per_day: bool = False,
        limit: Optional[int] = None,
    ) -> List[ActivityScore]:
        """See :meth:`ActivityStatisticsServiceABC.get_most_used`."""
        if note_id is not None:
            await self._assert_view_on_note(actor, note_id)
        if directory_id is not None:
            await self._assert_view_on_directory(actor, directory_id)

        builder = ActivityFilterBuilder().show_most_used()
        self._apply_kwargs_to_builder(
            builder,
            note_id=note_id,
            directory_id=directory_id,
            actions=actions,
            role_id=role_id,
            accessed_as=accessed_as,
            days=days,
        )
        if algorithm != "count":
            builder.with_algorithm(algorithm)  # type: ignore[arg-type]
        if unique_per_day:
            builder.unique_per_day()
        if limit is not None:
            builder.set_limit(limit)

        if note_id is None and directory_id is None:
            for d_id in await self._resolve_visible_directory_ids(actor):
                builder.set_directory(d_id)

        scores = await self._activity_repo.get_most_used(builder.build())

        # add note title and content to score
        await self._enrich_scores_with_notes(scores)
        return scores


__all__ = ["ActivityStatisticsServiceImpl"]