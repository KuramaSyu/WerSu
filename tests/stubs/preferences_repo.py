"""In-memory PreferencesRepoABC fake for unit tests.

Stores blobs in a flat dict keyed on (scope, target_id) so the
service-under-test sees deterministic CRUD semantics. Each call is
also recorded so tests can assert on what the service passed
through without having to introspect the storage back to the
original blob.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.repos.preferences_repo import PreferencesRepoABC
from src.api.services.preferences_service import PreferenceScope
from src.db.entities.preferences import PreferencesEntity


class _FakePreferencesRepo(PreferencesRepoABC):
    """Record-and-replay preferences repo for unit tests.

    Args:
        initial: optional initial blobs keyed on (scope, target_id)
            tuples.
        raise_on_read: scope/target combinations that should raise
            RuntimeError when read; useful for surfacing error paths
            through the service.
    """

    def __init__(
        self,
        initial: Optional[Dict[Tuple[PreferenceScope, str], Dict[str, Any]]] = None,
        raise_on_read: Optional[List[Tuple[PreferenceScope, str]]] = None,
    ) -> None:
        self._store: Dict[Tuple[PreferenceScope, str], Dict[str, Any]] = {
            key: dict(value) for key, value in (initial or {}).items()
        }
        self.read_calls: List[Tuple[PreferenceScope, str]] = []
        self.upsert_calls: List[
            Tuple[PreferenceScope, str, Dict[str, Any], Optional[datetime]]
        ] = []
        self.delete_calls: List[Tuple[PreferenceScope, str]] = []
        self.upsert_entity_calls: List[Any] = []
        self._raise_on_read = set(raise_on_read or [])

    async def read(
        self, scope: PreferenceScope, target_id: str
    ) -> Dict[str, Any]:
        self.read_calls.append((scope, target_id))
        if (scope, target_id) in self._raise_on_read:
            raise RuntimeError(
                f"fake preferences repo read failure: ({scope}, {target_id})"
            )
        return copy.deepcopy(self._store.get((scope, target_id), {}))

    async def upsert(
        self,
        scope: PreferenceScope,
        target_id: str,
        blob: Dict[str, Any],
        updated_at: Optional[datetime] = None,
    ) -> None:
        self.upsert_calls.append((scope, target_id, copy.deepcopy(blob), updated_at))
        self._store[(scope, target_id)] = copy.deepcopy(blob)

    async def read_entity(
        self, scope: PreferenceScope, target_id: str
    ) -> Optional[PreferencesEntity]:
        """Read the blob back as a PreferencesEntity; None when missing."""
        self.read_calls.append((scope, target_id))
        if (scope, target_id) in self._raise_on_read:
            raise RuntimeError(
                f"fake preferences repo read failure: ({scope}, {target_id})"
            )
        blob = self._store.get((scope, target_id))
        if blob is None:
            return None
        kwargs: Dict[str, Any] = {
            "scope": scope,
            "payload": copy.deepcopy(blob),
        }
        if scope == "user":
            kwargs["user_id"] = target_id
        elif scope == "shelf":
            kwargs["shelf_id"] = target_id
        else:
            kwargs["role_id"] = target_id
        return PreferencesEntity(id=UNDEFINED, **kwargs)

    async def upsert_entity(self, entity: PreferencesEntity) -> None:
        """Upsert via a PreferencesEntity; delegates to the blob store."""
        self.upsert_entity_calls.append(entity)
        scope = entity.scope
        target_id = None
        if scope == "user":
            target_id = entity.user_id
        elif scope == "shelf":
            target_id = entity.shelf_id
        elif scope == "role":
            target_id = entity.role_id
        if not target_id or is_undefined(target_id):
            raise ValueError("PreferencesEntity missing target_id for its scope")
        ts = entity.updated_at if not is_undefined(entity.updated_at) else None
        await self.upsert(
            scope,
            str(target_id),
            copy.deepcopy(entity.payload or {}),
            updated_at=ts,
        )

    async def delete(
        self, scope: PreferenceScope, target_id: str
    ) -> bool:
        self.delete_calls.append((scope, target_id))
        return self._store.pop((scope, target_id), None) is not None

    # --- introspection helpers (test-only) -----------------------------
    def stored(self, scope: PreferenceScope, target_id: str) -> Dict[str, Any]:
        """Return the *currently stored* blob (deep copy)."""
        return copy.deepcopy(self._store.get((scope, target_id), {}))


__all__ = ["_FakePreferencesRepo"]