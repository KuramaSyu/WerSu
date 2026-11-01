"""Domain entity for a per-scope preferences row."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.api.other.undefined import UNDEFINED, UndefinedOr
from src.api.other.visitor import AcceptsVisitor
from src.api.services.preferences_service import PreferenceScope


@dataclass
class PreferencesEntity(AcceptsVisitor):
    """One row of the unified preferences table.

    The table is keyed on a single owner (user, shelf, or role).
    """

    id: UndefinedOr[str] = UNDEFINED
    """Server-generated UUID; UNDEFINED on insert, populated on read-back."""

    scope: UndefinedOr[PreferenceScope] = UNDEFINED
    """Which column owns this row."""

    key: UndefinedOr[str] = UNDEFINED
    """Optional caller-chosen key; UNDEFINED/None means the default "" key."""

    user_id: UndefinedOr[str] = UNDEFINED
    """Owner id when scope is user."""

    shelf_id: UndefinedOr[str] = UNDEFINED
    """Owner id when scope is shelf."""

    role_id: UndefinedOr[str] = UNDEFINED
    """Owner id when scope is role."""

    payload: UndefinedOr[dict] = UNDEFINED
    """Stored JSON object."""

    updated_at: UndefinedOr[datetime] = UNDEFINED
    """Last write timestamp."""

    def preference_key(self) -> str:
        """Return the row key, normalising UNDEFINED/None to the default ""."""
        if self.key is UNDEFINED or self.key is None:
            return ""
        return str(self.key)

    def owner_id(self) -> str:
        """Return the non-NULL owner id for this row.

        Raises ValueError if the entity is not shaped correctly.
        """
        owners = [
            v for v in (self.user_id, self.shelf_id, self.role_id)
            if v is not UNDEFINED and v is not None and v != ""
        ]
        if len(owners) != 1:
            raise ValueError(
                "PreferencesEntity must carry exactly one of "
                "user_id, shelf_id, role_id"
            )
        return str(owners[0])

    def visit(self, visitor) -> object:
        """Dispatch this preferences row to visitor.visit_preferences."""
        return visitor.visit_preferences(self)
