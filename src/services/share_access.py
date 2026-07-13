from datetime import datetime
from typing import Optional, Tuple

from src.api.repos.permission_repo import PermissionRepoABC
from src.api.other.relationship import ObjectRef
from src.api.services.sharing import ShareAccessServiceABC, SharingRepoABC
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, unwrap_undefined
from src.api.repos.user_action_repo import UserActionRepoABC
from src.api.other.user_context import ContextFactory, UserContextABC
from src.db.entities.note.sharing import NoteShareEntity
from src.db.repos.user import UnimplementedUserContext
from src.db.repos.user.user import UserRepoABC


class ShareAccessService(ShareAccessServiceABC):
    def __init__(
        self,
        sharing_repo: SharingRepoABC,
        permission_repo: PermissionRepoABC,
        user_repo: UserRepoABC,
        user_action_repo: UserActionRepoABC,
        logger: LoggingProvider,
        context_factory: ContextFactory[UserContextABC],
    ) -> None:
        self._sharing_repo = sharing_repo
        self._permission_repo = permission_repo
        self._user_repo = user_repo
        self._user_action_repo = user_action_repo
        self._context_factory = context_factory
        self._log = logger(__name__, self)

    async def access_share(self, share_id: str, ctx: Optional[UserContextABC]) -> NoteShareEntity:
        # get share from DB
        share = await self._sharing_repo.get_shares_by_id([share_id], ctx)
        if not share:
            raise ValueError(f"Share not found: {share_id}")
        share = share[0]

        # check if the share user has acces to the given note
        # and at the same time check what permissions the share user has
        note_id = unwrap_undefined(share.note_id)
        access_as = unwrap_undefined(share.access_as)
        if await self._is_user_disabled(access_as):
            raise PermissionError(
                f"Share user {access_as!r} is disabled"
            )
        permissions = await self._permission_repo.get_permissions(
            user=await self._context_factory.create(access_as),
            resource=ObjectRef("note", note_id),
        )
        self._log.debug(f"Share access check for share {share_id} on note {note_id} with permissions {permissions}")

        if "reader" in permissions:
            share.permission = "read"
        elif "writer" in permissions:
            share.permission = "write"
        else:
            raise PermissionError("Share user does not have access to the note")
        return share

    async def get_share_user(
        self,
        share_id: str,
    ) -> Tuple[str, UndefinedNoneOr[datetime]]:
        """Return ``(access_as, online_until)`` for the given share id.

        The access user is fetched from the user table after the share
        lookup to guarantee the share points at a real, existing user;
        a dangling reference raises ``ValueError``.

        Raises ``PermissionError`` if the access user is disabled, since
        handing out a disabled user id would let the caller trigger
        downstream auth paths that have already been torn down.
        """
        if not share_id:
            raise ValueError("share_id is required")

        shares = await self._sharing_repo.get_shares_by_id(
            [share_id],
            ctx=UnimplementedUserContext(),
        )
        if not shares:
            raise ValueError(f"Share not found: {share_id}")
        share = shares[0]

        access_as = unwrap_undefined(share.access_as)
        access_user = await self._user_repo.select(access_as)
        if access_user is None:
            raise ValueError(
                f"Share {share_id} references access user {access_as!r}, "
                f"but that user no longer exists"
            )

        if await self._is_user_disabled(access_as):
            raise PermissionError(
                f"Share user {access_as!r} is disabled"
            )

        return (access_as, share.online_until)

    async def _is_user_disabled(self, user_id: str) -> bool:
        """Return True if the most recent executed action for ``user_id`` is ``disable``.

        A user is disabled when the scheduler has executed a ``disable``
        action since the last ``enable`` (or there is no ``enable`` at
        all).  ``delete`` actions do not flip the state because the
        user is gone by then; they're handled separately.
        """
        # Fetch every action for the user and pick out the executed ones.
        actions = await self._user_action_repo.get_actions_by_user(user_id)
        executed = [
            a for a in actions
            if a.executed_at is not None and a.executed_at is not UNDEFINED
        ]
        if not executed:
            return False
        executed.sort(key=lambda a: a.executed_at, reverse=True)  # type: ignore[arg-type,return-value]
        return str(executed[0].action) == "disable"


