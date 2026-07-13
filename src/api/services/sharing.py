"""Contracts for note sharing: persistence, public-link access, and the service that ties them together.

Three ABCs live here:

* :class:`SharingRepoABC` - thin DB wrapper, no permission checks.
* :class:`ShareAccessServiceABC` - public-link access path, used by
  unauthenticated callers.
* :class:`SharingServiceABC` - authenticated CRUD path; every method
  enforces ``edit_permissions`` on the underlying note.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Tuple

from src.api.other.undefined import UndefinedNoneOr
from src.api.other.user_context import UserContextABC
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity


class SharingRepoABC(ABC):
    """Thin DB wrapper for ``share`` rows.  No permission checks.

    The service layer is responsible for permission enforcement; this
    repo only translates requests into storage operations and surfaces
    the persisted entity back to the caller.

    Implementations:
    * :class:`src.db.repos.sharing.sharing.SharingPostgresRepo`
    """

    @abstractmethod
    async def create_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        """Insert a new share row.

        Args:
            share: share entity to insert.  Any
                :obj:`~src.api.undefined.UNDEFINED` field is set to a
                sensible default when one exists; otherwise the call
                fails.
            ctx: caller context, currently used for audit fields.

        Raises:
            ValueError: if ``share`` is missing required fields or has
                invalid values.

        Returns:
            NoteShareEntity: the persisted share, with ``id`` and any
            other defaulted fields populated.
        """
        ...

    @abstractmethod
    async def update_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        """Update the share row identified by ``share.id``.

        :obj:`~src.api.undefined.UNDEFINED` fields are left untouched.
        Use :obj:`None` to explicitly clear a column.

        Args:
            share: share entity to update.  Updatable fields:

                * ``description``
                * ``online_since``
                * ``online_until``
            ctx: caller context, currently used for audit fields.

        Raises:
            ValueError: if ``share`` is missing required fields or has
                invalid values.

        Returns:
            NoteShareEntity: the updated share.
        """
        ...

    async def get_share_by_id(self, share_id: str, ctx: UserContextABC) -> NoteShareEntity:
        """Fetch a single share by id.

        Args:
            share_id: id of the share to fetch.
            ctx: caller context.

        Raises:
            ValueError: if ``share_id`` is invalid or the share does
                not exist.

        Returns:
            NoteShareEntity: the requested share.
        """
        shares = await self.get_shares_by_id([share_id], ctx)
        if not shares:
            raise ValueError(f"Share not found: {share_id}")
        return shares[0]

    @abstractmethod
    async def get_shares_by_id(self, share_ids: List[str], ctx: UserContextABC) -> List[NoteShareEntity]:
        """Fetch several shares by id in a single call.

        Args:
            share_ids: ids to fetch.
            ctx: caller context.

        Raises:
            ValueError: if any id is invalid or does not exist.

        Returns:
            List[NoteShareEntity]: the matching shares, one per id.
        """
        ...

    async def get_share(self, filter: FilterShareNote, ctx: UserContextABC) -> NoteShareEntity:
        """Return the first share matching ``filter``.

        Args:
            filter: filter describing the share to fetch.
            ctx: caller context.

        Raises:
            ValueError: if no share matches ``filter``.

        Returns:
            NoteShareEntity: the first matching share.
        """
        shares = await self.get_shares(filter, ctx)
        if not shares:
            raise ValueError("Share not found")
        return shares[0]

    @abstractmethod
    async def get_shares(self, filter: FilterShareNote, ctx: UserContextABC) -> List[NoteShareEntity]:
        """Return every share matching ``filter``.

        Args:
            filter: filter describing the shares to fetch.
            ctx: caller context.

        Returns:
            List[NoteShareEntity]: matching shares.
        """
        ...

    async def delete_share(self, share_id: str, ctx: UserContextABC) -> None:
        """Delete the share with the given id.

        Args:
            share_id: id of the share to delete.
            ctx: caller context.

        Raises:
            ValueError: if ``share_id`` is invalid or the share does
                not exist.
        """
        return await self.delete_shares([share_id], ctx)

    @abstractmethod
    async def delete_shares(self, share_ids: List[str], ctx: UserContextABC) -> None:
        """Delete every share whose id is in ``share_ids``.

        Args:
            share_ids: ids of the shares to delete.
            ctx: caller context.

        Raises:
            ValueError: if any id is invalid.
        """
        ...


class ShareAccessServiceABC(ABC):
    """Public-link access path used by unauthenticated callers.

    Implementations:
    * :class:`src.services.share_access.ShareAccessServiceImpl`
    """

    @abstractmethod
    async def access_share(self, share_id: str, ctx: Optional[UserContextABC]) -> NoteShareEntity:
        """Resolve the share backing ``share_id`` and the note it points at.

        The implementation checks that the share exists, that the
        authenticated user (if any) is allowed to use it, and returns
        the share entity together with the note it grants access to.

        Args:
            share_id: id of the share, typically the value from the
                public share URL.
            ctx: optional caller context.  ``None`` for fully
                unauthenticated requests.

        Raises:
            ValueError: if ``share_id`` is invalid or the share does
                not exist.
            PermissionError: if the caller is not allowed to use the
                share.

        Returns:
            NoteShareEntity: the resolved share.
        """
        ...

    @abstractmethod
    async def get_share_user(
        self,
        share_id: str,
    ) -> Tuple[str, UndefinedNoneOr[datetime]]:
        """Return the temporary user id that backs a share.

        Looks up the share by id, extracts its ``access_as`` user and
        the share's ``online_until`` value.  The access user is fetched
        from the user store to ensure it still exists.

        Args:
            share_id: share id, typically provided via public URL.

        Returns:
            tuple[str, UndefinedNoneOr[datetime]]: ``(access_as, online_until)``
            where ``access_as`` is the temporary user id and
            ``online_until`` mirrors the ``shared.online_until`` column
            (:obj:`~src.api.undefined.UNDEFINED` if not set on the
            share, :obj:`None` if explicitly ``NULL`` meaning "never
            expires").

        Raises:
            ValueError: if ``share_id`` is empty, the share does not
                exist, or the linked access user has been removed.
        """
        ...


class SharingServiceABC(ABC):
    """Authenticated CRUD path for note shares.

    Every method enforces the ``edit_permissions`` permission on the
    underlying note on behalf of ``ctx``.

    Implementations:
    * :class:`src.services.sharing.SharingServiceImpl`
    """

    @abstractmethod
    async def create_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        """Create a share entity for a note.

        Any :obj:`~src.api.undefined.UNDEFINED` field is set to a
        sensible default when one exists; otherwise the call fails.

        Permissions:
            Requires ``edit_permissions`` on the note being shared.

        Args:
            share: share entity to create.
            ctx: caller context.

        Raises:
            ValueError: if ``share`` is missing required fields or has
                invalid values.
            PermissionError: if ``ctx`` lacks ``edit_permissions`` on
                the underlying note.

        Returns:
            NoteShareEntity: the created share, with ``id`` and any
            other defaulted fields populated.
        """
        ...

    @abstractmethod
    async def update_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        """Update an existing share.

        :obj:`~src.api.undefined.UNDEFINED` fields are left untouched.
        Use :obj:`None` to explicitly clear a column.

        Permissions:
            Requires ``edit_permissions`` on the note being shared.

        Args:
            share: share entity to update.  Updatable fields:

                * ``description``
                * ``online_since``
                * ``online_until``
                * ``permission``: when provided, the underlying SpiceDB
                  relationship for the share's ``access_as`` user is
                  replaced so the effective access matches the new
                  value (``"read"`` or ``"write"``).
            ctx: caller context.

        Raises:
            ValueError: if ``share`` is missing required fields or has
                invalid values.
            PermissionError: if ``ctx`` lacks ``edit_permissions`` on
                the underlying note.

        Returns:
            NoteShareEntity: the updated share.
        """
        ...

    async def get_share_by_id(self, share_id: str, ctx: UserContextABC) -> NoteShareEntity:
        """Fetch a single share by id.

        Permissions:
            Requires ``edit_permissions`` on the shared note.

        Args:
            share_id: id of the share to fetch.
            ctx: caller context.

        Raises:
            ValueError: if ``share_id`` is invalid or the share does
                not exist.
            PermissionError: if ``ctx`` lacks ``edit_permissions`` on
                the underlying note.
        """
        shares = await self.get_shares_by_id([share_id], ctx)
        if not shares:
            raise ValueError(f"Share not found: {share_id}")
        return shares[0]

    @abstractmethod
    async def get_shares_by_id(self, share_ids: List[str], ctx: UserContextABC) -> List[NoteShareEntity]:
        """Fetch several shares by id in a single call.

        Permissions:
            Shares for notes where ``ctx`` lacks ``edit_permissions``
            are filtered out before returning.

        Args:
            share_ids: ids to fetch.
            ctx: caller context.

        Raises:
            ValueError: if any id is invalid or does not exist.

        Returns:
            List[NoteShareEntity]: the editable shares for ``share_ids``.
        """
        ...

    async def get_share(self, filter: FilterShareNote, ctx: UserContextABC) -> NoteShareEntity:
        """Return the first share matching ``filter``.

        Permissions:
            Shares for notes where ``ctx`` lacks ``edit_permissions``
            are filtered out before the first match is taken.

        Args:
            filter: filter describing the share to fetch.
            ctx: caller context.

        Raises:
            ValueError: if no editable share matches ``filter``.

        Returns:
            NoteShareEntity: the first editable share matching ``filter``.
        """
        shares = await self.get_shares(filter, ctx)
        if not shares:
            raise ValueError("Share not found")
        return shares[0]

    @abstractmethod
    async def get_shares(self, filter: FilterShareNote, ctx: UserContextABC) -> List[NoteShareEntity]:
        """Return every editable share matching ``filter``.

        Permissions:
            Shares for notes where ``ctx`` lacks ``edit_permissions``
            are filtered out.

        Args:
            filter: filter describing the shares to fetch.
            ctx: caller context.

        Returns:
            List[NoteShareEntity]: matching editable shares.
        """
        ...

    async def delete_share(self, share_id: str, ctx: UserContextABC) -> None:
        """Delete the share with the given id.

        Permissions:
            Requires ``edit_permissions`` on the underlying note.

        Args:
            share_id: id of the share to delete.
            ctx: caller context.

        Raises:
            ValueError: if ``share_id`` is invalid or the share does
                not exist.
            PermissionError: if ``ctx`` lacks ``edit_permissions`` on
                the underlying note.
        """
        return await self.delete_shares([share_id], ctx)

    @abstractmethod
    async def delete_shares(self, share_ids: List[str], ctx: UserContextABC) -> None:
        """Delete every share whose id is in ``share_ids``.

        Permissions:
            Each underlying note must have ``edit_permissions`` for
            ``ctx``; otherwise the call raises :exc:`PermissionError`.

        Args:
            share_ids: ids of the shares to delete.
            ctx: caller context.

        Raises:
            ValueError: if any id is invalid.
            PermissionError: if ``ctx`` lacks ``edit_permissions`` on
                any of the underlying notes.
        """
        ...