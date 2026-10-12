"""Concrete PreferencesServiceABC implementation."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from src.api import (
    LoggingProvider,
    PermissionRepoABC,
    PreferencePayload,
    PreferenceScope,
    PreferencesRepoABC,
    PreferencesServiceABC,
)
from src.api.other.user_context import UserContextABC
from src.domain.permission_chain import (
    HasRoleManagePerm,
    HasShelfViewPerm,
    IsSelfOrSystem,
)
from src.utils import logging_provider as default_logging_provider


def _coerce_payload(payload: PreferencePayload) -> dict[str, Any]:
    """Parse a string payload as JSON and require the result to be a dict."""
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"preferences must be valid JSON: {exc}") from exc
    else:
        parsed = payload

    if not isinstance(parsed, dict):
        raise ValueError(
            f"preferences must be a JSON object, got {type(parsed).__name__}"
        )
    return parsed


class PreferencesServiceImpl(PreferencesServiceABC):
    """Service-layer implementation backed by a PreferencesRepoABC."""

    def __init__(
        self,
        preferences_repo: PreferencesRepoABC,
        permission_repo: PermissionRepoABC,
        logging_provider: LoggingProvider | None = None,
        get_now: Callable[[], datetime] | None = lambda: datetime.now(),
    ) -> None:
        self._repo = preferences_repo
        self._permission_repo = permission_repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)
        self.get_now = get_now

    async def get_preferences(
        self,
        scope: PreferenceScope,
        target_id: str,
        ctx: UserContextABC,
    ) -> dict[str, Any]:
        """Read the blob, gated on the scope-appropriate read chain."""
        if not target_id:
            raise ValueError("target_id is required")

        if scope == "user":
            chain = IsSelfOrSystem(target_id).set_permission_repo(
                self._permission_repo
            )
        elif scope == "shelf":
            chain = HasShelfViewPerm(target_id).set_permission_repo(
                self._permission_repo
            )
        elif scope == "role":
            chain = HasRoleManagePerm(target_id).set_permission_repo(
                self._permission_repo
            )
        else:
            raise ValueError(f"unsupported preference scope: {scope!r}")

        result = await chain.check(ctx)
        if not result:
            raise result.error  # type: ignore[misc]

        return await self._repo.read(scope, target_id)

    async def update_preferences(
        self,
        scope: PreferenceScope,
        target_id: str,
        preferences: PreferencePayload,
        ctx: UserContextABC,
    ) -> dict[str, Any]:
        """Replace the blob.  Returns the post-replace state."""
        # TODO: gate writes on the scope-appropriate permission chain.
        # Reads already enforce per-scope permission; writes currently
        # rely on the gRPC layer. Revisit once the chain is decided.
        if not target_id:
            raise ValueError("target_id is required")
        parsed = _coerce_payload(preferences)
        await self._repo.upsert(scope, target_id, parsed, self.get_now())
        return parsed

    async def patch_preferences(
        self,
        scope: PreferenceScope,
        target_id: str,
        preferences: PreferencePayload,
        ctx: UserContextABC,
    ) -> dict[str, Any]:
        """Shallow-merge into the existing blob, then upsert."""
        if not target_id:
            raise ValueError("target_id is required")
        parsed = _coerce_payload(preferences)
        merged = {**await self._repo.read(scope, target_id), **parsed}
        await self._repo.upsert(scope, target_id, merged, self.get_now())
        return merged

    async def delete_preferences(
        self,
        scope: PreferenceScope,
        target_id: str,
        ctx: UserContextABC,
    ) -> bool:
        """Drop the row.  Returns whether a row was actually removed."""
        if not target_id:
            raise ValueError("target_id is required")
        return await self._repo.delete(scope, target_id)
