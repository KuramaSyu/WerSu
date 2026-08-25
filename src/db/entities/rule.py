from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping

from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.api.other.visitor import AcceptsVisitor


AttachedEntityType = Literal["directory", "note", "shelf"]
"""Allowed values for :attr:`RuleEntity.attached_entity_type`.

When ``attached_entity_type`` is set, the matching scope is
computed by the dispatcher:

* ``"directory"`` -- the rule matches events whose primary
  entity is this directory or any of its descendants.
* ``"note"`` -- the rule matches events whose primary entity is
  this note.
* ``"shelf"`` -- the rule matches events whose primary entity is
  a book sitting on this shelf (or, for note events, a note
  inside such a book).

``attached_entity_type`` and ``attached_entity_id`` are both
required -- "global" rules (one or both unset) are no longer
accepted.  The dispatcher rejects any rule whose attached-entity
fields are not both set.
"""


@dataclass
class RuleEntity(AcceptsVisitor):
    """Represents a single row of the ``rules`` table.
    
    Mirrors the schema created by the ``rules.sql`` migration:

    * ``id``                  uuid primary key, populated by the database.
    * ``event_type``          the event this rule listens for
                            (e.g. ``"NoteUpdated"``).
    * ``attached_entity_type`` optional scope anchor (e.g. ``"directory"``).
                            When ``None`` the rule fires for every event
                            of its ``event_type`` (a "global" rule).
    * ``attached_entity_id``  id of the scope anchor; required when
                            ``attached_entity_type`` is set.
    * ``condition``           JSONB payload describing the condition
                            variant + its parameters.
    * ``action_type``         discriminator for the action variant.
    * ``action_context``      JSONB payload carrying the variant's
                            parameters (e.g. ``{"directory_id": "..."}``).
    * ``enabled``             paused rules keep their row but never fire.
    * ``creator_id``          user that created the rule; used for audit
                            and the "rules I created" UI.
    * ``created_at`` / ``updated_at``  timestamps; the DB fills them in
                            when omitted.

    The ``condition`` and ``action_context`` shapes are validated at the
    service layer against the dataclass unions in
    :mod:`src.api.events.conditions` and :mod:`src.api.events.actions`
    respectively.  This module only carries the raw JSONB; it does not
    parse the payloads.

    ``UNDEFINED`` on a dataclass field means "not set / leave alone";
    ``None`` means "explicitly NULL" (only meaningful for the
    ``attached_entity_*`` columns).
    """

    # uuid primary key; the DB fills this in when omitted.
    id: UndefinedOr[str] = UNDEFINED

    # the event kind this rule reacts to (e.g. ``"NoteUpdated"``).
    event_type: UndefinedOr[str] = UNDEFINED

    # optional scope anchor.  ``None`` means the rule is global.
    attached_entity_type: UndefinedNoneOr[AttachedEntityType] = UNDEFINED
    attached_entity_id: UndefinedNoneOr[str] = UNDEFINED

    # condition discriminator + parameters; JSONB on the row.
    condition: UndefinedOr[Mapping[str, object]] = UNDEFINED

    # action discriminator + parameters; split into two columns so the
    # discriminator is indexable / filterable without parsing JSONB.
    action_type: UndefinedOr[str] = UNDEFINED
    action_context: UndefinedOr[Mapping[str, object]] = UNDEFINED

    # paused rules keep their row but never fire.
    enabled: UndefinedOr[bool] = UNDEFINED

    # audit + "rules I created" lookup.
    creator_id: UndefinedOr[str] = UNDEFINED

    # DB-managed timestamps; populated when omitted.
    created_at: UndefinedOr[datetime] = UNDEFINED
    updated_at: UndefinedOr[datetime] = UNDEFINED

    def visit(self, visitor: Any) -> Any:
        """Dispatch this rule to ``visitor.visit_rule``."""
        return visitor.visit_rule(self)
