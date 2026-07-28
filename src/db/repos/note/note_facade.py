"""Note facade composing the storage / permission / embedding repos.

Public methods follow the
:class:`~src.api.note_facade.NoteRepoFacadeABC` contract.  Every
SQL statement lives in the repos the facade delegates to
(:class:`~src.db.repos.note.content.NoteContentRepo`,
:class:`~src.db.repos.note.combined.CombinedNotePostgresRepo`,
:class:`src.db.repos.tag.postgres.PostgresTagRepo`, the embedding
/ version repos).  The facade itself does **not** issue SQL --
it only orchestrates.

The :class:`Database` handle injected via the constructor is the
one exception: search strategies live in their own module and own
their own SQL, so the facade hands them `self._db` at dispatch
time and otherwise ignores it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from warnings import deprecated

from src.api import NoteRelationEnum, ObjectRef, ObjectTypeEnum, Relationship, SubjectRef
from src.api.repos.combined_note_repo import CombinedNoteRepoABC
from src.api.facades.note_facade import NoteFacadeABC, SearchType
from src.api.repos.tag_repo import TagRepoABC
from src.api.services.note_service import NoteIncludeOptions, resolve_include_options
from src.api.other.relationship import AttachmentRelationEnum
from src.api.other.types import LoggingProvider, Pagination
from src.api.other.undefined import UNDEFINED, unwrap_undefined, unwrap_undefined_or
from src.api.other.user_context import UserContextABC
from src.db import Database
from src.db.entities import NoteEntity
from src.db.repos.directory.directory import DirectoryFacadeABC
from src.db.repos.note.content import NoteContentRepo
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.db.repos.note.search_strategy import (
    ContextNoteSearchStrategy,
    DateNoteSearchStrategy,
    SimilaritySearchStrategy,
    WebNoteSearchStrategy,
)
from src.db.repos.note.versioning import NoteVersionRepoABC
from src.db.repos.permissions import PermissionRepoABC


class NoteFacadeImpl(NoteFacadeABC):
    """Compose the note repos without issuing raw SQL.

    NoteFacadeImpl orchestrates the content, embedding, permission (insertion only), directory and tag repos.
    Each returned :class:`~src.db.entities.note.metadata.NoteEntity` should have:
    - `directory_ids` 
    - `tag_ids`
    - ~`permissions`~ (this is replaced with directory ids and tag ids)

    The `db` argument is required, since the serach strategies execute direct SQL
    """

    # TODO: constructor overinjection here
    def __init__(
        self,
        db: Database,
        content_repo: NoteContentRepo,
        combined_repo: CombinedNoteRepoABC,
        embedding_repo: NoteEmbeddingRepo,
        permission_repo: PermissionRepoABC,
        directory_repo: DirectoryFacadeABC,
        tag_repo: TagRepoABC,
        logging_provider: LoggingProvider,
        version_repo: NoteVersionRepoABC,
    ):
        self._db = db
        self._content_repo = content_repo
        self._combined_repo = combined_repo
        self._embedding_repo = embedding_repo
        self._permission_repo = permission_repo
        self._directory_facade = directory_repo
        self._tag_repo = tag_repo
        self._version_repo = version_repo
        self.log = logging_provider(__name__, self)

    # ---- private helpers ---------------------------------------------

    @deprecated("dont populate note.permissions anymore")
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
        """Get the directory ids for a freshly-inserted note. Either
        use the user-supplied list of directories or fall back to the default directory.
        """
        user_directory_ids = await self._directory_facade.list_user_directory_ids(
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
            self._directory_facade.get_default_directory_specs()[0].name
        )
        self.log.info(
            f"No directory_ids supplied for note insert; "
            f"resolving default directory {default_slug!r} for "
            f"user {user.user_id!r} by scanning {len(user_directory_ids)} dirs"
        )
        for d_id in user_directory_ids:
            d = await self._directory_facade.fetch_directory(d_id)
            if d and d.slug == default_slug:
                return [str(d.id)]
        raise ValueError(
            f"Could not resolve default directory {default_slug!r} "
            f"for user {user.user_id!r}"
        )

    async def _populate_relation_fields(
        self,
        note: NoteEntity,
        note_id: str,
    ) -> NoteEntity:
        """Refresh `note.directory_ids` / `tag_ids` after a write path.

        Used by the write paths (`insert` / `update` /
        `search_notes`) to mirror the freshly-written
        parent-directory + tag bindings back onto the in-memory
        entity before it's returned to the caller.  The read
        paths (`select_by_id` / `select_by_ids`) skip this
        helper entirely -- the combined repo's
        `NoteFetchStrategyABC` SQL already returns
        `directory_ids` / `tag_ids` / `attachment_ids`
        alongside the note row.

        Args:
            note: entity to enrich; mutated in place.
            note_id: id used for the directory / tag lookups.

        Returns:
            NoteEntity: updated version (same object)
        """
        note.directory_ids = await self._directory_facade.get_parent_of(
            "note", note_id,
        )
        note.tag_ids = await self._tag_repo.list_tags_of("note", note_id)
        return note

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
        # insert main content
        inserted = await self._content_repo.insert(note)
        note_id = inserted.note_id
        if not note_id:
            raise RuntimeError("content repo returned no note id")
        note_id = str(note_id)
        self.log.debug(f"Inserted note with ID: {note_id}")
        note.note_id = note_id

        # insert embedding
        note.embeddings = []
        if note.content:
            embedding = await self._embedding_repo.insert(
                note_id,
                note.title if note.title else "",
                note.content,
            )
            note.embeddings.append(embedding)

        # resolve parent directories
        if note.directory_ids is UNDEFINED:
            resolved_dirs = await self._resolve_directory_ids(None, user)
        else:
            resolved_dirs = await self._resolve_directory_ids(
                list(unwrap_undefined_or(note.directory_ids, [])), user,  # type: ignore
            )
            # if the given dirs reoslved nothing, then get default dirs
            if not resolved_dirs:
                resolved_dirs = await self._resolve_directory_ids(None, user)

        # assign the resolved directory ids. reset note.directory_ids and
        # repopulate it to ensure consistency if one call would fail.
        note.directory_ids = []
        for directory_id in resolved_dirs:
            await self._directory_facade.add_child_to_directory(
                "note", str(directory_id), str(note_id),
            )
            note.directory_ids.append(directory_id)


        # insert tags
        if note.tag_ids is not UNDEFINED:
            tag_ids = note.tag_ids or []
            await self._tag_repo.replace_tags_of(
                "note", note_id, [str(t) for t in tag_ids if t],
            )

        # note#owner@user permission
        owner_relation = Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
            relation=NoteRelationEnum.OWNER,
            subject=SubjectRef(ObjectTypeEnum.USER, user.user_id),
        )
        await self._permission_repo.insert([owner_relation])

        # deprecated: dont populate note.permissions
        # note.permissions = await self._fetch_note_permissions(note_id=note_id)
        # replacement: read directory_ids and tag_ids back from their
        # dedicated repos so the returned entity matches the persisted state.
        note = await self._populate_relation_fields(note, note_id)
        # match the `update` path: a fresh insert carries no
        # caller-supplied permissions, so the returned entity
        # reflects the same `[]` shape as after an update.
        if note.permissions is UNDEFINED:
            note.permissions = []

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
            tag_ids = note.tag_ids or []
            await self._tag_repo.replace_tags_of(
                "note", unwrap_undefined(note.note_id), tag_ids,
            )

        # replace dirs when given
        if note.directory_ids:
            directory_ids: List[str] = note.directory_ids or []
            await self._directory_facade.set_parent_directories_of("note", unwrap_undefined(note.note_id), directory_ids)

        # `permissions` deprecated; the returned `updated` therefore comes back with the
        # dataclass default `UNDEFINED`
        if updated.permissions is UNDEFINED:
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

        # replacement: read directory_ids and tag_ids back from their
        # dedicated repos so the returned entity matches the persisted state.
        updated = await self._populate_relation_fields(updated, str(note.note_id))
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

        `directory_ids` and `tag_ids` are always refreshed from
        the directory / tag repos before returning so callers
        see the same shape the write paths produced -- the
        combined repo's side-table JOINs are honoured when they
        carry data, but this facade remains the source of truth.
        """
        include_opts = resolve_include_options(include)
        note = await self._combined_repo.select_by_id(
            note_id, include=include_opts,
        )
        if note is not None:
            await self._populate_relation_fields(note, note_id)
        return note

    async def select_by_ids(
        self,
        note_ids: List[str],
        ctx: UserContextABC,
        *,
        include: Optional[NoteIncludeOptions] = None,
        include_permissions: bool = True,
    ) -> List[NoteEntity]:
        """Bulk variant of :meth:`select_by_id`.

        `directory_ids` and `tag_ids` are refreshed per-id from
        the directory / tag repos -- see :meth:`select_by_id`.
        """
        include_opts = resolve_include_options(include)
        notes = await self._combined_repo.select_by_ids(
            note_ids, include=include_opts,
        )
        for note in notes:
            await self._populate_relation_fields(note, str(note.note_id))
        return notes

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
        common_init_parameters: Dict[str, Any] = {
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
            # replacement: read directory_ids and tag_ids back from their
            # dedicated repos so the search result rows mirror the per-id
            # view of parent-directory and tag bindings.
            await self._populate_relation_fields(note, str(note.note_id))
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
            return SimilaritySearchStrategy(**common_init_parameters)
        if search_type == SearchType.CONTEXT:
            return ContextNoteSearchStrategy(
                **common_init_parameters,
                generator=self._embedding_repo.embedding_generator,
            )
        raise ValueError(f"Unknown SearchType: {search_type}")



def _strip_non_content_fields(note: NoteEntity) -> NoteEntity:
    """Return a copy of ``note`` with relation fields cleared.

    The ``content_repo.update`` method writes only the columns on
    the ``note.content`` row -- the relation / list fields
    (``embeddings``, ``permissions``, ``directory_ids``,
    ``tag_ids``, ``attachment_ids``) must not bleed into the SET
    clause.
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
        attachment_ids=UNDEFINED,
    )
