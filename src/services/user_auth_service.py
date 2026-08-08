"""Concrete implementations of the auth-side service contracts.

Four impls sit on top of :class:`UserAuthRepoABC`.  The repo
does the SQL; the services do the policy.  Business logic that
doesn't belong in either (e.g. WebAuthn ceremony verification)
lives in the REST controller, not here.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import replace
from typing import List, Optional

from src.api.other.types import LoggingProvider
from src.api.other.undefined import is_undefined
from src.api.repos.user_auth_repo import UserAuthRepoABC
from src.api.services.user_auth_service import (
    UserAuthServiceABC,
    UserPasskeyAuthServiceABC,
    UserPasswordAuthServiceABC,
    UserThirdPartyAuthServiceABC,
)
from src.api.services.user_service import UserFilter
from src.db.entities.user.passkey import PasskeyEntity
from src.db.entities.user.password import PasswordEntity
from src.db.entities.user.third_party import (
    DiscordLink,
    GoogleLink,
    ThirdPartyEntity,
    ThirdPartyFilter,
    ThirdPartyLinkSpec,
)
from src.db.entities.user.user_auth import UserAuthEntity
from src.utils.logging import logging_provider as default_logging_provider


class UserPasswordAuthServiceImpl(UserPasswordAuthServiceABC):
    """Password-hash service backed by :class:`UserAuthRepoABC`."""

    def __init__(
        self,
        repo: UserAuthRepoABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._repo = repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    async def set_user_password(
        self,
        user_id: str,
        requester_id: str,
        password_hash: str,
    ) -> PasswordEntity:
        """Insert or replace the user's password row."""
        if requester_id != user_id:
            self.log.warning(
                f"set_user_password rejected: requester_id={requester_id} != user_id={user_id}"
            )
            raise PermissionError("requester_id must equal user_id")
        self.log.debug(f"set_user_password user_id={user_id}")
        return await self._repo.upsert_password(
            PasswordEntity(
                user_id=user_id,
                password_hash=password_hash,
                created_at=_dt.datetime.now(),
                updated_at=_dt.datetime.now(),
            )
        )

    async def find_password(
        self, user_id: str
    ) -> Optional[PasswordEntity]:
        """Return the user's password row, or ``None``."""
        self.log.debug(f"find_password user_id={user_id}")
        return await self._repo.find_password(user_id)


class UserPasskeyAuthServiceImpl(UserPasskeyAuthServiceABC):
    """Passkey service backed by :class:`UserAuthRepoABC`."""

    def __init__(
        self,
        repo: UserAuthRepoABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._repo = repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    async def register_passkey(
        self,
        user_id: str,
        requester_id: str,
        passkey: PasskeyEntity,
    ) -> PasskeyEntity:
        """Insert a new passkey."""
        if requester_id != user_id:
            self.log.warning(
                f"register_passkey rejected: requester_id={requester_id} != user_id={user_id}"
            )
            raise PermissionError("requester_id must equal user_id")
        self.log.debug(f"register_passkey user_id={user_id}")
        # Stamp the user_id / created_at from the request -- the
        # caller may have left them unset, but the request itself
        # is the source of truth for the ownership.
        to_insert = replace(
            passkey,
            id=passkey.id if not is_undefined(passkey.id) else "",
            user_id=user_id,
            created_at=passkey.created_at or _dt.datetime.now(),
        )
        return await self._repo.insert_passkey(to_insert)

    async def find_passkey(
        self, credential_id: bytes
    ) -> Optional[PasskeyEntity]:
        """Find a passkey by its credential id."""
        self.log.debug(f"find_passkey credential_id={credential_id!r}")
        return await self._repo.find_passkey(credential_id)

    async def list_passkeys(
        self,
        user_id: str,
        include_revoked: bool = False,
    ) -> list[PasskeyEntity]:
        """List the user's passkeys (revoked hidden by default)."""
        self.log.debug(
            f"list_passkeys user_id={user_id} include_revoked={include_revoked}"
        )
        return await self._repo.list_passkeys(
            user_id, include_revoked=include_revoked
        )

    async def update_sign_count(
        self,
        passkey_id: str,
        new_sign_count: int,
        requester_id: str,
    ) -> PasskeyEntity:
        """Bump the sign counter on a passkey."""
        existing = await self._repo.find_passkey_by_id(passkey_id)
        if existing is None:
            self.log.warning(f"update_sign_count: passkey_id={passkey_id} not found")
            raise KeyError(f"passkey not found: {passkey_id}")
        if requester_id != existing.user_id:
            self.log.warning(
                f"update_sign_count rejected: requester_id={requester_id} != "
                f"passkey.user_id={existing.user_id}"
            )
            raise PermissionError("requester_id must equal user_id")
        self.log.debug(
            f"update_sign_count passkey_id={passkey_id} new_sign_count={new_sign_count}"
        )
        updated = await self._repo.update_passkey_sign_count(
            passkey_id, new_sign_count
        )
        return replace(updated, last_used_at=_dt.datetime.now())

    async def revoke_passkey(
        self,
        passkey_id: str,
        requester_id: str,
    ) -> PasskeyEntity:
        """Revoke a passkey."""
        existing = await self._repo.find_passkey_by_id(passkey_id)
        if existing is None:
            self.log.warning(f"revoke_passkey: passkey_id={passkey_id} not found")
            raise KeyError(f"passkey not found: {passkey_id}")
        if requester_id != existing.user_id:
            self.log.warning(
                f"revoke_passkey rejected: requester_id={requester_id} != "
                f"passkey.user_id={existing.user_id}"
            )
            raise PermissionError("requester_id must equal user_id")
        self.log.debug(f"revoke_passkey passkey_id={passkey_id}")
        return await self._repo.revoke_passkey(passkey_id)


class UserThirdPartyAuthServiceImpl(UserThirdPartyAuthServiceABC):
    """Third-party-link service backed by :class:`UserAuthRepoABC`."""

    def __init__(
        self,
        repo: UserAuthRepoABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._repo = repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    async def find_third_party(
        self, filter: ThirdPartyFilter
    ) -> List[ThirdPartyEntity]:
        """Return every third-party link matching `filter`."""
        return await self._repo.find_third_party(filter)

    async def link_third_party(
        self,
        user_id: str,
        requester_id: str,
        spec: ThirdPartyLinkSpec,
    ) -> ThirdPartyEntity:
        """Attach a third-party link to an existing user.

        `spec` is either a :class:`DiscordLink` or :class:`GoogleLink`;
        the provider and provider_user_id are inferred from the
        payload's type.  Discord's optional 4-digit ``discriminator``
        is folded into ``extra_fields`` so the OAuth signup path
        can hand it over without a separate parameter.
        """
        if requester_id != user_id:
            self.log.warning(
                f"link_third_party rejected: requester_id={requester_id} != user_id={user_id}"
            )
            raise PermissionError("requester_id must equal user_id")

        if isinstance(spec, DiscordLink):
            link = ThirdPartyEntity(
                user_id=user_id,
                provider="discord",
                provider_user_id=str(spec.discord_id),
            )
            if spec.discriminator:
                link.set_extra("discriminator", spec.discriminator)
            self.log.debug(
                f"link_third_party user_id={user_id} discord_id={spec.discord_id}"
            )
        elif isinstance(spec, GoogleLink):
            link = ThirdPartyEntity(
                user_id=user_id,
                provider="google",
                provider_user_id=spec.google_id,
            )
            self.log.debug(
                f"link_third_party user_id={user_id} google_id={spec.google_id}"
            )
        else:  # pragma: no cover -- type checker
            raise TypeError(
                f"unsupported link spec: {type(spec).__name__}"
            )

        return await self._repo.insert_third_party(link)

    async def unlink_third_party(
        self,
        third_party_id: str,
        requester_id: str,
    ) -> bool:
        """Remove a third-party link by its id."""
        links = await self._repo.find_third_party(
            ThirdPartyFilter(id=third_party_id)
        )
        if not links:
            self.log.warning(
                f"unlink_third_party: third_party_id={third_party_id} not found"
            )
            raise KeyError(f"third_party not found: {third_party_id}")
        existing = links[0]
        if requester_id != existing.user_id:
            self.log.warning(
                f"unlink_third_party rejected: requester_id={requester_id} != "
                f"third_party.user_id={existing.user_id}"
            )
            raise PermissionError("requester_id must equal user_id")
        self.log.debug(f"unlink_third_party third_party_id={third_party_id}")
        return await self._repo.delete_third_party(third_party_id)


class UserAuthServiceImpl(UserAuthServiceABC):
    """Auth-side service backed by :class:`UserAuthRepoABC`.

    Wires the three sub-services against the same repo instance so
    callers can do ``auth_service.passwords.set_user_password``
    without juggling extra constructor args.
    """

    def __init__(
        self,
        repo: UserAuthRepoABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._repo = repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)
        self._passwords = UserPasswordAuthServiceImpl(repo, logging_provider)
        self._passkeys = UserPasskeyAuthServiceImpl(repo, logging_provider)
        self._third_parties = UserThirdPartyAuthServiceImpl(repo, logging_provider)

    @property
    def passwords(self) -> UserPasswordAuthServiceABC:
        """Password sub-service."""
        return self._passwords

    @property
    def passkeys(self) -> UserPasskeyAuthServiceABC:
        """Passkey sub-service."""
        return self._passkeys

    @property
    def third_parties(self) -> UserThirdPartyAuthServiceABC:
        """Third-party link sub-service."""
        return self._third_parties

    async def get_user(
        self, filter: UserFilter
    ) -> Optional[UserAuthEntity]:
        """Look up a user by a :class:`UserFilter`."""
        self.log.debug(f"get_user filter={filter}")
        return await self._repo.select(filter)

    async def create_user(self, user: UserAuthEntity) -> UserAuthEntity:
        """Insert a new user row.

        No directory bootstrap here -- that's the legacy
        :class:`src.services.user_service.UserServiceImpl`'s job.
        This service only owns the auth row.
        """
        self.log.debug(f"create_user email={user.email} username={user.username}")
        return await self._repo.insert(user)

    async def update_user(
        self,
        user: UserAuthEntity,
        requester_id: str,
    ) -> UserAuthEntity:
        """Persist partial updates to an existing user."""
        if requester_id != user.id:
            self.log.warning(
                f"update_user rejected: requester_id={requester_id} != user.id={user.id}"
            )
            raise PermissionError("requester_id must equal user_id")
        self.log.debug(f"update_user user.id={user.id}")
        return await self._repo.update(user)


__all__ = [
    "UserAuthServiceImpl",
    "UserPasskeyAuthServiceImpl",
    "UserPasswordAuthServiceImpl",
    "UserThirdPartyAuthServiceImpl",
]
