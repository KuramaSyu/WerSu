from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.services.user_service import UserFilter
from src.db.entities.user.passkey import PasskeyEntity
from src.db.entities.user.password import PasswordEntity
from src.db.entities.user.third_party import (
    ThirdPartyEntity,
    ThirdPartyFilter,
    ThirdPartyLinkSpec,
)
from src.db.entities.user.user_auth import UserAuthEntity


class UserPasswordAuthServiceABC(ABC):
    """Password-hash side of :class:`UserAuthServiceABC`.

    Implementations:

    * :class:`src.services.user_auth_service.UserPasswordAuthServiceImpl`
    """

    @abstractmethod
    async def set_user_password(
        self,
        user_id: str,
        requester_id: str,
        password_hash: str,
    ) -> PasswordEntity:
        """Insert or update the user's password row.

        Args:
            user_id: id of the user whose password is being set.
            requester_id: actor performing the write.  Must
                equal ``user_id``.
            password_hash: argon2id encoded hash; the caller is
                responsible for the hashing.

        Raises:
            PermissionError: when ``requester_id != user_id``.
        """

    @abstractmethod
    async def find_password(
        self, user_id: str
    ) -> Optional[PasswordEntity]:
        """Return the user's password row, or ``None``."""


class UserPasskeyAuthServiceABC(ABC):
    """Passkey side of :class:`UserAuthServiceABC`.

    Implementations:

    * :class:`src.services.user_auth_service.UserPasskeyAuthServiceImpl`
    """

    @abstractmethod
    async def register_passkey(
        self,
        user_id: str,
        requester_id: str,
        passkey: PasskeyEntity,
    ) -> PasskeyEntity:
        """Register a new WebAuthn passkey for the user.

        Args:
            user_id: id of the user the passkey belongs to.
            requester_id: actor performing the write.  Must
                equal ``user_id``.
            passkey: the new passkey.  ``id`` is assigned by the
                service; ``user_id`` is overwritten with the
                ``user_id`` arg.

        Returns:
            PasskeyEntity: the persisted row with its
            server-assigned id populated.

        Raises:
            PermissionError: when ``requester_id != user_id``.
        """

    @abstractmethod
    async def find_passkey(
        self, credential_id: bytes
    ) -> Optional[PasskeyEntity]:
        """Look up a passkey by its WebAuthn credential id."""

    @abstractmethod
    async def list_passkeys(
        self,
        user_id: str,
        include_revoked: bool = False,
    ) -> List[PasskeyEntity]:
        """List the user's passkeys.

        Args:
            user_id: id of the user.
            include_revoked: when ``True``, include revoked
                passkeys (with ``revoked_at`` set).  Default is
                ``False``.
        """

    @abstractmethod
    async def update_sign_count(
        self,
        passkey_id: str,
        new_sign_count: int,
        requester_id: str,
    ) -> PasskeyEntity:
        """Bump the sign counter after a successful assertion.

        Args:
            passkey_id: id of the passkey.
            new_sign_count: the new counter value.  Must be
                strictly greater than the stored one.
            requester_id: actor performing the write.  Must
                equal the passkey's ``user_id``.

        Raises:
            PermissionError: when ``requester_id != passkey.user_id``.
            ValueError: when ``new_sign_count`` is not strictly
                greater than the stored counter (cloned
                authenticator).
        """

    @abstractmethod
    async def revoke_passkey(
        self,
        passkey_id: str,
        requester_id: str,
    ) -> PasskeyEntity:
        """Revoke a passkey.

        Args:
            passkey_id: id of the passkey.
            requester_id: actor performing the write.  Must
                equal the passkey's ``user_id``.

        Raises:
            PermissionError: when ``requester_id != passkey.user_id``.
        """


class UserThirdPartyAuthServiceABC(ABC):
    """Third-party credentials side of :class:`UserAuthServiceABC`.

    Implements the OAuth-link surface: Discord and Google links
    live on :class:`ThirdPartyEntity` rows behind the
    ``auth.third_party`` table.

    Implementations:

    * :class:`src.services.user_auth_service.UserThirdPartyAuthServiceImpl`
    """

    @abstractmethod
    async def find_third_party(
        self, filter: ThirdPartyFilter
    ) -> List[ThirdPartyEntity]:
        """Return every third-party link matching `filter`.

        Set fields are AND-ed; an empty filter returns ``[]``.

        Callers that want the user behind a single provider/id
        pair build the filter themselves -- the service exposes
        only the generic filter lookup.
        """

    @abstractmethod
    async def link_third_party(
        self,
        user_id: str,
        requester_id: str,
        spec: ThirdPartyLinkSpec,
    ) -> ThirdPartyEntity:
        """Attach a third-party link to an existing user.

        ``spec`` is one of :class:`DiscordLink` / :class:`GoogleLink`;
        the provider is inferred from the payload's type.  The
        ``requester_id == user_id`` check runs first and raises
        :exc:`PermissionError` on mismatch before any write.

        Args:
            user_id: id of the user receiving the link.
            requester_id: actor performing the write.  Must
                equal ``user_id``.
            spec: typed link payload (``DiscordLink`` /
                ``GoogleLink``).

        Raises:
            PermissionError: when ``requester_id != user_id``.
        """

    @abstractmethod
    async def unlink_third_party(
        self,
        third_party_id: str,
        requester_id: str,
    ) -> bool:
        """Remove a third-party link by its id.

        Args:
            third_party_id: id of the link row.
            requester_id: actor performing the write.  Must
                equal the link's ``user_id``.

        Returns:
            bool: ``True`` when a row was removed.

        Raises:
            PermissionError: when ``requester_id != link.user_id``.
            KeyError: when no link with that id exists.
        """


class UserAuthServiceABC(ABC):
    """Auth-side application service.

    User CRUD with a :class:`UserFilter` lookup; exposes
    :attr:`passwords` and :attr:`passkeys` sub-services. 
    Permissions are checked and raise with PermissionError if not granted.

    Implementations:

    * :class:`src.services.user_auth_service.UserAuthServiceImpl`
    """

    @property
    @abstractmethod
    def passwords(self) -> UserPasswordAuthServiceABC:
        """Password sub-service."""

    @property
    @abstractmethod
    def passkeys(self) -> UserPasskeyAuthServiceABC:
        """Passkey sub-service."""

    @property
    @abstractmethod
    def third_parties(self) -> UserThirdPartyAuthServiceABC:
        """Third-party link sub-service."""

    @abstractmethod
    async def get_user(
        self, filter: UserFilter
    ) -> Optional[UserAuthEntity]:
        """Look up a user by a :class:`UserFilter`.

        Set fields are AND-ed; an empty filter returns ``None``.
        """

    @abstractmethod
    async def create_user(self, user: UserAuthEntity) -> UserAuthEntity:
        """Insert a new user row and return the persisted entity."""

    @abstractmethod
    async def update_user(
        self,
        user: UserAuthEntity,
        requester_id: str,
    ) -> UserAuthEntity:
        """Persist partial updates to an existing user.

        Args:
            user: the user carrying ``id`` and the fields to
                update.  ``id`` must be set; the rest may be
                :obj:`~src.api.undefined.UNDEFINED` to leave
                the column untouched.
            requester_id: actor performing the write.  Must
                equal ``user.id``.

        Raises:
            PermissionError: when ``requester_id != user.id``.
            ValueError: when ``user.id`` is missing or no row
                exists with that id.
        """


__all__ = [
    "UserAuthServiceABC",
    "UserPasskeyAuthServiceABC",
    "UserPasswordAuthServiceABC",
    "UserThirdPartyAuthServiceABC",
]
