"""Composite facade that turns a share into a coordinated multi-repo operation.

Creating or tearing down a share is never a single row write in
isolation.  The full lifecycle needs at least:

* a temporary access user in the ``users`` table,
* the share row itself in ``shared``,
* one or more scheduled ``disable`` actions in ``user_action`` so
  the share can expire without manual intervention.

:class:`ShareActionFacade` inherits :class:`SharingRepoABC` and bundles
those writes behind the repo's CRUD surface, so the service layer only
talks to one collaborator for share persistence.

The class is intentionally concrete: every dependency it composes is
itself an ABC, so tests can swap the underlying repos in
``__init__`` instead of subclassing the facade.  This mirrors how
:class:`DefaultSharingService` already accepts its collaborators.

Collaborators composed by this facade:

* :class:`src.api.sharing.SharingRepoABC`
* :class:`src.db.repos.user.user.UserRepoABC`
* :class:`src.api.user_action.UserActionRepoABC`
* :class:`src.api.types.LoggingProvider`

The permission-repo writes (inserting the reader/writer relation on
create, deleting it on teardown) stay with the service layer since
they encode policy decisions, not persistence mechanics.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, cast
from uuid import uuid7

from src.api.sharing import SharingRepoABC
from src.api.types import LoggingProvider
from src.api.undefined import (
    UNDEFINED,
    UndefinedNoneOr,
    UndefinedOr,
    unwrap_undefined,
)
from src.api.user_action import UserActionRepoABC
from src.api.user_context import UserContextABC
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.db.entities.user.user import UserEntity
from src.db.entities.user.user_action import (
    FilterUserAction,
    UserActionEntity,
    UserActionKind,
)
from src.db.repos.user.user import UserRepoABC


class ShareActionFacade(SharingRepoABC):
    """Facade to reduce complexity, e.g. constructor overinjection, in
    :class:`DefaultSharingService`. 
    When applying a CRUD operation to a share, then also
    update temporary users and schedule their future actions 
    (e.g. disable, or never disable). 

    Following (abstract) repos are combined here:
    * :class:`src.api.sharing.SharingRepoABC`
    * :class:`src.db.repos.user.user.UserRepoABC`
    * :class:`src.api.user_action.UserActionRepoABC`

    and logs them.
    """

    def __init__(
        self,
        sharing_repo: SharingRepoABC,
        user_repo: UserRepoABC,
        user_action_repo: UserActionRepoABC,
        logging_provider: LoggingProvider,
    ) -> None:
        self._sharing_repo = sharing_repo
        self._user_repo = user_repo
        self._user_action_repo = user_action_repo
        self.log = logging_provider(__name__, self, prefix="sharing facade")

    async def create_share(
        self,
        share: NoteShareEntity,
        ctx: UserContextABC,
    ) -> NoteShareEntity:
        """Create the temp user, the share row, and any scheduled actions.

        The reader/writer relation for the temp user is *not* inserted
        here; that write belongs to the service layer because it
        encodes policy.  This method only handles the persistence-side
        concerns: temp-user creation, share row insert, and expiry
        scheduling.

        Args:
            share: :class:`NoteShareEntity`.  ``note_id`` must be set;
                ``access_as`` is filled in by the facade.
            ctx: :class:`UserContextABC`.  Forwarded to the wrapped
                repos; not interpreted by the facade itself.

        Raises:
            ValueError: if ``note_id`` is missing.

        Returns:
            :class:`NoteShareEntity`: the persisted share, including the
            generated ``access_as`` user id.
        """
        if share.note_id in (UNDEFINED, None):
            raise ValueError("share.note_id is required")

        self.log.debug(f"create_share for note={share.note_id} by user={ctx.user_id}")

        # create the temporary user that will hold the share's permission
        access_user = await self._create_access_user(share, ctx)

        # the row needs the access user before persisting
        share.access_as = access_user.id

        created = await self._sharing_repo.create_share(share, ctx)
        self.log.debug(f"create_share persisted share id={created.id} access_as={access_user.id}")

        # schedule the "disable" action if the share has an explicit expiry.
        await self._update_future_actions(
            access_as=access_user.id,
            online_until=share.online_until,
        )

        return created

    async def update_share(
        self,
        share: NoteShareEntity,
        ctx: UserContextABC,
    ) -> NoteShareEntity:
        """Pass-through to the wrapped repo, with action reconciliation on expiry changes.

        Args:
            share: :class:`NoteShareEntity` carrying the new field values
                plus the ``id`` of the row to update.
            ctx: :class:`UserContextABC`, forwarded to the wrapped repo.

        Returns:
            :class:`NoteShareEntity`: the updated share.
        """
        if share.id in (UNDEFINED, None):
            raise ValueError("share.id is required")

        self.log.debug(f"update_share for share id={share.id} by user={ctx.user_id}")

        current = await self._sharing_repo.get_share_by_id(str(share.id), ctx)
        updated = await self._sharing_repo.update_share(share, ctx)

        # re-evaluate scheduling only when the caller touched online_until.
        # ``current.access_as`` is the canonical id (the share row owns it),
        # since the caller's input share may not carry it.
        if share.online_until is not UNDEFINED:
            access_as = unwrap_undefined(current.access_as)
            self.log.debug(
                f"update_share id={share.id}: online_until changed, reconciling "
                f"scheduled actions for access_as={access_as}"
            )
            await self._update_future_actions(
                access_as=access_as,
                online_until=updated.online_until,
            )

        return updated

    async def delete_share(self, share_id: str, ctx: UserContextABC) -> None:
        """Tear down everything the facade owns for a share.

        Removes the share row, then the temp user's pending and
        executed actions, then the temp user itself.  The SpiceDB
        relation delete is the caller's responsibility (policy) and
        is *not* performed here.

        Args:
            share_id: id of the share to delete.
            ctx: caller context, forwarded to the wrapped repo.

        Raises:
            ValueError: if ``share_id`` is empty, the share row is not
                found, or the share is in a broken state (missing
                ``note_id`` / ``access_as``).
        """
        if not share_id:
            raise ValueError("share_id is required")

        self.log.debug(f"delete_share id={share_id} by user={ctx.user_id}")

        share = await self._sharing_repo.get_share_by_id(share_id, ctx)
        if share.note_id in (UNDEFINED, None):
            raise ValueError(f"Share has no note_id: {share.id}")
        if share.access_as in (UNDEFINED, None):
            raise ValueError(f"Share has no access_as: {share.id}")

        access_as = str(share.access_as)

        # delete the share row
        await self._sharing_repo.delete_shares([str(share.id)], ctx)
        self.log.debug(f"delete_share id={share_id}: share row removed")

        # remove all actions, pending or executed, for the temp user
        actions = await self._user_action_repo.get_actions_by_user(access_as)
        for action in actions:
            if not action.id:
                continue
            await self._user_action_repo.remove_action(str(action.id))
        if actions:
            self.log.debug(
                f"delete_share id={share_id}: purged {len(actions)} user_action "
                f"rows for access_as={access_as}"
            )

        # delete the temporary access user
        await self._user_repo.delete(access_as)
        self.log.debug(
            f"delete_share id={share_id}: removed temporary access user {access_as}"
        )

    async def delete_shares(self, share_ids: List[str], ctx: UserContextABC) -> None:
        """Delete every share in ``share_ids`` independently.

        Each share is torn down in its own
        :meth:`delete_share` call so a failure on one share does not
        block the others from being cleaned up.

        Args:
            share_ids: ids of the shares to delete.  Must be non-empty.
            ctx: caller context, forwarded to each :meth:`delete_share`.

        Raises:
            ValueError: if ``share_ids`` is empty, or any single share
                teardown fails (which leaves earlier shares already
                deleted).
        """
        if not share_ids:
            raise ValueError("At least one share_id is required")
        self.log.debug(
            f"delete_shares: tearing down {len(share_ids)} share(s) by user={ctx.user_id}"
        )
        for share_id in share_ids:
            await self.delete_share(share_id, ctx)

    async def get_share_by_id(self, share_id: str, ctx: UserContextABC) -> NoteShareEntity:
        """Return the :class:`NoteShareEntity` with the given id, or raise :exc:`ValueError`."""
        return await self._sharing_repo.get_share_by_id(share_id, ctx)

    async def get_shares_by_id(
        self,
        share_ids: List[str],
        ctx: UserContextABC,
    ) -> List[NoteShareEntity]:
        """Return every :class:`NoteShareEntity` whose id is in ``share_ids``."""
        return await self._sharing_repo.get_shares_by_id(share_ids, ctx)

    async def get_share(
        self,
        filter: FilterShareNote,
        ctx: UserContextABC,
    ) -> NoteShareEntity:
        """Return the first :class:`NoteShareEntity` matching ``filter``, or raise :exc:`ValueError`."""
        return await self._sharing_repo.get_share(filter, ctx)

    async def get_shares(
        self,
        filter: FilterShareNote,
        ctx: UserContextABC,
    ) -> List[NoteShareEntity]:
        """Return every :class:`NoteShareEntity` matching ``filter``."""
        return await self._sharing_repo.get_shares(filter, ctx)

    async def _create_access_user(
        self,
        share: NoteShareEntity,
        ctx: UserContextABC,
    ) -> UserEntity:
        """Insert the temporary user that will hold the share's permission.

        Args:
            share: :class:`NoteShareEntity` whose ``note_id`` is used to
                build the access user's username.
            ctx: caller context, also used in the generated username.

        Returns:
            :class:`UserEntity`: the inserted temporary user, with its
            ``id`` populated by the underlying user repo.
        """
        note_id = unwrap_undefined(share.note_id)
        access_user = UserEntity(
            id=uuid7().hex,
            username=f"share-{ctx.user_id}-{note_id}",
            type="temporary",
        )
        inserted = await self._user_repo.insert(access_user)
        self.log.debug(
            f"_create_access_user: inserted temporary user {inserted.id} for "
            f"share on note {note_id}"
        )
        return inserted

    async def _update_future_actions(
        self,
        *,
        access_as: str,
        online_until: UndefinedNoneOr[datetime],
    ) -> None:
        """Bring pending share actions in line with the share's expiry.

        Rules:

        * ``online_until is UNDEFINED`` -> the caller did not touch the
          field; do nothing.
        * ``online_until is None`` -> the share explicitly never expires;
          any pending ``disable`` action is dropped, but already-executed
          rows are left alone.
        * concrete ``datetime`` -> ensure exactly one pending ``disable``
          action exists at that timestamp; create it or reschedule it,
          dropping any other pending ``disable`` rows for the user.
        """
        if online_until is UNDEFINED:
            # online_until not touched -> don't touch pending actions either
            return

        self.log.debug(
            f"_update_future_actions: reconciling scheduled actions for "
            f"access_as={access_as} (online_until={online_until!r})"
        )

        # fetch every pending ``disable`` action and drop them
        pending = await self._user_action_repo.get_actions(
            FilterUserAction(
                user_id=access_as,
                action=cast(UserActionKind, "disable"),
                executed_at=None,
            )
        )
        for action in pending:
            if not action.id:
                continue
            await self._user_action_repo.remove_action(str(action.id))
        if pending:
            self.log.debug(
                f"_update_future_actions: dropped {len(pending)} pending disable "
                f"row(s) for access_as={access_as}"
            )

        if online_until is None:
            # no new due date? -> ok; return
            self.log.debug(
                f"_update_future_actions: share now never expires for "
                f"access_as={access_as}"
            )
            return

        # there is a new due date -> schedule the new disable action
        await self._user_action_repo.add_action(
            UserActionEntity(
                user_id=access_as,
                action="disable",
                execute_at=online_until,
            )
        )
        self.log.debug(
            f"_update_future_actions: scheduled disable at "
            f"{online_until.isoformat()} for access_as={access_as}"
        )


__all__ = ["ShareActionFacade"]