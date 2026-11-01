"""Application service for per-scope preferences."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal, Union

from src.api.other.user_context import UserContextABC


PreferenceScope = Literal["user", "shelf", "role"]
"""Which kind of entity a preference blob is attached to."""

PreferencePayload = Union[str, dict[str, Any]]
"""Caller-supplied JSON.  str is parsed with json.loads; dict is stored verbatim."""


class PreferencesServiceABC(ABC):
    """Application service for per-scope preference storage."""

    @abstractmethod
    async def get_preferences(
        self,
        scope: PreferenceScope,
        target_id: str,
        ctx: UserContextABC,
        key: str = "",
    ) -> dict[str, Any]:
        """Read the preference blob for (target_id, key)."""
        ...

    @abstractmethod
    async def update_preferences(
        self,
        scope: PreferenceScope,
        target_id: str,
        preferences: PreferencePayload,
        ctx: UserContextABC,
        key: str = "",
    ) -> dict[str, Any]:
        """Replace the entire preference blob.  Returns the post-replace state."""
        ...

    @abstractmethod
    async def patch_preferences(
        self,
        scope: PreferenceScope,
        target_id: str,
        preferences: PreferencePayload,
        ctx: UserContextABC,
        key: str = "",
    ) -> dict[str, Any]:
        """Shallow-merge into the stored blob.  Returns the merged state."""
        ...

    @abstractmethod
    async def delete_preferences(
        self,
        scope: PreferenceScope,
        target_id: str,
        ctx: UserContextABC,
        key: str = "",
    ) -> bool:
        """Drop the stored blob.  Returns whether a row was removed."""
        ...


__all__ = ["PreferencePayload", "PreferenceScope", "PreferencesServiceABC"]
