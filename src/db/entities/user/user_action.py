"""Domain entity and filter for ``user_action`` rows.

Mirrors the schema created by the
``20260620-create-share-relation`` migration:

* ``id``          uuidv7 primary key, populated by the database.
* ``user_id``     user the action targets.
* ``action``      one of ``disable`` / ``enable`` / ``delete``.
* ``execute_at``  timestamp at which the action should run.
* ``executed_at`` timestamp at which it was actually executed
                  (``NULL`` while pending).

``UNDEFINED`` on a dataclass field means "not set / leave alone";
``None`` means "explicitly NULL". This matches the convention used by
``NoteShareEntity`` and ``FilterShareNote``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr


UserActionKind = Literal["disable", "enable", "delete"]


@dataclass
class UserActionEntity:
    """Represents a scheduled user-action row.

    Use ``UNDEFINED`` for fields that are not yet set (the repo will
    populate them). Use ``None`` to explicitly persist a NULL.
    """

    # uuidv7 primary key; the DB fills this in when omitted.
    id: UndefinedOr[str] = UNDEFINED

    # the user the action targets; required.
    user_id: UndefinedOr[str] = UNDEFINED

    # one of "disable" / "enable" / "delete"; required.
    action: UndefinedOr[UserActionKind] = UNDEFINED

    # when the action should fire; required.
    execute_at: UndefinedOr[datetime] = UNDEFINED

    # when the action actually ran; ``NULL`` while pending.
    executed_at: UndefinedNoneOr[datetime] = UNDEFINED


@dataclass
class FilterUserAction:
    """Filter criteria for ``user_action`` lookups.

    Semantics for each field:

    * ``UNDEFINED`` -> the column is ignored.
    * ``None`` on ``executed_at`` matches rows where the column IS NULL.
    * A concrete datetime on ``executed_at`` matches rows where the
      column is NOT NULL and ``<=`` the provided value (i.e. it
      captures everything that happened up to that point).
    * A concrete datetime on ``execute_at`` matches rows where
      ``execute_at >=`` the provided value (i.e. everything scheduled
      at or after that point, including future entries).
    * Other string fields are exact matches when set.
    """

    id: UndefinedOr[str] = UNDEFINED
    user_id: UndefinedOr[str] = UNDEFINED
    action: UndefinedOr[UserActionKind] = UNDEFINED

    # ``None`` -> IS NULL. datetime -> ``executed_at <= value``.
    executed_at: UndefinedNoneOr[datetime] = UNDEFINED

    # datetime -> ``execute_at >= value``.
    execute_at: UndefinedOr[datetime] = UNDEFINED


__all__ = [
    "UserActionKind",
    "UserActionEntity",
    "FilterUserAction",
]