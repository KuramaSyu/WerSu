from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Optional, TYPE_CHECKING

from src.api.services.preferences_service import PreferenceScope

if TYPE_CHECKING:
    from src.db.entities.preferences import PreferencesEntity


class PreferencesRepoABC(ABC):
    """Storage contract for the unified preferences table.

    Every method is keyed on (scope, target_id); the repo chooses the
    right owner column (user_id, shelf_id, or role_id).
    """

    @abstractmethod
    async def read(
        self, scope: PreferenceScope, target_id: str
    ) -> Dict[str, Any]:
        """Return the stored blob; {} when no row exists."""
        ...

    @abstractmethod
    async def read_entity(
        self, scope: PreferenceScope, target_id: str
    ) -> Optional["PreferencesEntity"]:
        """Return the entity for (scope, target_id); None when no row exists."""
        ...

    @abstractmethod
    async def upsert_entity(
        self, entity: "PreferencesEntity"
    ) -> None:
        """Insert or update via a fully-shaped entity.

        The entity's owner column (user_id, shelf_id, or role_id)
        and scope must be consistent. updated_at is sourced from the
        entity; if it is UNDEFINED the repo defaults to now().
        """
        ...

    @abstractmethod
    async def upsert(
        self,
        scope: PreferenceScope,
        target_id: str,
        blob: Dict[str, Any],
        updated_at: Optional[datetime] = None,
    ) -> None:
        """Insert or update the row owned by (scope, target_id)."""
        ...

    @abstractmethod
    async def delete(
        self, scope: PreferenceScope, target_id: str
    ) -> bool:
        """Drop the row owned by (scope, target_id); returns true if a row was removed."""
        ...


__all__ = ["PreferencesRepoABC"]