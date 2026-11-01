from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.repos.preferences_repo import PreferencesRepoABC
from src.api.services.preferences_service import PreferenceScope
from src.db.entities.preferences import PreferencesEntity


_StoreKey = Tuple[PreferenceScope, str, str]


class _FakeSpyPreferencesRepo(PreferencesRepoABC):
    """Record-and-replay preferences repo for unit tests.

    Rows are keyed on (scope, target_id, key); the empty key is the
    default/legacy row.

    Args:
        initial: optional initial blobs keyed on
            (scope, target_id, key) tuples.  Legacy two-tuples
            (scope, target_id) are accepted and mapped to key "".
        raise_on_read: scope/target combinations that should raise
            RuntimeError when read; useful for surfacing error paths
            through the service.
    """

    def __init__(
        self,
        initial: Optional[Dict[Tuple, Dict[str, Any]]] = None,
        raise_on_read: Optional[List[Tuple[PreferenceScope, str]]] = None,
    ) -> None:
        self._store: Dict[_StoreKey, Dict[str, Any]] = {}
        for raw_key, value in (initial or {}).items():
            scope, target_id, key = self._normalise_store_key(raw_key)
            self._store[(scope, target_id, key)] = dict(value)
        self.read_calls: List[Tuple[PreferenceScope, str, str]] = []
        self.upsert_calls: List[
            Tuple[
                PreferenceScope,
                str,
                Dict[str, Any],
                Optional[datetime],
                str,
            ]
        ] = []
        self.delete_calls: List[Tuple[PreferenceScope, str, str]] = []
        self.upsert_entity_calls: List[Any] = []
        self._raise_on_read = set(raise_on_read or [])

    @staticmethod
    def _normalise_store_key(raw_key: Tuple) -> _StoreKey:
        """Accept both (scope, target) and (scope, target, key) shapes."""
        if len(raw_key) == 2:
            scope, target_id = raw_key
            return (scope, target_id, "")
        scope, target_id, key = raw_key
        return (scope, target_id, key or "")

    async def read(
        self, scope: PreferenceScope, target_id: str, key: str = ""
    ) -> Dict[str, Any]:
        self.read_calls.append((scope, target_id, key))
        if (scope, target_id) in self._raise_on_read:
            raise RuntimeError(
                f"fake preferences repo read failure: ({scope}, {target_id})"
            )
        return copy.deepcopy(self._store.get((scope, target_id, key), {}))

    async def upsert(
        self,
        scope: PreferenceScope,
        target_id: str,
        blob: Dict[str, Any],
        updated_at: Optional[datetime] = None,
        key: str = "",
    ) -> None:
        self.upsert_calls.append(
            (scope, target_id, copy.deepcopy(blob), updated_at, key)
        )
        self._store[(scope, target_id, key)] = copy.deepcopy(blob)

    async def read_entity(
        self, scope: PreferenceScope, target_id: str, key: str = ""
    ) -> Optional[PreferencesEntity]:
        """Read the blob back as a PreferencesEntity; None when missing."""
        self.read_calls.append((scope, target_id, key))
        if (scope, target_id) in self._raise_on_read:
            raise RuntimeError(
                f"fake preferences repo read failure: ({scope}, {target_id})"
            )
        blob = self._store.get((scope, target_id, key))
        if blob is None:
            return None
        kwargs: Dict[str, Any] = {
            "scope": scope,
            "key": key,
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
            key=entity.preference_key(),
        )

    async def delete(
        self, scope: PreferenceScope, target_id: str, key: str = ""
    ) -> bool:
        self.delete_calls.append((scope, target_id, key))
        return self._store.pop((scope, target_id, key), None) is not None

    # --- introspection helpers (test-only) -----------------------------
    def stored(
        self, scope: PreferenceScope, target_id: str, key: str = ""
    ) -> Dict[str, Any]:
        """Return the *currently stored* blob (deep copy)."""
        return copy.deepcopy(self._store.get((scope, target_id, key), {}))


__all__ = [
    '_FakeSpyPreferencesRepo',
]
