"""Domain entity and filter for ``activity`` rows.

Mirrors the schema created by the
``20260707-create-activity-log`` migration:

* ``id``           uuidv7 primary key, populated by the database.
* ``actor_id``     user who performed the action; NULL for anonymous.
                   ``ON DELETE SET NULL`` -- the row outlives its actor.
* ``accessed_as``  whether the actor was the user themselves or the
                   system acting on their behalf.  One of ``"user"`` /
                   ``"system"``; defaults to ``"user"``.
* ``action``       ENUM of the event kind.
* ``note_id``      target note id when ``action`` starts with ``note_``;
                   NULL otherwise.
* ``directory_id`` target directory id when ``action`` starts with
                   ``directory_``; NULL otherwise.
* ``role_id``      affected role id when ``action`` is one of
                   ``role_grant`` / ``role_revoke`` / ``role_change``;
                   NULL otherwise.
* ``at``           ``TIMESTAMPTZ`` of the event (DB default ``NOW()``).
* ``metadata``     JSONB action-specific payload (e.g. ``{"added":
                   [...], "removed": [...]}`` on ``role_change``).

The target-shape invariant (exactly one of note / directory / role_id
is set, matching the action prefix) is enforced by
:class:`ActivityLoggerService` and re-checked by the repo; the schema
deliberately has no CHECK so future kinds can be added without a DDL
round-trip.

``UNDEFINED`` on a dataclass field means "not set / leave alone";
``None`` means "explicitly NULL".  This matches the convention used by
:class:`UserActionEntity` and :class:`FilterUserAction`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, List, Literal, Mapping

from src.api.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.api.visitor import AcceptsVisitor, EntityVisitor


ActivityKind = Literal[
    # note-target
    "note_viewed",
    "note_created",
    "note_edited",
    "note_deleted",
    "note_published",
    "note_shared",
    "note_restored",
    "note_archived",
    "note_version_restored",
    "note_attachment_added",
    # directory-target
    "directory_created",
    "directory_viewed",
    "directory_edited",
    "directory_deleted",
    # role-target (global roles; scope rides in ``role_id`` / metadata)
    "role_grant",
    "role_revoke",
    "role_change",
]


ActorAs = Literal["user", "system"]


@dataclass
class ActivityEntity(AcceptsVisitor):
    """Represents a single row of the ``activity`` log.

    Use :obj:`~src.api.undefined.UNDEFINED` for fields that are not yet
    set (the repo will populate them).  Use :obj:`None` to explicitly
    persist a NULL.
    """

    # uuidv7 primary key; the DB fills this in when omitted.
    id: UndefinedOr[str] = UNDEFINED

    # the user who performed the action; ``ON DELETE SET NULL``.
    actor_id: UndefinedNoneOr[str] = UNDEFINED

    # whether the actor was the user or the system acting on their
    # behalf.  Defaults to ``"user"``; the logger service reads this
    # from the ``UserContextABC``.
    accessed_as: UndefinedOr[ActorAs] = UNDEFINED

    # one of the ``ActivityKind`` literals; required.
    action: UndefinedOr[ActivityKind] = UNDEFINED

    # exactly one of these is set, matching the action prefix.  The
    # logger service + repo validate the shape per kind.
    note_id: UndefinedNoneOr[str] = UNDEFINED
    directory_id: UndefinedNoneOr[str] = UNDEFINED
    role_id: UndefinedNoneOr[str] = UNDEFINED

    # when the action happened; DB default ``NOW()`` when omitted.
    at: UndefinedOr[datetime] = UNDEFINED

    # action-specific payload (JSONB).
    metadata: UndefinedOr[Mapping[str, object]] = UNDEFINED

    def visit(self, visitor: EntityVisitor) -> Any:
        """Dispatch this activity row to ``visitor.visit_activity``."""
        return visitor.visit_activity(self)


@dataclass
class ActivityScore(AcceptsVisitor):
    """Result row from a ``most_used`` aggregate query.

    Unlike :class:`ActivityEntity`, this row carries no actor, action,
    or timestamp -- the aggregation collapses many events into one
    ``(note_id, score)`` pair per note.  ``score`` is the value the
    chosen strategy computed (raw count or log-flattened count).
    """

    note_id: str
    score: float

    def visit(self, visitor: EntityVisitor) -> Any:
        """Dispatch this score to ``visitor.visit_activity_score``."""
        return visitor.visit_activity_score(self)


@dataclass
class FilterActivity:
    """Filter criteria for ``activity`` lookups.

    Semantics for each field:

    * :obj:`~src.api.undefined.UNDEFINED` -> the column is ignored.
    * :obj:`None` on nullable columns (``actor_id``, ``note_id``,
      ``directory_id``, ``role_id``) -> ``IS NULL``.
    * Concrete values -> exact match for string columns.
    * ``action_set`` -> ``action = ANY($N)``; useful when a caller
      wants to ask for "any role-change action" or "any note action"
      without listing each value.
    * ``directory_ids`` -> list of directory roots; the repo expands
      each into its subtree via :meth:`DirectoryRepo.resolve_subtree`.
    * ``days`` -> ``at >= NOW() - INTERVAL 'N days'``; ``None``
      disables the time window.
    * ``limit`` / ``offset`` -> pagination.

    Modes (``mode``):

    * ``"history"`` -- plain reverse-chronological list.  This is the
      default mode.
    * ``"most_used"`` -- aggregate ranking.  The repo dispatches to a
      strategy chosen by ``algorithm``; ``unique_per_day`` collapses
      repeats to one count per actor per day before aggregating.
    """

    actor_id: UndefinedNoneOr[str] = UNDEFINED
    accessed_as: UndefinedOr[ActorAs] = UNDEFINED
    action: UndefinedOr[ActivityKind] = UNDEFINED
    action_set: UndefinedOr[tuple[ActivityKind, ...]] = UNDEFINED

    note_id: UndefinedNoneOr[str] = UNDEFINED
    # list of directory roots; each is expanded to a subtree at query
    # time.  ``None`` means "match no rows"; ``UNDEFINED`` means
    # "no directory filter".
    directory_ids: UndefinedNoneOr[List[str]] = UNDEFINED
    role_id: UndefinedNoneOr[str] = UNDEFINED

    days: UndefinedNoneOr[int] = UNDEFINED
    limit: UndefinedOr[int] = UNDEFINED
    offset: UndefinedOr[int] = UNDEFINED

    mode: UndefinedOr[Literal["history", "most_used"]] = UNDEFINED
    algorithm: UndefinedOr[Literal["count", "log_count"]] = UNDEFINED
    unique_per_day: UndefinedOr[bool] = UNDEFINED


class ActivityFilterBuilder:
    """Fluent constructor for :class:`FilterActivity`.

    Use one of the mode-setters first (``use_history`` or
    ``show_most_used``), then layer filters, then ``build()`` to
    produce the immutable :class:`FilterActivity`.

    Examples
    --------

    History of note X for the last 30 days, paginated::

        ActivityFilterBuilder() \\
            .use_history() \\
            .set_note(note_id) \\
            .set_days(30) \\
            .set_limit(50) \\
            .build()

    Most-viewed notes in a directory subtree, last 30 days,
    log-flattened scoring, one count per actor per day::

        ActivityFilterBuilder() \\
            .show_most_used() \\
            .with_algorithm("log_count") \\
            .unique_per_day() \\
            .set_directory(directory_id) \\
            .set_days(30) \\
            .build()

    Notes
    -----
    * ``set_directory`` is *additive*: every call appends another root
      to ``directory_ids``.  The repo expands each root to its
      subtree at query time.
    * For ``most_used`` mode, ``algorithm`` defaults to ``"count"`` when
      not set explicitly.  Setting ``algorithm`` without ``most_used``
      mode raises :class:`ValueError`.
    * ``unique_per_day`` only applies to ``most_used`` mode; setting it
      outside that mode raises :class:`ValueError`.
    """

    def __init__(self) -> None:
        self._filter = FilterActivity()
        # Internal accumulator for ``set_directory``.  Each call
        # appends; we copy into the filter on ``build()`` so the
        # builder stays immutable from the caller's perspective.
        self._directory_ids: List[str] = []


    def use_history(self) -> "ActivityFilterBuilder":
        """Switch to plain reverse-chronological list mode."""
        self._filter = replace(self._filter, mode="history")
        return self

    def show_most_used(self) -> "ActivityFilterBuilder":
        """Switch to aggregated most-used ranking mode."""
        self._filter = replace(self._filter, mode="most_used")
        return self


    def set_note(self, note_id: str) -> "ActivityFilterBuilder":
        """Restrict to a single note.  Replaces any prior note id."""
        self._filter = replace(self._filter, note_id=note_id)
        return self

    def set_directory(self, directory_id: str) -> "ActivityFilterBuilder":
        """Add ``directory_id`` to the list of subtree roots.

        The repo expands every root in the list into the full set of
        note / directory ids reachable from that root.  This builder
        only accumulates ids; the expansion happens at query time.

        Calling ``set_directory`` multiple times composes -- it does
        NOT replace prior ids.
        """
        self._directory_ids.append(directory_id)
        return self


    def set_user(self, user_id: str) -> "ActivityFilterBuilder":
        """Restrict to events performed by ``user_id``."""
        self._filter = replace(self._filter, actor_id=user_id)
        return self

    def set_accessed_as(self, accessed_as: ActorAs = "user") -> "ActivityFilterBuilder":
        """Restrict to events whose actor was acting as ``accessed_as``.

        Args:
            accessed_as: ``"user"`` for actions taken by the user
                themselves, ``"system"`` for actions taken by the
                system on the user's behalf.  Defaults to ``"user"``.
        """
        self._filter = replace(self._filter, accessed_as=accessed_as)
        return self

    def set_role_id(self, role_id: str) -> "ActivityFilterBuilder":
        """Restrict to events affecting the role with ``role_id``."""
        self._filter = replace(self._filter, role_id=role_id)
        return self

    def set_action(self, action: ActivityKind) -> "ActivityFilterBuilder":
        """Restrict to a single action kind."""
        self._filter = replace(self._filter, action=action)
        return self

    def set_action_set(self, *actions: ActivityKind) -> "ActivityFilterBuilder":
        """Restrict to any of the given action kinds (``action = ANY(...)``)."""
        self._filter = replace(self._filter, action_set=tuple(actions))
        return self


    def set_days(self, days: int) -> "ActivityFilterBuilder":
        """Restrict to events within the last ``days`` days."""
        self._filter = replace(self._filter, days=days)
        return self

    def set_limit(self, limit: int) -> "ActivityFilterBuilder":
        """Cap the number of returned rows."""
        self._filter = replace(self._filter, limit=limit)
        return self

    def set_offset(self, offset: int) -> "ActivityFilterBuilder":
        """Skip the first ``offset`` rows."""
        self._filter = replace(self._filter, offset=offset)
        return self


    def with_algorithm(self, algorithm: Literal["count", "log_count"]) -> "ActivityFilterBuilder":
        """Pick the scoring algorithm for ``most_used`` mode."""
        self._filter = replace(self._filter, algorithm=algorithm)
        return self

    def unique_per_day(self) -> "ActivityFilterBuilder":
        """Collapse repeats to one count per actor per day before aggregating.

        Only meaningful in ``most_used`` mode.
        """
        self._filter = replace(self._filter, unique_per_day=True)
        return self


    def build(self) -> FilterActivity:
        """Validate the accumulated filter and return it.

        Raises:
            ValueError: if ``mode`` was never set, if ``algorithm`` /
                ``unique_per_day`` were set without ``most_used``
                mode, or if ``days`` is non-positive.
        """
        # Materialise the directory accumulator into the filter.
        if self._directory_ids:
            self._filter = replace(
                self._filter,
                directory_ids=list(self._directory_ids),
            )

        f = self._filter
        if f.mode is UNDEFINED:
            raise ValueError(
                "ActivityFilterBuilder: call use_history() or show_most_used() first"
            )
        if f.algorithm is not UNDEFINED and f.mode != "most_used":
            raise ValueError(
                "ActivityFilterBuilder: with_algorithm() requires show_most_used()"
            )
        if f.unique_per_day is not UNDEFINED and f.mode != "most_used":
            raise ValueError(
                "ActivityFilterBuilder: unique_per_day() requires show_most_used()"
            )
        if f.days is not UNDEFINED and f.days is not None and f.days <= 0:
            raise ValueError("ActivityFilterBuilder: days must be a positive integer")
        return f


__all__ = [
    "ActivityKind",
    "ActorAs",
    "ActivityEntity",
    "ActivityScore",
    "FilterActivity",
    "ActivityFilterBuilder",
]