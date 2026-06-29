from dataclasses import replace
from datetime import datetime
from typing import List, Literal, cast
from uuid import uuid7

from src.api import PermissionRepoABC, UserActionRepoABC
from src.api.relationship import NoteRelationEnum, ObjectRef, Relationship, SubjectRef
from src.api.sharing import SharingRepoABC, SharingServiceABC
from src.api.types import LoggingProvider
from src.api.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr, unwrap_undefined, unwrap_undefined_or
from src.api.user_context import UserContextABC
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.db.entities.user.user import UserEntity
from src.db.entities.user.user_action import (
    FilterUserAction,
    UserActionEntity,
    UserActionKind,
)
from src.db.repos.note.note import UserContext
from src.db.repos.user.user import UserRepoABC
from src.domain.permission_chain import HasNoteEditPermissionsPerm
from src.services.permissions import PermissionServiceABC


class DefaultSharingService(SharingServiceABC):
    """Service layer for note shares with note permission checks."""

    def __init__(
        self,
        sharing_repo: SharingRepoABC,
        user_repo: UserRepoABC,
        permission_repo: PermissionRepoABC,
        permission_service: PermissionServiceABC,
        logging_provider: LoggingProvider,
        user_action_repo: UserActionRepoABC,
    ) -> None:
        self._user_repo = user_repo
        self._sharing_repo = sharing_repo
        self._permission_repo = permission_repo
        self._permission_service = permission_service
        # The action repo is required: every share CRUD path touches it so
        # the schedule is consistent with the share's expiry.  Tests that
        # don't care about scheduling still inject a fake.
        self._user_action_repo = user_action_repo
        self.log = logging_provider(__name__, self)

    async def create_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        if share.note_id in (UNDEFINED, None):
            raise ValueError("share.note_id is required")

        await self._assert_can_edit_permissions(str(share.note_id), ctx)

        # create a temporary user that will hold the share's permission
        access_user = UserEntity(
            id=uuid7().hex,
            username=f"share-{share.created_by}--{share.note_id}",
            type="temporary",
        )
        access_user = await self._user_repo.insert(access_user)

        permission = unwrap_undefined(share.permission)
        if permission == "read":
            relation = NoteRelationEnum.READER
        elif permission == "write":
            relation = NoteRelationEnum.WRITER
        else:
            raise ValueError(f"Invalid permission for a share: {permission}")
        await self._permission_repo.insert([Relationship(
            resource=ObjectRef("note", unwrap_undefined(share.note_id)),
            relation=relation,
            subject=SubjectRef("user", access_user.id),
        )])

        # the repo needs the access user on the entity before persisting
        share.access_as = access_user.id

        # apply sane defaults; repos should not invent values
        normalized = replace(
            share,
            id=UNDEFINED,
            created_at=unwrap_undefined_or(share.created_at, datetime.now()),
            created_by=ctx.user_id,
        )
        created = await self._sharing_repo.create_share(normalized, ctx)

        # schedule the "disable" action if the share has an explicit expiry.
        await self._reconcile_share_actions(
            access_as=access_user.id,
            online_until=normalized.online_until,
        )

        return created

    async def update_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        if share.id in (UNDEFINED, None):
            raise ValueError("share.id is required")

        current = await self._sharing_repo.get_share_by_id(str(share.id), ctx)
        if current.note_id in (UNDEFINED, None):
            raise ValueError(f"Share has no note_id: {share.id}")

        await self._assert_can_edit_permissions(str(current.note_id), ctx)

        # swap the access user's reader/writer relation when the permission changes
        if share.permission is not UNDEFINED and share.permission is not None:
            await self._replace_share_permission(current, share.permission, ctx)

        updated = await self._sharing_repo.update_share(share, ctx)

        # re-evaluate scheduling only when the caller touched online_until.
        # ``current.access_as`` is the canonical id (the share row owns it),
        # since the caller's input share may not carry it.
        if share.online_until is not UNDEFINED:
            await self._reconcile_share_actions(
                access_as=unwrap_undefined(current.access_as),
                online_until=updated.online_until,
            )

        return updated

    async def get_shares_by_id(self, share_ids: List[str], ctx: UserContextABC) -> List[NoteShareEntity]:
        shares = await self._sharing_repo.get_shares_by_id(share_ids, ctx)
        return await self._filter_editable_shares(shares, ctx)

    async def get_shares(self, filter: FilterShareNote, ctx: UserContextABC) -> List[NoteShareEntity]:
        shares = await self._sharing_repo.get_shares(filter, ctx)
        shares = await self._filter_editable_shares(shares, ctx)
        # permissions live in SpiceDB, not on the share row, so resolve them per-share
        for share in shares:
            await self._resolve_share_permission(share)

        return shares

    async def delete_shares(self, share_ids: List[str], ctx: UserContextABC) -> None:
        if not share_ids:
            raise ValueError("At least one share_id is required")

        # test permissions for all shares before deleting any
        shares = await self._sharing_repo.get_shares_by_id(share_ids, ctx)
        note_ids = self._collect_note_ids(shares)
        for note_id in note_ids:
            await self._assert_can_edit_permissions(note_id, ctx)

        # delete all shares separately to prevent partial deletes caused by failures 
        # as good as possible. 
        for share in shares:
            if share.note_id in (UNDEFINED, None):
                raise ValueError(f"Share has no note_id: {share.id}")
            if share.access_as in (UNDEFINED, None):
                raise ValueError(f"Share has no access_as: {share.id}")

            access_as = str(share.access_as)

            # UNDEFINED on the relation matches every relation the access user holds here.
            await self._permission_repo.delete(
                Relationship(
                    resource=ObjectRef("note", str(share.note_id)),
                    relation=UNDEFINED,
                    subject=SubjectRef("user", access_as),
                )
            )

            # delete the share row
            await self._sharing_repo.delete_shares([str(share.id)], ctx)

            # tear down any user_action rows targeting the temporary access user
            # before removing the user itself.
            await self._purge_actions_for_user(access_as)

            # delete the temporary access user
            await self._user_repo.delete(access_as)



    def _collect_note_ids(self, shares: List[NoteShareEntity]) -> set[str]:
        """Return note IDs for permission checks and reject broken share rows."""
        note_ids = set()
        for share in shares:
            if share.note_id in (UNDEFINED, None):
                raise ValueError(f"Share has no note_id: {share.id}")
            note_ids.add(str(share.note_id))
        return note_ids

    async def _assert_can_edit_permissions(self, note_id: str, ctx: UserContextABC) -> None:
        """Raise when the actor cannot manage sharing for the note."""
        if await self._can_edit_permissions(note_id, ctx):
            return

        raise PermissionError(f"user has no permission to edit permissions for note {note_id}")

    async def _can_edit_permissions(self, note_id: str, ctx: UserContextABC) -> bool:
        """Return whether the actor can manage sharing for the note."""
        check = HasNoteEditPermissionsPerm(note_id).set_permission_repo(self._permission_repo)
        result = await check.check(ctx)
        return bool(result)

    async def _filter_editable_shares(
        self,
        shares: List[NoteShareEntity],
        ctx: UserContextABC,
    ) -> List[NoteShareEntity]:
        """Keep only shares for notes where the actor can edit permissions."""
        editable_note_ids = set()
        denied_note_ids = set()
        filtered = []

        for share in shares:
            if share.note_id in (UNDEFINED, None):
                continue

            note_id = str(share.note_id)
            if note_id in denied_note_ids:
                continue
            if note_id not in editable_note_ids:
                if await self._can_edit_permissions(note_id, ctx):
                    editable_note_ids.add(note_id)
                else:
                    denied_note_ids.add(note_id)
                    continue

            filtered.append(share)

        return filtered

    async def _resolve_share_permission(self, share: NoteShareEntity) -> NoteShareEntity:
        """Populate ``share.permission`` from the access user's SpiceDB relation."""
        note_id = share.note_id
        access_as = share.access_as
        if note_id in (UNDEFINED, None) or access_as in (UNDEFINED, None):
            return share

        share.permission = await self._get_share_permission(str(note_id), str(access_as))
        return share

    async def _get_share_permission(
        self,
        note_id: str,
        access_as: str,
    ) -> UndefinedOr[Literal["read", "write"]]:
        """Map the access user's effective permission to ``"read"``/``"write"``.

        ``writer`` implies both read and write via the schema, so check it first.
        """
        permissions = await self._permission_repo.get_permissions(
            user=UserContext(user_id=access_as),
            resource=ObjectRef("note", note_id),
        )
        self.log.debug(
            f"Resolving share permission (note {note_id}, access_as {access_as}): permissions={permissions}"
        )
        if "writer" in permissions:
            return "write"
        if "reader" in permissions:
            return "read"
        return UNDEFINED

    async def _replace_share_permission(
        self,
        current: NoteShareEntity,
        new_permission: Literal["read", "write"],
        ctx: UserContextABC,
    ) -> None:
        """Swap the access user's reader/writer relation on the shared note.

        Delegates to :meth:`PermissionServiceABC.replace_relationships` so all
        other relations on the note are preserved.
        """
        if current.note_id in (UNDEFINED, None):
            raise ValueError(f"Share has no note_id: {current.id}")
        access_as = current.access_as
        if access_as in (UNDEFINED, None):
            raise ValueError(f"Share has no access_as: {current.id}")

        note_id = str(current.note_id)
        access_as_id = str(access_as)
        new_relation = (
            NoteRelationEnum.READER
            if new_permission == "read"
            else NoteRelationEnum.WRITER
        )

        resource = ObjectRef("note", note_id)
        subject = SubjectRef("user", access_as_id)
        new_relationship = Relationship(resource=resource, relation=new_relation, subject=subject)

        # keep every existing relation except the access user's reader/writer entry
        keep: list[Relationship] = []
        for rel in await self._permission_repo.list_relationships(resource):
            is_access_user_share = (
                str(rel.subject.object_type) == "user"
                and str(rel.subject.object_id) == access_as_id
                and str(rel.relation) in {str(NoteRelationEnum.READER), str(NoteRelationEnum.WRITER)}
            )
            if not is_access_user_share:
                keep.append(rel)
        keep.append(new_relationship)

        await self._permission_service.replace_relationships(
            resource=resource,
            relationships=keep,
            actor=ctx,
        )

    # ------------------------------------------------------------------
    # user_action reconciliation
    # ------------------------------------------------------------------

    async def _reconcile_share_actions(
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
            return

        await self._drop_pending_disable(access_as)

        if online_until is None:
            return

        await self._user_action_repo.add_action(
            UserActionEntity(
                user_id=access_as,
                action="disable",
                execute_at=online_until,
            )
        )

    async def _drop_pending_disable(self, access_as: str) -> None:
        """Remove every pending ``disable`` action for ``access_as``."""
        pending = await self._user_action_repo.get_actions(
            FilterUserAction(
                user_id=access_as,
                action=cast(UserActionKind, "disable"),
                executed_at=None,
            )
        )
        for action in pending:
            if action.id in (UNDEFINED, None):
                continue
            await self._user_action_repo.remove_action(str(action.id))

    async def _purge_actions_for_user(self, access_as: str) -> None:
        """Remove every (pending or executed) action targeting ``access_as``.

        Called when a share is deleted so the scheduler never fires
        against an access user that no longer exists.
        """
        actions = await self._user_action_repo.get_actions_by_user(access_as)
        for action in actions:
            if action.id in (UNDEFINED, None):
                continue
            await self._user_action_repo.remove_action(str(action.id))
