from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.services.user_service import UserFilter
from src.db.entities.user.passkey import PasskeyEntity
from src.db.entities.user.password import PasswordEntity
from src.db.entities.user.third_party import ThirdPartyEntity, ThirdPartyFilter
from src.db.entities.user.user_auth import UserAuthEntity


class UserAuthRepoABC(ABC):
    """Abstract storage contract for the auth schema.

    Implementations:

    * :class:`src.db.repos.user.user_auth_postgres.PostgresUserAuthRepoImpl`
    """

    @abstractmethod
    async def insert(self, user: UserAuthEntity) -> UserAuthEntity:
        """Insert a new user; the repo assigns the id when UNDEFINED.

        Raises:
            Exception: when ``email`` collides with an existing row.
        """

    @abstractmethod
    async def select(
        self, filter: UserFilter
    ) -> Optional[UserAuthEntity]:
        """Fetch the first user matching every set field of `filter`.

        Set fields are AND-ed; an empty filter matches no user
        (``None`` returned).  A single-field filter (``user_id``,
        ``email``, ...) covers the lookup-by-id case too.
        """

    @abstractmethod
    async def update(self, user: UserAuthEntity) -> UserAuthEntity:
        """Persist partial updates to an existing user.

        Only fields whose value is not :obj:`UNDEFINED` are
        written; ``None`` explicitly clears the column.

        Raises:
            ValueError: when ``user.id`` is missing or no row
                exists with that id.
        """

    @abstractmethod
    async def upsert_password(
        self, password: PasswordEntity
    ) -> PasswordEntity:
        """Upsert the user's password row.

        A user has at most one password row; subsequent calls
        with the same ``user_id`` overwrite the hash.
        """

    @abstractmethod
    async def find_password(
        self, user_id: str
    ) -> Optional[PasswordEntity]:
        """Return the user's password row, or ``None`` if none is set."""

    @abstractmethod
    async def insert_passkey(
        self, passkey: PasskeyEntity
    ) -> PasskeyEntity:
        """Insert a new passkey and return the persisted entity."""

    @abstractmethod
    async def find_passkey(
        self, credential_id: bytes
    ) -> Optional[PasskeyEntity]:
        """Find a passkey by its WebAuthn credential id."""

    @abstractmethod
    async def find_passkey_by_id(
        self, passkey_id: str
    ) -> Optional[PasskeyEntity]:
        """Find a passkey by its server-assigned id."""

    @abstractmethod
    async def list_passkeys(
        self, user_id: str, include_revoked: bool = False
    ) -> List[PasskeyEntity]:
        """List a user's passkeys, ordered by id.

        Revoked rows are excluded by default.
        """

    @abstractmethod
    async def update_passkey_sign_count(
        self,
        passkey_id: str,
        new_sign_count: int,
    ) -> PasskeyEntity:
        """Bump the sign counter after a successful assertion.

        Raises:
            ValueError: when ``new_sign_count`` is not strictly
                greater than the stored counter (cloned
                authenticator).
            KeyError: when no passkey with that id exists.
        """

    @abstractmethod
    async def revoke_passkey(self, passkey_id: str) -> PasskeyEntity:
        """Stamp ``revoked_at`` on the passkey.

        Idempotent -- a second call leaves the timestamp at the
        original revoke time.
        """

    @abstractmethod
    async def insert_third_party(
        self, third: ThirdPartyEntity
    ) -> ThirdPartyEntity:
        """Insert a third-party link.

        Raises when another user already owns the
        ``(provider, provider_user_id)`` pair.
        """

    @abstractmethod
    async def find_third_party(
        self, filter: ThirdPartyFilter
    ) -> List[ThirdPartyEntity]:
        """Return every third-party link matching `filter`.

        Set fields are AND-ed; an empty filter returns ``[]`` --
        an unfiltered scan would touch every link row.
        """

    @abstractmethod
    async def delete_third_party(self, third_party_id: str) -> bool:
        """Delete a third-party link by its id.

        Returns ``True`` when a row was removed, ``False`` when the
        id was unknown.
        """


__all__ = ["UserAuthRepoABC"]
