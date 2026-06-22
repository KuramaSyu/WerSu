from dataclasses import replace
from datetime import datetime
from typing import List
from uuid import uuid7

from src.api import PermissionRepoABC
from src.api.relationship import NoteRelationEnum, ObjectRef, Relationship, SubjectRef
from src.api.sharing import SharingRepoABC, SharingServiceABC
from src.api.undefined import UNDEFINED, unwrap_undefined, unwrap_undefined_or
from src.api.user_context import UserContextABC
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.db.entities.user.user import UserEntity
from src.db.repos.user.user import UserRepoABC
from src.domain.permission_chain import HasNoteEditPermissionsPerm


class DefaultSharingService(SharingServiceABC):
    """Service layer for note shares with note permission checks."""

    def __init__(
        self,
        sharing_repo: SharingRepoABC,
        user_repo: UserRepoABC,
        permission_repo: PermissionRepoABC,

    ) -> None:
        self._user_repo = user_repo
        self._sharing_repo = sharing_repo
        self._permission_repo = permission_repo

    async def create_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        if share.note_id in (UNDEFINED, None):
            raise ValueError("share.note_id is required")

        await self._assert_can_edit_permissions(str(share.note_id), ctx)

        # create a temporary user for that share which will get the permissions to read or write
        user = UserEntity(
            id=uuid7().hex,
            username=f"share-{share.acc}-{share.note_id}",
            type="temporary",
        )
        user = await self._user_repo.insert(user)

        # create read or write permission for the temporary user
        relation = {}
        permission = unwrap_undefined(share.permission)
        if permission == "read":
            relation["relation"] = NoteRelationEnum.READER
        elif permission == "write":
            relation["relation"] = NoteRelationEnum.WRITER
        _relationships = await self._permission_repo.insert([Relationship(
            resource=ObjectRef("note", unwrap_undefined(share.note_id)),
            subject=SubjectRef("user", user.id),
            **relation
        )])

        # set sane defaults - REPOs should normally not create any defaults. 
        normalized = replace(
            share,
            id=UNDEFINED,
            created_at=unwrap_undefined_or(share.created_at, datetime.now()),
            created_by=ctx.user_id,
        )
        return await self._sharing_repo.create_share(normalized, ctx)

    async def update_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        if share.id in (UNDEFINED, None):
            raise ValueError("share.id is required")

        current = await self._sharing_repo.get_share_by_id(str(share.id), ctx)
        if current.note_id in (UNDEFINED, None):
            raise ValueError(f"Share has no note_id: {share.id}")

        await self._assert_can_edit_permissions(str(current.note_id), ctx)
        return await self._sharing_repo.update_share(share, ctx)

    async def get_shares_by_id(self, share_ids: List[str], ctx: UserContextABC) -> List[NoteShareEntity]:
        shares = await self._sharing_repo.get_shares_by_id(share_ids, ctx)
        return await self._filter_editable_shares(shares, ctx)

    async def get_shares(self, filter: FilterShareNote, ctx: UserContextABC) -> List[NoteShareEntity]:
        shares = await self._sharing_repo.get_shares(filter, ctx)
        return await self._filter_editable_shares(shares, ctx)

    async def delete_shares(self, share_ids: List[str], ctx: UserContextABC) -> None:
        if not share_ids:
            raise ValueError("At least one share_id is required")

        shares = await self._sharing_repo.get_shares_by_id(share_ids, ctx)
        note_ids = self._collect_note_ids(shares)
        for note_id in note_ids:
            await self._assert_can_edit_permissions(note_id, ctx)

        await self._sharing_repo.delete_shares(share_ids, ctx)

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
