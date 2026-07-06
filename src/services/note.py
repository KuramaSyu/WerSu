"""Concrete :class:`~src.api.note_service.NoteServiceABC` implementation.

This service composes :class:`src.db.repos.note.note.NoteFacade`
with the permission and directory repos and orchestrates every
permission-related concern (parent-directory resolution, owner /
parent-dir relation insert, post-fetch permission enrichment, search
result enrichment).

It is the only layer in the codebase that holds a
:class:`src.api.permission_repo.PermissionRepoABC`.  The gRPC adapter
(:class:`src.grpc_mod.service.GrpcNoteService`) calls into it; nothing
else reaches the permission repo.
"""

from __future__ import annotations

from typing import List, Optional

from src.api import (
    NoteRelationEnum,
    NoteResponse,
    NoteServiceABC,
    ObjectRef,
    ObjectTypeEnum,
    PermissionRepoABC,
    Relationship,
    SubjectRef,
    ActivityLoggerServiceABC,
)
from src.api.jwt_provider import JwtProvider
from src.api.relationship import AttachmentRelationEnum
from src.api.undefined import UNDEFINED, is_undefined, unwrap_undefined_or
from src.api.user_context import UserContextABC
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.note import NoteRepoFacadeABC, Pagination, SearchType
from src.domain.permission_chain import HasNoteDeletePerm, HasNoteWritePerm
from src.utils.extract_attachments import extract_attachment_ids


class NoteService(NoteServiceABC):
    """Concrete :class:`~src.api.note_service.NoteServiceABC` backed by `NoteFacade`.

    Owns every permission-check and relation-mutation that previously
    lived on the note facade; the facade is now a pure CRUD repo.
    """

    def __init__(
        self,
        note_repo: NoteRepoFacadeABC,
        permission_repo: PermissionRepoABC,
        jwt_provider: JwtProvider,
        directory_repo: DirectoryRepo,
        activity_logger: ActivityLoggerServiceABC,
    ) -> None:
        self._note_repo = note_repo
        self._permission_repo = permission_repo
        self._jwt_provider = jwt_provider
        self._directory_repo = directory_repo
        self._activity_logger = activity_logger

    async def get_note(
        self,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> NoteResponse:
        note = await self._note_repo.select_by_id(note_id, user_ctx)
        if note is None:
            return NoteResponse(note=None)

        await self._activity_logger.note_viewed(note_id, user_ctx)

        note.permissions = await self._fetch_note_permissions(note_id)

        id_token_map: dict[str, str] = {}
        if await user_ctx.is_temporary_user():
            id_token_map = await self._build_attachment_tokens(note, user_ctx)

        return NoteResponse(note=note, id_token_map=id_token_map)

    async def insert_note(
        self,
        note: NoteEntity,
        user_ctx: UserContextABC,
    ) -> NoteEntity:
        parent_directory_id = await self._resolve_parent_directory_id(note, user_ctx)
        # The repo owns the owner + parent_directory relation writes;
        # this layer only adds the eventual-consistency backfill below.
        inserted = await self._note_repo.insert(note, user_ctx)

        await self._activity_logger.note_created(str(inserted.note_id), user_ctx)

        # Local copy used only for the read-after-write backfill so the
        # parent_directory relation shows up even if SpiceDB hasn't
        # converged yet.  The actual relation was inserted by the repo.
        parent_dir_relation = Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, inserted.note_id),
            relation=NoteRelationEnum.PARENT_DIRECTORY,
            subject=SubjectRef(ObjectTypeEnum.DIRECTORY, parent_directory_id),
        )

        inserted.permissions = await self._fetch_note_permissions(str(inserted.note_id))
        # SpiceDB can be eventually consistent right after the insert;
        # make sure the parent_directory relation shows up even before
        # the read-after-write converges.
        has_parent_dir = any(
            str(rel.relation) == str(NoteRelationEnum.PARENT_DIRECTORY)
            and str(rel.subject.object_type) == str(ObjectTypeEnum.DIRECTORY)
            and str(rel.subject.object_id) == str(parent_directory_id)
            for rel in inserted.permissions
        )
        if not has_parent_dir:
            inserted.permissions.append(parent_dir_relation)
        return inserted

    async def update_note(
        self,
        note: NoteEntity,
        user_ctx: UserContextABC,
    ) -> NoteEntity:
        write_check = HasNoteWritePerm(str(note.note_id)).set_permission_repo(
            self._permission_repo
        )
        write_result = await write_check.check(user_ctx)
        if not write_result:
            raise write_result.error
        return await self._note_repo.update(note, user_ctx)

    async def delete_note(
        self,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> Optional[NoteEntity]:
        delete_check = HasNoteDeletePerm(note_id).set_permission_repo(
            self._permission_repo
        )
        delete_result = await delete_check.check(user_ctx)
        if not delete_result:
            raise delete_result.error
        deleted = await self._note_repo.delete(note_id, user_ctx)
        if not deleted:
            return None
        assert len(deleted) <= 1
        await self._activity_logger.note_deleted(note_id, user_ctx)
        return deleted[0]

    async def search_notes(
        self,
        search_type: str,
        query: str,
        user_ctx: UserContextABC,
        limit: int,
        offset: int,
    ) -> List[NoteEntity]:
        notes = await self._note_repo.search_notes(
            SearchType[search_type],
            query,
            ctx=user_ctx,
            pagination=Pagination(limit=limit, offset=offset),
        )
        await self._attach_directory_relations(notes, user_ctx)
        return notes

    async def _fetch_note_permissions(self, note_id: str) -> List[Relationship]:
        """Fetch every direct relationship stored for a note."""
        relations = await self._permission_repo.list_relationships(
            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
        )
        # Attachments are stored as child->parent, so look them up via
        # the reverse direction and merge.
        attachment_relations = await self._permission_repo.lookup_relationships(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, UNDEFINED),
                relation=AttachmentRelationEnum.PARENT_NOTE,
                subject=SubjectRef(ObjectTypeEnum.NOTE, note_id),
            )
        )
        return sorted(
            relations + attachment_relations,
            key=lambda rel: (
                str(rel.relation),
                str(rel.subject.object_type),
                "" if rel.subject.object_id is UNDEFINED else str(rel.subject.object_id),
            ),
        )

    async def _resolve_parent_directory_id(
        self,
        note: NoteEntity,
        user_ctx: UserContextABC,
    ) -> str:
        """Pick the directory id the new note should be parented under.

        If the caller already picked a `parent_dir_id` and that
        directory is visible to them, use it.  Otherwise fall back to
        the user's first default directory.
        """
        requested = note.parent_dir_id if note.parent_dir_id not in (UNDEFINED, None) else None
        user_directory_ids = await self._directory_repo.list_user_directory_ids(user_ctx)
        if requested is not None:
            requested_str = str(requested)
            if requested_str not in user_directory_ids:
                raise ValueError(
                    f"Provided parent_dir_id {requested_str!r} is not accessible for "
                    f"user {user_ctx.user_id!r}"
                )
            return requested_str

        default_name = self._directory_repo.get_default_directory_specs()[0].name
        directories = [
            await self._directory_repo.fetch_directory(directory_id)
            for directory_id in user_directory_ids
        ]
        matches = [d for d in directories if d and d.name == default_name]
        if len(matches) != 1 or matches[0].id in (UNDEFINED, None):
            raise ValueError(
                f"Could not resolve default directory {default_name!r} for user {user_ctx.user_id!r}"
            )
        return str(matches[0].id)

    async def _attach_directory_relations(
        self,
        notes: List[NoteEntity],
        user_ctx: UserContextABC,
    ) -> None:
        """Populate `permissions` for each note with directory relations. 
        Since the user probably has less directories than notes, we iterate over the directories, 
        and check for each, if it has a `PARENT_DIRECTORY` relation, meaning it has a child note. 
        If yes, then we append it to the note's permissions. """
        if not notes:
            return
        notes_by_id: dict[str, NoteEntity] = {
            str(note.note_id): note
            for note in notes
            if note.note_id not in (UNDEFINED, None)
        }
        if not notes_by_id:
            return
        
        # iterate all directories
        user_directory_ids = await self._directory_repo.list_user_directory_ids(user_ctx)
        for directory_id in user_directory_ids:
            # fetch note:???#PARENT_DIRECTORY@direcory:id
            relationships = await self._permission_repo.lookup_relationships(
                Relationship(
                    resource=ObjectRef(ObjectTypeEnum.NOTE, UNDEFINED),
                    relation=NoteRelationEnum.PARENT_DIRECTORY,
                    subject=SubjectRef(ObjectTypeEnum.DIRECTORY, directory_id),
                )
            )
            # check if the found relationships belong to any note contained in notes.
            for rel in relationships:
                note_id = rel.resource.object_id
                if note_id not in (UNDEFINED, None) and note_id in notes_by_id:
                    notes_by_id[note_id].permissions.append(
                        Relationship(
                            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
                            relation=NoteRelationEnum.PARENT_DIRECTORY,
                            subject=SubjectRef(ObjectTypeEnum.DIRECTORY, directory_id),
                        )
                    )

    async def _build_attachment_tokens(
        self,
        note: NoteEntity,
        user_ctx: UserContextABC,
    ) -> dict[str, str]:
        """Generate a JWT for every embedded attachment the user can read."""
        content = unwrap_undefined_or(note.content, "")
        attachment_ids: List[str] = extract_attachment_ids(content or "")

        tokens: dict[str, str] = {}
        for attachment_id in attachment_ids:
            resource = ObjectRef(object_type=ObjectTypeEnum.ATTACHMENT, object_id=attachment_id)
            if not await self._permission_repo.has_permission(
                user_ctx, "view", resource
            ):
                continue
            tokens[attachment_id] = self._jwt_provider.create_attachment_token(
                user_id=user_ctx.user_id,
                attachment_id=attachment_id,
            )
        return tokens


__all__ = ["NoteService"]