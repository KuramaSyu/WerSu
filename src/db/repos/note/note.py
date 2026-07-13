"""Note facade composing the storage / permission / embedding repos.

Public methods follow the
:class:`~src.api.note_facade.NoteRepoFacadeABC` contract.  Every
SQL statement lives in the repos the facade delegates to
(:class:`~src.db.repos.note.content.NoteContentRepo`,
:class:`~src.db.repos.note.combined.CombinedNotePostgresRepo`,
:class:`NoteTagPostgresRepo`, the embedding / version repos).  The
facade itself does **not** issue SQL -- it only orchestrates.

The :class:`Database` handle injected via the constructor is the
one exception: search strategies live in their own module and own
their own SQL, so the facade hands them `self._db` at dispatch
time and otherwise ignores it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.api import NoteRelationEnum, ObjectRef, ObjectTypeEnum, Relationship, SubjectRef
from src.api.repos.combined_note_repo import CombinedNoteRepoABC
from src.api.facades.note_facade import NoteRepoFacadeABC, SearchType
from src.api.services.note_service import NoteIncludeOptions, resolve_include_options
from src.api.repos.note_tag_repo import NoteTagRepoABC
from src.api.other.relationship import AttachmentRelationEnum
from src.api.other.types import LoggingProvider, Pagination
from src.api.other.undefined import UNDEFINED, is_undefined, unwrap_undefined_or
from src.api.other.user_context import UserContextABC
from src.db import Database
from src.db.entities import NoteEntity
from src.db.repos.directory.directory import DirectoryFacadeABC
from src.db.repos.note.content import NoteContentRepo
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.db.repos.note.search_strategy import (
    ContextNoteSearchStrategy,
    DateNoteSearchStrategy,
    FuzzyTitleContentSearchStrategy,
    WebNoteSearchStrategy,
)
from src.db.repos.note.versioning import NoteVersionRepoABC
from src.db.repos.permissions import PermissionRepoABC
from src.utils.logging import logging_provider


class NoteFacadeImpl(NoteRepoFacadeABC):
    """Compose the note repos without issuing raw SQL.

    The facade is intentionally SQL-free: every storage call is
    routed through the repos passed in.  Search strategies are
    configured with the same ``note_permissions`` they always
    were; that is the strategy layer's own contract, not the
    facade's.

    The `db` argument is the only raw handle the facade holds,
    and it is used exclusively to forward to the search
    strategies.
    """

    def __init__(
        self,
        db: Database,
        content_repo: NoteContentRepo,
        combined_repo: CombinedNoteRepoABC,
        embedding_repo: NoteEmbeddingRepo,
        permission_repo: PermissionRepoABC,
        directory_repo: DirectoryFacadeABC,
        tag_repo: NoteTagRepoABC,
        logging_provider: LoggingProvider,
        version_repo: NoteVersionRepoABC,
    ):
        self._db = db
        self._content_repo = content_repo
        self._combined_repo = combined_repo
        self._embedding_repo = embedding_repo
        self._permission_repo = permission_repo
        self._directory_repo = directory_repo
        self._tag_repo = tag_repo
        self._version_repo = version_repo
        self.log = logging_provider(__name__, self)

    # ---- private helpers ---------------------------------------------

    async def _fetch_note_permissions(
        self,
        note_id: str,
    ) -> List[Relationship]:
        """Fetch every direct relationship stored for a note.

        Combines the regular note relations with the reverse
        ``attachment#parent_note@note`` lookups.

        Args:
            note_id: id of the note whose direct relations to
                fetch.

        Returns:
            List[Relationship]: the merged, sorted relation list
                the caller can attach to ``note.permissions``.
        """
        relations = await self._permission_repo.list_relationships(
            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
        )
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

    async def _resolve_directory_ids(
        self,
        requested_ids: Optional[List[str]],
        user: UserContextABC,
    ) -> List[str]:
        """Pick the directory ids for a freshly-inserted note.

        Behaviour:
        * ``requested_ids`` empty / UNDEFINED -> fall back to the
          user's default zettelkasten directory.
        * non-empty -> validate the caller has access to every id,
          return them as the resolved parent set.
        """
        user_directory_ids = await self._directory_repo.list_user_directory_ids(
            user
        )
        if requested_ids:
            for did in requested_ids:
                if not did:
                    continue
                if str(did) not in user_directory_ids:
                    raise ValueError(
                        f"Provided directory_id '{did!r}' is not accessible "
                        f"for user {user.user_id!r}"
                    )
            return [str(d) for d in requested_ids if d]

        # Fall back to the default ("fleeting_notes") directory.
        default_slug = (
            self._directory_repo.get_default_directory_specs()[0].name
        )
        self.log.info(
            f"No directory_ids supplied for note insert; "
            f"resolving default directory {default_slug!r} for "
            f"user {user.user_id!r} by scanning {len(user_directory_ids)} dirs"
        )
        for d_id in user_directory_ids:
            d = await self._directory_repo.fetch_directory(d_id)
            if d and d.slug == default_slug:
                return [str(d.id)]
        raise ValueError(
            f"Could not resolve default directory {default_slug!r} "
            f"for user {user.user_id!r}"
        )

    # ---- insert / update ---------------------------------------------

    async def insert(self, note: NoteEntity, user: UserContextABC):
        """Insert a note, its embedding, parent-directory bindings and tags.

        Args:
            note: payload carrying the scalar fields plus the
                optional `directory_ids` and `tag_ids`.
            user: caller identity; becomes the owner relation and
                scopes the parent-directory lookup.

        Returns:
            NoteEntity: the persisted note with id and permissions
            populated.
        """
        # 1) row
        inserted = await self._content_repo.insert(note)
        note_id = inserted.note_id
        if not note_id:
            raise RuntimeError("content repo returned no note id")
        note_id = str(note_id)
        self.log.debug(f"Inserted note with ID: {note_id}")
        note.note_id = note_id

        # 2) embedding
        note.embeddings = []
        if note.content:
            embedding = await self._embedding_repo.insert(
                note_id,
                note.title if note.title else "",
                note.content,
            )
            note.embeddings.append(embedding)

        # 3) parent directories (multi)
        if note.directory_ids is UNDEFINED:
            resolved_dirs = await self._resolve_directory_ids(None, user)
        else:
            resolved_dirs = await self._resolve_directory_ids(
                list(unwrap_undefined_or(note.directory_ids, [])), user,  # type: ignore
            )
            # if the given dirs reoslved nothing, then get default dirs
            if not resolved_dirs:
                resolved_dirs = await self._resolve_directory_ids(None, user)

        for directory_id in resolved_dirs:
            await self._directory_repo.add_note_to_directory(
                note_id, directory_id,
            )

        # 4) tags
        if note.tag_ids is not UNDEFINED:
            tag_ids = unwrap_undefined_or(note.tag_ids, [])
            await self._tag_repo.replace_note_tags(
                note_id, [str(t) for t in tag_ids if t],
            )

        # 5) note#owner@user permission
        owner_relation = Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
            relation=NoteRelationEnum.OWNER,
            subject=SubjectRef(ObjectTypeEnum.USER, user.user_id),
        )
        await self._permission_repo.insert([owner_relation])
        note.permissions = await self._fetch_note_permissions(note_id=note_id)

        # 6) version snapshot
        title_value: Optional[str] = unwrap_undefined_or(note.title)
        content_value: Optional[str] = unwrap_undefined_or(note.content)
        author_value: str = (
            str(note.author_id) if note.author_id is not UNDEFINED
            else str(user.user_id)
        )
        created_at: datetime = unwrap_undefined_or(
            note.updated_at, datetime.now(),
        )
        await self._version_repo.record_initial_snapshot(
            note_id=note_id,
            title=title_value,
            content=content_value,
            author_id=author_value,
            created_at=created_at,
        )
        return note

    async def update(self, note: NoteEntity, ctx: UserContextABC) -> NoteEntity:
        # fetch current state for versioning before applying updates
        current = await self._content_repo.select_by_id(str(note.note_id))

        updated = await self._content_repo.update(
            set=_strip_non_content_fields(note),
            where=NoteEntity(note_id=note.note_id),
        )

        # Embedding refresh.
        if note.content and note.note_id:
            embedding = await self._embedding_repo.update(
                note.note_id,
                note.title if note.title else "",
                note.content,
            )
            updated.embeddings = [embedding]

        # replace tags when given
        if note.tag_ids is not UNDEFINED:
            tag_ids = unwrap_undefined_or(note.tag_ids, [])
            await self._tag_repo.replace_note_tags(
                str(note.note_id), [str(t) for t in tag_ids if t],
            )

        if note.permissions is UNDEFINED:
            updated.permissions = []

        new_title: str = unwrap_undefined_or(
            note.title, str(current.title),
        )
        new_content: str = unwrap_undefined_or(
            note.content, str(current.content),
        )
        new_author_id: str = (
            str(note.author_id) if note.author_id is not UNDEFINED
            else str(current.author_id)
        )
        new_updated_at: datetime = unwrap_undefined_or(
            note.updated_at, datetime.now(),
        )

        await self._version_repo.append_version(
            note_id=str(note.note_id),
            old_title=unwrap_undefined_or(current.title),
            old_content=unwrap_undefined_or(current.content),
            new_title=new_title,
            new_content=new_content,
            author_id=new_author_id,
            created_at=new_updated_at,
        )

        return updated

    async def delete(self, note_id: str, ctx: UserContextABC) -> Optional[List[NoteEntity]]:
        return await self._content_repo.delete(
            NoteEntity(note_id=note_id, author_id=ctx.user_id)
        )

    # ---- select ------------------------------------------------------

    async def select_by_id(
        self,
        note_id: str,
        ctx: UserContextABC,
        *,
        include: Optional[NoteIncludeOptions] = None,
        include_permissions: bool = True,
    ) -> Optional[NoteEntity]:
        """Resolve a single note by id, with the requested enrichment.

        The cheap row-only fetch and the three JOIN shapes live on
        :class:`CombinedNoteRepo`.  Permissions are layered on top
        so each :meth:`NoteServiceABC.get_note` consumer gets a
        ready-to-return entity.
        """
        include_opts = resolve_include_options(include)
        entity = await self._combined_repo.select_by_id(
            note_id, include=include_opts,
        )
        if not entity:
            return None
        if include_permissions:
            entity.permissions = await self._fetch_note_permissions(
                note_id=note_id,
            )
        return entity

    async def select_by_ids(
        self,
        note_ids: List[str],
        ctx: UserContextABC,
        *,
        include: Optional[NoteIncludeOptions] = None,
        include_permissions: bool = True,
    ) -> List[NoteEntity]:
        """Bulk variant of :meth:`select_by_id`."""
        include_opts = resolve_include_options(include)
        entities = await self._combined_repo.select_by_ids(
            note_ids, include=include_opts,
        )
        if not include_permissions:
            return entities
        for note in entities:
            note.permissions = await self._fetch_note_permissions(
                note_id=str(note.note_id),
            )
        return entities

    # ---- search ------------------------------------------------------

    async def search_notes(
        self,
        search_type: SearchType,
        query: str,
        ctx: UserContextABC,
        pagination: Pagination,
    ) -> List[NoteEntity]:
        """Run a search strategy and tidy up the returned entities.

        Strategies own their own SQL (they predate the facade's
        SQL-free contract and live in their own module).  The
        facade passes its `db` handle through to the strategy
        only -- nothing else in the facade requires it.

        Method body orchestration:

        1. Picks the right strategy for `search_type`.
        2. Normalises `UNDEFINED` list fields to ``[]`` so the
           gRPC layer doesn't have to.
        3. Augments `note.permissions` with the user's
           ``parent_directory`` SpiceDB relations so the search
           result row mirrors the per-id view.

        Args:
            search_type: which strategy to run.
            query: search text; interpretation depends on
                `search_type`.
            ctx: caller identity used to scope the result set.
            pagination: offset / limit window for the search.
        """
        common_init_parameters = {
            "db": self._db,
            "query": query,
            "limit": pagination.limit,
            "offset": pagination.offset,
            "user_context": ctx,
            "note_permissions": self._permission_repo,
        }
        strategy = self._strategy_for(search_type, common_init_parameters)

        note_entities = await strategy.search()
        for note in note_entities:
            if note.permissions is UNDEFINED:
                note.permissions = []
            if note.directory_ids is UNDEFINED:
                note.directory_ids = []
            if note.tag_ids is UNDEFINED:
                note.tag_ids = []

        note_entities_dict: Dict[str, NoteEntity] = {
            str(note.note_id): note
            for note in note_entities
            if note.note_id is not UNDEFINED
        }
        await self._enrich_with_parent_directory_permissions(
            ctx, note_entities_dict,
        )
        return note_entities

    def _strategy_for(
        self,
        search_type: SearchType,
        common_init_parameters: Dict[str, Any],
    ):
        """Pick the :class:`NoteSearchStrategy` for ``search_type``.

        Helper that maps the :class:`SearchType` enum to the
        matching concrete strategy.  Raises :exc:`ValueError`
        for unknown values.
        """
        if search_type == SearchType.NO_SEARCH:
            return DateNoteSearchStrategy(**common_init_parameters)
        if search_type == SearchType.FULL_TEXT_TITLE:
            return WebNoteSearchStrategy(**common_init_parameters)
        if search_type == SearchType.FUZZY:
            return FuzzyTitleContentSearchStrategy(**common_init_parameters)
        if search_type == SearchType.CONTEXT:
            return ContextNoteSearchStrategy(
                **common_init_parameters,
                generator=self._embedding_repo.embedding_generator,
            )
        raise ValueError(f"Unknown SearchType: {search_type}")

    async def _enrich_with_parent_directory_permissions(
        self,
        ctx: UserContextABC,
        note_entities_dict: Dict[str, NoteEntity],
    ) -> None:
        """Augment each note with the user's ``parent_directory`` relations.

        Mirrors the per-id behaviour of
        :meth:`NoteServiceImpl.get_note` -- search callers see the same
        parent-directory relations the per-id view would have
        returned.
        """
        if not note_entities_dict:
            return
        user_directory_ids = await self._directory_repo.list_user_directory_ids(ctx)
        for directory_id in user_directory_ids:
            note_ids = await self._permission_repo.lookup(
                Relationship(
                    resource=ObjectRef(ObjectTypeEnum.NOTE, UNDEFINED),
                    relation=NoteRelationEnum.PARENT_DIRECTORY,
                    subject=SubjectRef(
                        object_type=ObjectTypeEnum.DIRECTORY,
                        object_id=directory_id,
                    ),
                )
            )
            for note_id in note_ids:
                if note_id and note_entities_dict.get(note_id):
                    note_entities_dict[note_id].permissions.append(  # type: ignore
                        Relationship(
                            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
                            relation=NoteRelationEnum.PARENT_DIRECTORY,
                            subject=SubjectRef(
                                ObjectTypeEnum.DIRECTORY, directory_id,
                            ),
                        )
                    )


def _strip_non_content_fields(note: NoteEntity) -> NoteEntity:
    """Return a copy of ``note`` with relation fields cleared.

    The ``content_repo.update`` method writes only the columns on
    the ``note.content`` row -- the relation / list fields
    (``embeddings``, ``permissions``, ``directory_ids``,
    ``tag_ids``) must not bleed into the SET clause.
    """
    return NoteEntity(
        note_id=UNDEFINED,
        title=note.title,
        content=note.content,
        updated_at=note.updated_at,
        author_id=note.author_id,
        embeddings=UNDEFINED,
        permissions=UNDEFINED,
        directory_ids=UNDEFINED,
        tag_ids=UNDEFINED,
    )
