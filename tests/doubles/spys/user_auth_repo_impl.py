from __future__ import annotations

import datetime
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional
from src.api.other.undefined import UNDEFINED
from src.api.repos.user_auth_repo import UserAuthRepoABC
from src.api.services.user_service import UserFilter
from src.db.entities.user.passkey import PasskeyEntity
from src.db.entities.user.password import PasswordEntity
from src.db.entities.user.third_party import ThirdPartyEntity, ThirdPartyFilter
from src.db.entities.user.user_auth import UserAuthEntity


@dataclass
class RecordingUserAuthRepo(UserAuthRepoABC):
    """In-memory + recording :class:`UserAuthRepoABC`.

    Stores rows in ``self._by_id`` / ``self._passwords`` /
    ``self._passkeys`` / ``self._third_parties`` so the real impl
    can run against a working backend, and records every method
    call in :attr:`calls` so the test can assert what the impl
    delegated.
    """

    calls: List[_Call] = field(default_factory=list)
    _counter: int = 0
    _by_id: Dict[str, UserAuthEntity] = field(default_factory=dict)
    _by_email: Dict[str, str] = field(default_factory=dict)
    _by_discord_provider: Dict[str, str] = field(default_factory=dict)
    _passwords: Dict[str, PasswordEntity] = field(default_factory=dict)
    _passkeys: Dict[str, PasskeyEntity] = field(default_factory=dict)
    _third_parties: Dict[str, ThirdPartyEntity] = field(default_factory=dict)

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(_Call(name=name, args=args, kwargs=kwargs))

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    # ---- user -------------------------------------------------------

    async def insert(self, user: UserAuthEntity) -> UserAuthEntity:
        self._record("insert", user)
        uid = self._next_id("u")
        created = replace(user, id=uid)
        self._by_id[uid] = created
        if created.email is not None:
            self._by_email[created.email] = uid
        for tp in user.third_parties:
            await self.insert_third_party(replace(tp, user_id=uid))
        return await self.select(UserFilter(user_id=uid)) or created

    async def select(
        self, filter: UserFilter
    ) -> Optional[UserAuthEntity]:
        self._record("select", filter)
        if filter.is_empty():
            return None
        for user in self._by_id.values():
            if (
                not _is_undefined(filter.user_id)
                and user.id != filter.user_id
            ):
                continue
            if (
                not _is_undefined(filter.email)
                and user.email != filter.email
            ):
                continue
            if (
                not _is_undefined(filter.discord_id)
                and user.discord_id() != filter.discord_id
            ):
                continue
            return replace(user, third_parties=list(user.third_parties))
        return None

    async def update(self, user: UserAuthEntity) -> UserAuthEntity:
        self._record("update", user)
        existing = self._by_id.get(user.id)  # type: ignore[arg-type]
        if existing is None:
            raise ValueError(f"User not found: {user.id}")
        merged = existing
        for field_name in ("avatar", "username", "email", "type"):
            new_val = getattr(user, field_name, UNDEFINED)
            if not _is_undefined(new_val):
                merged = replace(merged, **{field_name: new_val})
        if user.third_parties:
            merged = replace(merged, third_parties=list(user.third_parties))
        self._by_id[merged.id] = merged  # type: ignore[arg-type]
        return (
            await self.select(UserFilter(user_id=merged.id)) or merged  # type: ignore[arg-type]
        )

    # ---- password --------------------------------------------------

    async def upsert_password(
        self, password: PasswordEntity
    ) -> PasswordEntity:
        self._record("upsert_password", password)
        self._passwords[password.user_id] = password
        return password

    async def find_password(self, user_id: str) -> Optional[PasswordEntity]:
        self._record("find_password", user_id)
        return self._passwords.get(user_id)

    # ---- passkey ---------------------------------------------------

    async def insert_passkey(self, passkey: PasskeyEntity) -> PasskeyEntity:
        self._record("insert_passkey", passkey)
        pid = self._next_id("pk")
        stored = replace(passkey, id=pid)
        self._passkeys[pid] = stored
        return stored

    async def find_passkey(
        self, credential_id: bytes
    ) -> Optional[PasskeyEntity]:
        self._record("find_passkey", credential_id)
        for pk in self._passkeys.values():
            if pk.credential_id == credential_id:
                return pk
        return None

    async def find_passkey_by_id(
        self, passkey_id: str
    ) -> Optional[PasskeyEntity]:
        self._record("find_passkey_by_id", passkey_id)
        return self._passkeys.get(passkey_id)

    async def list_passkeys(
        self, user_id: str, include_revoked: bool = False
    ) -> List[PasskeyEntity]:
        self._record("list_passkeys", user_id, include_revoked=include_revoked)
        out = []
        for pk in self._passkeys.values():
            if pk.user_id != user_id:
                continue
            if not include_revoked and pk.revoked_at is not None:
                continue
            out.append(pk)
        return out

    async def update_passkey_sign_count(
        self,
        passkey_id: str,
        new_sign_count: int,
    ) -> PasskeyEntity:
        self._record(
            "update_passkey_sign_count", passkey_id, new_sign_count
        )
        pk = self._passkeys[passkey_id]
        updated = replace(pk, sign_count=new_sign_count)
        self._passkeys[passkey_id] = updated
        return updated

    async def revoke_passkey(self, passkey_id: str) -> PasskeyEntity:
        self._record("revoke_passkey", passkey_id)
        pk = self._passkeys[passkey_id]
        if pk.revoked_at is None:
            revoked = replace(pk, revoked_at=datetime.datetime.now())
            self._passkeys[passkey_id] = revoked
            return revoked
        return pk

    # ---- third party ----------------------------------------------

    async def insert_third_party(
        self, third: ThirdPartyEntity
    ) -> ThirdPartyEntity:
        self._record("insert_third_party", third)
        tid = self._next_id("tp")
        stored = replace(third, id=tid)
        self._third_parties[tid] = stored
        key = (stored.provider, stored.provider_user_id)
        self._by_discord_provider[key[1]] = (
            stored.user_id if stored.provider == "discord" else stored.user_id
        )
        return stored

    async def find_third_party(
        self, filter: ThirdPartyFilter
    ) -> List[ThirdPartyEntity]:
        self._record("find_third_party", filter)
        if filter.is_empty():
            return []
        out: List[ThirdPartyEntity] = []
        for tp in self._third_parties.values():
            if (
                not _is_undefined(filter.id)
                and str(tp.id or "") != filter.id
            ):
                continue
            if (
                not _is_undefined(filter.user_id)
                and tp.user_id != filter.user_id
            ):
                continue
            if (
                not _is_undefined(filter.provider)
                and tp.provider != filter.provider
            ):
                continue
            if (
                not _is_undefined(filter.provider_user_id)
                and tp.provider_user_id != filter.provider_user_id
            ):
                continue
            out.append(tp)
        return out

    async def delete_third_party(self, third_party_id: str) -> bool:
        self._record("delete_third_party", third_party_id)
        return self._third_parties.pop(third_party_id, None) is not None


@dataclass
class _Call:
    """A single recorded call on the fake repo."""

    name: str
    args: tuple
    kwargs: dict


def _is_undefined(value: Any) -> bool:
    return value is UNDEFINED


__all__ = [
    'RecordingUserAuthRepo',
    '_Call',
    '_is_undefined',
]
