"""Note facade composing the storage / permission / embedding repos.

Public methods follow the
:class:`~src.api.note_facade.NoteRepoFacadeABC` contract.  Every
SQL statement lives in the repos the facade delegates to
(:class:`~src.api.repos.note_content_repo.NoteContentRepo`,
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

from dataclasses import fields, replace
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.api import NoteRelationEnum, ObjectRef, ObjectTypeEnum, Relationship, SubjectRef
from src.api.other.relationship import (
    ShelfRelationEnum
)
from src.api.repos.combined_note_repo import CombinedNoteRepoABC
from src.api.facades.note_facade import NoteFacadeABC, SearchType
from src.api.repos.rule_repo import RuleRepoABC
from src.api.repos.tag_repo import TagRepoABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.api.search_filter import NoteSearchFilter
from src.api.services.note_service import NoteIncludeOptions, resolve_include_options
from src.api.other.types import LoggingProvider, Pagination
from src.api.other.undefined import UNDEFINED, unwrap_undefined, unwrap_undefined_or
from src.api.other.user_context import UserContextABC
from src.db import Database
from src.db.entities import NoteEntity
from src.db.repos.directory.directory_facade import DirectoryFacadeABC
from src.api.repos.note_content_repo import NoteContentRepo
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
        shelf_repo: ShelfRepoABC,
        rule_repo: RuleRepoABC,
    ):
        self._db = db
        self._content_repo = content_repo
        self._combined_repo = combined_repo
        self._embedding_repo = embedding_repo
        self._permission_repo = permission_repo
        self._directory_facade = directory_repo
        self._tag_repo = tag_repo
        self._version_repo = version_repo
        # Powers the default-fleeting rule lookup in _resolve_directory_ids.
        self._shelf_repo = shelf_repo
        self._rule_repo = rule_repo
        self.log = logging_provider(__name__, self)

    # ---- private helpers ---------------------------------------------

    async def _resolve_directory_ids(
        self,
        requested_ids: Optional[List[str]],
        user: UserContextABC,
        shelf_id: Optional[str] = None,
    ) -> List[str]:
        """Get the directory ids for a freshly-inserted note.

        Either use the user-supplied list of directories (validated
        against the user's visibility) or, when no directory was
        specified, fall back to the user's default-fleeting rule:

        1. Find the user's ``users_shelf`` (every bootstrap user
           has one).  The shelf id is the scope anchor for the
           default rule.
        2. Look up an enabled ``NoteCreated`` rule attached to
           that shelf with an ``add_to_directory`` action.
        3. Return the action's ``directory_id``.

        Raises ``ValueError`` when the user has no such rule --
        inserts with no parent directory are no longer allowed to
        silently fall back to ``fleeting_notes``; users opt into
        the default by creating the rule (the user service does
        this on :func:`create_user`).
        """
        user_directory_ids = await self._directory_facade.list_user_directory_ids(
            user
        )
        if requested_ids:
            for did in requested_ids:
                if not did:
                    continue
                if did not in user_directory_ids:
                    raise ValueError(
                        f"Provided directory_id '{did!r}' is not accessible "
                        f"for user {user.user_id!r}"
                    )
            return [str(d) for d in requested_ids if d]

        rules = await self._fetch_default_fleeting_rules_for(user, shelf_id)
        if not rules:
            where = (
                f"attached to shelf {shelf_id!r}"
                if shelf_id
                else "for this user"
            )
            raise ValueError(
                f"No directory_ids supplied for note insert and "
                f"user {user.user_id!r} has no default-fleeting rule "
                f"{where}; create a NoteCreated rule with an "
                f"'add_to_directory' action or pass directory_ids "
                f"explicitly."
            )
        # Schema doesn't enforce uniqueness on (attached_entity_id, event_type, enabled).
        rule = rules[0]
        action_context = rule.action_context or {}
        directory_id = action_context.get("directory_id")
        if not isinstance(directory_id, str) or not directory_id:
            raise ValueError(
                f"Default-fleeting rule {rule.id!r} has no "
                f"'directory_id' in its action_context; cannot "
                f"resolve default directory for user "
                f"{user.user_id!r}"
            )
        if directory_id not in user_directory_ids:
            raise ValueError(
                f"Default-fleeting rule {rule.id!r} points at "
                f"directory {directory_id!r} which user "
                f"{user.user_id!r} cannot access"
            )
        return [directory_id]

    async def _fetch_default_fleeting_rules_for(
        self,
        user: UserContextABC,
        shelf_id: Optional[str] = None,
    ) -> List[Any]:
        """Return enabled NoteCreated rules attached to a shelf (or every shelf)."""
        try:
            if shelf_id:
                # -> shelf id given: fetch rule directly
                return await self._rule_repo.list_rules(
                    event_type="NoteCreated",
                    attached_entity_type="shelf",
                    attached_entity_id=shelf_id,
                    enabled_only=True,
                )

            shelf_ids = await self._shelfs_user_can_view(user)
            if not shelf_ids:
                return []
            
            # iteralte all shelves the user can view
            rules: List[Any] = []
            for sid in shelf_ids:
                shelf_rules = await self._rule_repo.list_rules(
                    event_type="NoteCreated",
                    attached_entity_type="shelf",
                    attached_entity_id=sid,
                    enabled_only=True,
                )
                rules.extend(shelf_rules)
            # collection of all rules from all shelves
            return rules
        except Exception as exc:  # noqa: BLE001 -- best-effort
            self.log.warning(
                "failed to fetch default-fleeting rule for "
                f"user {user.user_id!r}: {exc}"
            )
            return []

    async def _shelfs_user_can_view(
        self,
        user: UserContextABC,
    ) -> List[str]:
        """Return ids of every shelf the user has admin/owner on.

        Uses the permission repo's lookup semantics:
        ``shelf#view@user:<user_id>``.  An empty list is fine --
        the caller treats it as "no default rule".
        """
        try:
            shelf_ids = await self._permission_repo.lookup(
                Relationship(
                    resource=ObjectRef(
                        object_type=ObjectTypeEnum.SHELF, object_id=UNDEFINED
                    ),
                    relation=ShelfRelationEnum.VIEW,
                    subject=SubjectRef(
                        object_type=ObjectTypeEnum.USER,
                        object_id=str(user.user_id),
                    ),
                )
            )
            return shelf_ids
        except Exception:  # noqa: BLE001 -- best-effort
            return []

    async def _populate_relation_fields(
        self,
        note: NoteEntity,
        note_id: str,
    ) -> NoteEntity:
        """Refresh directory_ids / tag_ids / shelf_ids from the repos.

        Args:
            note: entity to enrich; mutated in place.
            note_id: id used for the directory / tag lookups.

        Returns:
            NoteEntity: updated version (same object).
        """
        note.directory_ids = await self._directory_facade.get_parents_of(
            "note", note_id, "directory",
        )
        note.tag_ids = await self._tag_repo.list_tags_of("note", note_id)

        # TODO: actually resolve the wohle hierarchy to get the shelves.
        # for now, we dont fetch any sehlves
        shelf_ids: set[str] = set()
        note.sehlf_ids = list(shelf_ids)
        return note

    # ---- insert / update ---------------------------------------------

    async def insert(self, note: NoteEntity, user: UserContextABC):
        """Insert a note; resolves directory ids from explicit dirs, a shelf anchor, or the user's default-fleeting rule.

        Falls back to the user's default-fleeting rule when no
        explicit ``directory_ids`` are given.  Raises
        ``ValueError`` if neither an explicit list nor a
        default-fleeting rule is available.
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

        # Resolve directory ids: explicit list wins; otherwise
        # fall back to the default-fleeting rule scoped to the
        # given shelf anchor, or any shelf the user can view.
        explicit_dirs: List[str] = []
        if note.directory_ids is not UNDEFINED and note.directory_ids:
            explicit_dirs = [str(d) for d in note.directory_ids if d]

        shelf_ids = note.shelf_ids if note.shelf_ids is not UNDEFINED else None
        shelf_anchor = str(next((v for v in (shelf_ids or []) if v), ""))
        if explicit_dirs:
            resolved_dirs = await self._resolve_directory_ids(
                explicit_dirs, user, shelf_id=shelf_anchor,
            )
        else:
            resolved_dirs = await self._resolve_directory_ids(
                None, user, shelf_id=shelf_anchor,
            )

        # assign the resolved directory ids. reset note.directory_ids and
        # repopulate it to ensure consistency if one call would fail.
        note.directory_ids = []
        for directory_id in resolved_dirs:
            await self._directory_facade.add_child_to(
                "directory", str(directory_id), "note", str(note_id),
            )
            note.directory_ids.append(directory_id)

        # If the caller gave us a shelf but no explicit directory_ids,
        # the directory resolved by a default-fleeting rule

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

        # Read directory_ids and tag_ids back from their dedicated repos
        # so the returned entity matches the persisted state.
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
            set=_keep_only_content(note),
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
            await self._directory_facade.set_parents_of(
                "note",
                unwrap_undefined(note.note_id),
                "directory",
                directory_ids,
            )

        # we dont update shelfs. shelfs are only used
        # for default-fleeting rule resolution

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
        ``shelf_ids`` is refreshed from the shelf repo via the
        note's parent directories.
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
        *,
        filter_: Optional[NoteSearchFilter] = None,
    ) -> List[NoteEntity]:
        """Run a strategy (date bounds in SQL, include/exclude in Python)."""
        filter_obj = filter_ or NoteSearchFilter.empty()
        common_init_parameters: Dict[str, Any] = {
            "db": self._db,
            "query": query,
            "limit": pagination.limit,
            "offset": pagination.offset,
            "user_context": ctx,
            "note_permissions": self._permission_repo,
            "filter_": filter_obj,
        }
        strategy = self._strategy_for(search_type, common_init_parameters)

        note_entities = await strategy.search()
        if not filter_obj.is_empty():
            note_entities = await self._apply_search_filter(
                note_entities, filter_obj,
            )
        for note in note_entities:
            if note.permissions is UNDEFINED:
                note.permissions = []
            await self._populate_relation_fields(note, str(note.note_id))
        return note_entities

    async def _apply_search_filter(
        self,
        notes: List[NoteEntity],
        filter_: NoteSearchFilter,
    ) -> List[NoteEntity]:
        """Apply include/exclude halves in Python (date bounds handled by strategy)."""
        if not notes:
            return notes

        include_dirs = set(filter_.include_directory_ids)
        exclude_dirs = set(filter_.exclude_directory_ids)
        include_shelves = set(filter_.include_shelf_ids)
        exclude_shelves = set(filter_.exclude_shelf_ids)
        include_tags = set(filter_.include_tag_ids)
        exclude_tags = set(filter_.exclude_tag_ids)

        shelf_to_books: Dict[str, set[str]] = {}
        if include_shelves or exclude_shelves:
            for sid in (include_shelves | exclude_shelves):
                shelf_to_books[sid] = set(
                    await self._shelf_repo.get_books_of(sid)
                )

        kept: List[NoteEntity] = []
        for note in notes:
            note_id = str(note.note_id) if note.note_id else ""
            if not note_id:
                continue

            await self._populate_relation_fields(note, note_id)

            dir_ids = set(note.directory_ids or [])
            tag_ids = set(note.tag_ids or [])

            if include_dirs and not (dir_ids & include_dirs):
                continue
            if exclude_dirs and (dir_ids & exclude_dirs):
                continue

            if include_tags and not (tag_ids & include_tags):
                continue
            if exclude_tags and (tag_ids & exclude_tags):
                continue

            if include_shelves or exclude_shelves:
                # The note sits on a shelf when any parent dir is in
                # that shelf's book set.
                note_on_shelves: set[str] = set()
                for sid, books in shelf_to_books.items():
                    if dir_ids & books:
                        note_on_shelves.add(sid)
                if include_shelves and not (note_on_shelves & include_shelves):
                    continue
                if exclude_shelves and (note_on_shelves & exclude_shelves):
                    continue

            kept.append(note)
        return kept

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


#: Attributes backed by columns on the ``note`` SQL table.
#: Everything else (relation fields, embeddings, permissions) lives in a
#: side table or is computed at read time, so it must be cleared before
#: passing the entity to ``update``.
_CONTENT_FIELDS: frozenset[str] = frozenset({
    "note_id",
    "title",
    "content",
    "updated_at",
    "author_id",
})


def _keep_only_content(note: NoteEntity) -> NoteEntity:
    """Return ``note`` with every non-content field cleared.

    Walks :data:`dataclasses.fields` so any new field added to
    :class:`NoteEntity` is dropped automatically.
    """
    cleared = {
        name: UNDEFINED
        for name in (f.name for f in fields(NoteEntity))
        if name not in _CONTENT_FIELDS
    }
    return replace(note, **cleared)
