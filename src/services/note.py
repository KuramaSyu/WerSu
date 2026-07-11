"""Concrete :class:`~src.api.note_service.NoteServiceABC` implementation.

This service composes :class:`src.api.note_facade.NoteRepoFacadeABC`
(via its :class:`~src.db.repos.note.note.NoteFacade` implementation)
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

import datetime
from typing import Callable, List, Optional

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
from src.api.note_service import GetNotesOptions, resolve_options
from src.api.jwt_provider import JwtProvider
from src.api.note_facade import NoteRepoFacadeABC, SearchType
from src.api.relationship import AttachmentRelationEnum
from src.api.types import Pagination
from src.api.undefined import UNDEFINED, unwrap_undefined, unwrap_undefined_or
from src.api.user_context import UserContextABC
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.directory.directory import DirectoryRepo
from src.domain.permission_chain import HasDirectoryWritePerm, HasNoteDeletePerm, HasNoteWritePerm
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
        now: Callable[[], datetime.datetime] = datetime.datetime.now,
    ) -> None:
        self._note_repo = note_repo
        self._permission_repo = permission_repo
        self._jwt_provider = jwt_provider
        self._directory_repo = directory_repo
        self._activity_logger = activity_logger
        self._now = now

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
        note.parent_dir_id = parent_directory_id

        if not note.updated_at:
            note.updated_at = self._now()

        # insert note itself along with directory relation and owner relation
        inserted = await self._note_repo.insert(note, user_ctx)

        await self._activity_logger.note_created(str(inserted.note_id), user_ctx)

        # a local copy for later usage; this already got inserted in the note repo
        parent_dir_relation = Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, inserted.note_id),
            relation=NoteRelationEnum.PARENT_DIRECTORY,
            subject=SubjectRef(ObjectTypeEnum.DIRECTORY, parent_directory_id),
        )

        # check directories again -- is this really necessary?
        inserted.permissions = await self._fetch_note_permissions(str(inserted.note_id))
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
        if write_result.error:
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
        if delete_result.error:
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

    async def get_notes(
        self,
        note_ids: List[str],
        user_ctx: UserContextABC,
        options: Optional[GetNotesOptions] = None,
    ) -> List[NoteEntity]:
        """Bulk version of :meth:`get_note`.

        Resolves every id through :meth:`select_by_ids`, enforces the
        read permission per note, and applies the `options` shaping
        (drop or truncate `content`).  Per-note activity logging is
        intentionally skipped here - this method is used by list
        endpoints that read many notes at once.

        Args:
            note_ids: ids to resolve.
            user_ctx: caller identity used for permission checks.
            options: optional :class:`GetNotesOptions`; see the
                docstring on
                :meth:`~src.api.note_service.NoteServiceABC.get_notes`.

        Raises:
            ValueError: when `note_ids` is empty or any id is
                missing.
            TypeError: when `options` is not a mapping.

        Returns:
            List[NoteEntity]: resolved notes in `note_ids` order.
        """
        if not note_ids:
            raise ValueError("note_ids must not be empty")

        resolved = resolve_options(options)

        notes = await self._note_repo.select_by_ids(note_ids, user_ctx)

        # check read permission per note; mirrors the per-id paths
        # that gate on Has*ViewPerm (or rely on select_by_id to mask
        # invisible ones).  Centralising this keeps policy auditable.
        from src.domain.permission_chain import HasNoteViewPerm  # local to avoid import cycle

        for note in notes:
            read_check = HasNoteViewPerm(str(note.note_id)).set_permission_repo(
                self._permission_repo
            )
            read_result = await read_check.check(user_ctx)
            if read_result.error:
                raise read_result.error

        # apply content shaping
        include_content = bool(resolved.get("include_content", True))
        strip_content_at = int(resolved.get("strip_content_at", 0))
        for note in notes:
            if not include_content:
                note.content = None
                continue
            if strip_content_at > 0 and isinstance(note.content, str):
                if len(note.content) > strip_content_at:
                    note.content = note.content[:strip_content_at]
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
        """ `note.parent_dir_id` if user has permission for that, otherwise the first default directory (fleeting notes)"""
        requested = note.parent_dir_id
        user_directory_ids = await self._directory_repo.list_user_directory_ids(user_ctx)

        # requested does not exist
        if requested and requested not in user_directory_ids:
            raise ValueError(
                f"Provided parent_dir_id {requested!r} is not accessible for "
                f"user {user_ctx.user_id!r}"
            )
    
        # check write permission for the requested directory
        if requested:
            chain = HasDirectoryWritePerm(requested).set_permission_repo(self._permission_repo)
            result = await chain.check(user_ctx)
            if result.error:
                raise result.error
            return requested
        
        # requested is UNDEFINED -> return default directory
        # get hardcoded default directory name
        default_name = self._directory_repo.get_default_directory_specs()[0].name

        # check lazyly for the name by traversing through all the users directories
        for d_id in user_directory_ids:
            d = await self._directory_repo.fetch_directory(d_id)
            if d and d.name == default_name:
                return unwrap_undefined(d.id)

        raise ValueError(
            f"Could not resolve default directory {default_name!r} for user {user_ctx.user_id!r}"
        )
    
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
                note_id = unwrap_undefined(rel.resource.object_id)
                target_note = notes_by_id.get(note_id)

                if not target_note:
                    # should never happen
                    continue

                if not target_note.permissions:
                    target_note.permissions = []
                
                target_note.permissions.append(
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