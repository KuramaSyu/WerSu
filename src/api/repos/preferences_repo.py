from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING

from src.api.services.preferences_service import PreferenceScope

if TYPE_CHECKING:
    from src.db.entities.preferences import PreferencesEntity


class PreferencesLimitExceeded(ValueError):
    """Raised when an owner already holds the maximum number of preference rows."""


class PreferencesRepoABC(ABC):
    """Storage contract for the unified preferences table.

    Every method is keyed on (scope, target_id, key); the repo chooses
    the right owner column (user_id, shelf_id, or role_id).  ``key`` is
    optional and defaults to the empty string, which addresses the
    single legacy/default row for that owner.
    """

    @abstractmethod
    async def read(
        self, scope: PreferenceScope, target_id: str, key: str = ""
    ) -> Dict[str, Any]:
        """Return the stored blob for key; {} when no row exists."""
        ...

    @abstractmethod
    async def read_entity(
        self, scope: PreferenceScope, target_id: str, key: str = ""
    ) -> Optional["PreferencesEntity"]:
        """Return the entity for (scope, target_id, key); None when missing."""
        ...

    @abstractmethod
    async def upsert_entity(
        self, entity: "PreferencesEntity"
    ) -> None:
        """Insert or update via a fully-shaped entity.

        The entity's owner column (user_id, shelf_id, or role_id),
        scope and optional key must be consistent. updated_at is
        sourced from the entity; if it is UNDEFINED the repo defaults
        to now().

        Raises:
            PreferencesLimitExceeded: if inserting a new key would
                push the owner past the configured maximum.
        """
        ...

    @abstractmethod
    async def upsert(
        self,
        scope: PreferenceScope,
        target_id: str,
        blob: Dict[str, Any],
        updated_at: Optional[datetime] = None,
        key: str = "",
    ) -> None:
        """Insert or update the row owned by (scope, target_id, key).

        Raises:
            PreferencesLimitExceeded: if inserting a new key would
                push the owner past the configured maximum.
        """
        ...

    @abstractmethod
    async def delete(
        self, scope: PreferenceScope, target_id: str, key: str = ""
    ) -> bool:
        """Drop the row owned by (scope, target_id, key); true iff removed."""
        ...


__all__ = ["PreferencesLimitExceeded", "PreferencesRepoABC"]
