"""Concrete :class:`~src.api.note_service.NoteServiceABC` implementation.

This service composes :class:`src.api.note_facade.NoteRepoFacadeABC`
(via its :class:`~src.db.repos.note.note.NoteFacadeImpl` implementation)
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
    NoteIncludeOptions,
)
from src.api.services.note_service import GetNotesOptions, resolve_options
from src.api.services.jwt_provider import JwtProvider
from src.api.services.activity_logger_service import EventMetadataVisitor
from src.api.facades.note_facade import NoteFacadeABC, SearchType
from src.api.repos.rule_repo import RuleRepoABC
from src.api.search_filter import NoteSearchFilter
from src.api.other.relationship import AttachmentRelationEnum
from src.api.other.types import LoggingProvider, Pagination
from src.api.other.undefined import UNDEFINED, unwrap_undefined, unwrap_undefined_or
from src.api.other.user_context import UserContextABC
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.directory.directory_facade import DirectoryFacadeABC
from src.domain.permission_chain import  HasNoteDeletePerm, HasNoteViewPerm, HasNoteWritePerm
from src.utils.extract_attachments import extract_attachment_ids


class NoteServiceImpl(NoteServiceABC):
    """Concrete :class:`~src.api.note_service.NoteServiceABC` backed by `NoteFacadeImpl`.

    Owns every permission-check and relation-mutation that previously
    lived on the note facade; the facade is now a pure CRUD repo.
    """

    def __init__(
        self,
        note_repo: NoteFacadeABC,
        permission_repo: PermissionRepoABC,
        jwt_provider: JwtProvider,
        directory_repo: DirectoryFacadeABC,
        activity_logger: ActivityLoggerServiceABC,
        rule_repo: RuleRepoABC,
        logging_provider: LoggingProvider,
        now: Callable[[], datetime.datetime] = datetime.datetime.now,
    ) -> None:
        self._note_repo = note_repo
        self._permission_repo = permission_repo
        self._jwt_provider = jwt_provider
        self._directory_repo = directory_repo
        self._activity_logger = activity_logger
        self._rule_repo = rule_repo
        self._log = logging_provider(__name__, self)
        self._now = now
        self._to_metadata = EventMetadataVisitor()

    async def get_note(
        self,
        note_id: str,
        user_ctx: UserContextABC,
        *,
        include: Optional["NoteIncludeOptions"] = None,
    ) -> NoteResponse:
        check = HasNoteViewPerm(note_id).set_permission_repo(self._permission_repo)
        result = await check.check(user_ctx)
        if result.error:
            raise result.error
    
        note = await self._note_repo.select_by_id(
            note_id, user_ctx, include=include,
        )
        if note is None:
            return NoteResponse(note=None)
        
        await self._activity_logger.note_viewed(
            note_id, user_ctx,
            metadata=note.convert(self._to_metadata),
        )

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
        # Validate at the service boundary so errors surface here,
        # not deep in the facade.  NoteFacadeImpl.insert re-runs the
        # same resolution once we let it through.
        await self._validate_insert_parent(note, user_ctx)

        if not note.updated_at:
            note.updated_at = self._now()

        inserted = await self._note_repo.insert(note, user_ctx)
        await self._activity_logger.note_created(
            str(inserted.note_id), user_ctx,
            metadata=inserted.convert(self._to_metadata),
        )

        return inserted

    async def _validate_insert_parent(
        self,
        note: NoteEntity,
        user_ctx: UserContextABC,
    ) -> None:
        """Raise ValueError when no usable parent directory can be resolved."""
        directory_ids = note.directory_ids
        has_dirs = directory_ids is not UNDEFINED and bool(directory_ids)
        shelf_ids = note.shelf_ids
        has_shelf = shelf_ids is not UNDEFINED and bool(shelf_ids)

        if has_dirs:
            user_directory_ids = await self._directory_repo.list_user_directory_ids(
                user_ctx,
            )
            for did in directory_ids or []:
                if not did:
                    continue
                if str(did) not in user_directory_ids:
                    raise ValueError(
                        f"Provided directory_id '{did!r}' is not accessible "
                        f"for user {user_ctx.user_id!r}"
                    )
            return

        if not has_shelf:
            raise ValueError(
                "note insert requires either directory_ids or a shelf_id "
                f"to scope the default-fleeting rule (user {user_ctx.user_id!r})"
            )

        shelf_id = str(next(v for v in (shelf_ids or []) if v))
        rules = await self._rule_repo.list_rules(
            event_type="NoteCreated",
            attached_entity_type="shelf",
            attached_entity_id=shelf_id,
            enabled_only=True,
        )
        if not any(r.action_type == "add_to_directory" for r in rules):
            raise ValueError(
                f"shelf_id {shelf_id!r} has no enabled NoteCreated rule "
                f"with an 'add_to_directory' action for user "
                f"{user_ctx.user_id!r}; create the rule or pass "
                f"directory_ids explicitly"
            )

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
        await self._activity_logger.note_deleted(
            note_id, user_ctx,
            metadata=deleted[0].convert(self._to_metadata),
        )
        return deleted[0]

    async def search_notes(
        self,
        search_type: str,
        query: str,
        user_ctx: UserContextABC,
        limit: int,
        offset: int,
        *,
        filter_: Optional[NoteSearchFilter] = None,
    ) -> List[NoteEntity]:
        from src.api.search_filter import validate_search_filter

        if filter_ is not None:
            validate_search_filter(filter_)
        notes = await self._note_repo.search_notes(
            SearchType[search_type],
            query,
            ctx=user_ctx,
            pagination=Pagination(limit=limit, offset=offset),
            filter_=filter_,
        )
        await self._populate_directory_ids(notes)
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
        include_content = resolved.get("include_content", True)
        strip_content_at = resolved.get("strip_content_at", 0)
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

    async def _populate_directory_ids(
        self,
        notes: List[NoteEntity],
    ) -> None:
        """Populate directory_ids for every note from the directory repo.

        Asks the directory repo for the parents of every note in
        one batch call and assigns the result to note.directory_ids.
        """
        if not notes:
            return
        note_ids = [unwrap_undefined(n.note_id) for n in notes]
        parents_by_note = await self._directory_repo.get_parents_for(
            "note", note_ids, "directory",
        )
        for note, note_id in zip(notes, note_ids):
            note.directory_ids = parents_by_note.get(note_id, [])

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


__all__ = ["NoteServiceImpl"]