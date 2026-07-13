from dataclasses import replace
from datetime import datetime
from typing import List, Literal

from src.api import PermissionRepoABC, ActivityLoggerServiceABC
from src.api.other.relationship import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    Relationship,
    SubjectRef,
)
from src.api.services.sharing import SharingServiceABC
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED, UndefinedOr, unwrap_undefined, unwrap_undefined_or
from src.api.other.user_context import UserContextABC
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.db.repos.user import RepoUserContext
from src.db.repos.user.user import UserRepoABC
from src.domain.permission_chain import HasNoteEditPermissionsPerm
from src.services.permissions import PermissionServiceABC
from src.facades.share_action_facade import ShareActionFacade


class SharingServiceImpl(SharingServiceABC):
    """Service layer for note shares with note permission checks."""

    def __init__(
        self,
        share_facade: ShareActionFacade,
        permission_repo: PermissionRepoABC,
        permission_service: PermissionServiceABC,
        logging_provider: LoggingProvider,
        user_repo: UserRepoABC,
        activity_logger: ActivityLoggerServiceABC,
    ) -> None:
        self._share_facade = share_facade
        self._permission_repo = permission_repo
        self._permission_service = permission_service
        self._user_repo = user_repo
        self._activity_logger = activity_logger
        self.log = logging_provider(__name__, self)

    async def create_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        if share.note_id in (UNDEFINED, None):
            raise ValueError("share.note_id is required")

        await self._assert_can_edit_permissions(str(share.note_id), ctx)

        # map the share's ``permission`` string to the SpiceDB relation enum
        if not share.permission:
            raise ValueError("share.permission is required")
        if share.permission == "read":
            relation = NoteRelationEnum.READER
        elif share.permission == "write":
            relation = NoteRelationEnum.WRITER
        else:
            raise ValueError(f"Invalid permission for a share: {share.permission}")

        # apply sane defaults; repos should not invent values
        normalized = replace(
            share,
            id=UNDEFINED,
            created_at=unwrap_undefined_or(share.created_at, datetime.now()),
            created_by=ctx.user_id,
        )
        created = await self._share_facade.create_share(normalized, ctx)
        access_as = unwrap_undefined(created.access_as)

        # the facade created the temp user and persisted the share row;
        # policy lives here, so the reader/writer relation is inserted last.
        await self._permission_repo.insert([Relationship(
            resource=ObjectRef("note", str(created.note_id)),
            relation=relation,
            subject=SubjectRef("user", access_as),
        )])

        await self._activity_logger.note_shared(
            str(created.note_id),
            ctx,
            metadata={"permission": str(relation), "access_as": access_as},
        )

        return created

    async def update_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        if share.id in (UNDEFINED, None):
            raise ValueError("share.id is required")

        current = await self._share_facade.get_share_by_id(str(share.id), ctx)
        if current.note_id in (UNDEFINED, None):
            raise ValueError(f"Share has no note_id: {share.id}")

        await self._assert_can_edit_permissions(str(current.note_id), ctx)

        # swap the access user's reader/writer relation when the permission changes
        if share.permission is not UNDEFINED and share.permission is not None:
            await self._replace_share_permission(current, share.permission, ctx)

        return await self._share_facade.update_share(share, ctx)

    async def get_shares_by_id(self, share_ids: List[str], ctx: UserContextABC) -> List[NoteShareEntity]:
        shares = await self._share_facade.get_shares_by_id(share_ids, ctx)
        return await self._filter_editable_shares(shares, ctx)

    async def get_shares(self, filter: FilterShareNote, ctx: UserContextABC) -> List[NoteShareEntity]:
        shares = await self._share_facade.get_shares(filter, ctx)
        shares = await self._filter_editable_shares(shares, ctx)
        # permissions live in SpiceDB, not on the share row, so resolve them per-share
        for share in shares:
            await self._resolve_share_permission(share)

        return shares

    async def delete_shares(self, share_ids: List[str], ctx: UserContextABC) -> None:
        if not share_ids:
            raise ValueError("At least one share_id is required")

        # test permissions for all shares before deleting any
        shares = await self._share_facade.get_shares_by_id(share_ids, ctx)
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
            if share.id in (UNDEFINED, None):
                raise ValueError(f"Share has no id: {share!r}")

            access_as = str(share.access_as)
            note_id = str(share.note_id)
            share_id = str(share.id)

            # UNDEFINED on the relation matches every relation the access user holds here.
            await self._permission_repo.delete(
                Relationship(
                    resource=ObjectRef("note", note_id),
                    relation=UNDEFINED,
                    subject=SubjectRef("user", access_as),
                )
            )

            await self._share_facade.delete_share(share_id, ctx)

            await self._activity_logger.note_unshared(
                note_id,
                ctx,
                metadata={"share_id": share_id, "access_as": access_as},
            )



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
            user=RepoUserContext(self._user_repo, access_as),
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
        """Swap the access user's reader/writer relation on the shared note"""
        note_id = current.note_id
        access_as = current.access_as

        if not note_id:
            raise ValueError(f"Share has no note_id: {current.id}")
        if not access_as:
            raise ValueError(f"Share has no access_as: {current.id}")

        new_relation = (
            NoteRelationEnum.READER
            if new_permission == "read"
            else NoteRelationEnum.WRITER
        )

        resource = ObjectRef("note", note_id)
        subject = SubjectRef("user", access_as)
        new_relationship = Relationship(resource=resource, relation=new_relation, subject=subject)

        # Drop the access user's existing reader/writer relation (if
        # any) and insert the new one.  Going through
        # `replace_relationships` would also touch every other relation
        # on the note (owner, parent_directory, ...) and is rejected
        # by the permission service whenever a structural (hierarchy)
        # tuple is part of the picture -- neither is what we want for
        # a share-permission swap.
        for old_relation in (NoteRelationEnum.READER, NoteRelationEnum.WRITER):
            await self._permission_repo.delete(
                Relationship(
                    resource=resource,
                    relation=old_relation,
                    subject=subject,
                )
            )
        await self._permission_repo.insert([new_relationship])