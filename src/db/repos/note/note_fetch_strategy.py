"""SQL fetch strategies for the combined note + side-table reads.

Every call to :meth:`CombinedNotePostgresRepo.select_by_id` /
:meth:`CombinedNotePostgresRepo.select_by_ids` ends up running
one of the eight SQL shapes owned by a subvariant of
:class:`NoteFetchStrategyABC`.  Each subvariant corresponds to a
different combination of the three opt-in side-table JOINs
(directory / tag / attachment), so the basic "row only" read
stays cheap when the caller doesn't ask for enrichment.

The strategy objects are stateless; they only carry the SQL
constants and a record-to-entity converter.  The Postgres repo
constructs one strategy per call based on the resolved
:class:`~src.api.services.note_service.NoteIncludeOptions`.

Tables touched:

* ``note.content`` -- the note row (every shape).
* ``note.directory_note`` -- when ``include_directory_ids`` is set.
* ``note.note_tag`` -- when ``include_tag_ids`` is set.
* ``note.attachment_note_link`` -- when ``include_attachment_ids``
  is set (yields the attachment key, which is the public
  ``attachment_id``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from src.api.other.undefined import UNDEFINED
from src.api.services.note_service import (
    NoteIncludeOptions,
    resolve_include_options,
)
from src.db.database import Database
from src.db.entities import NoteEntity
from src.utils import all_valid_items


class NoteFetchStrategyABC(ABC):
    """One subvariant of the combined note fetch SQL.

    Each subvariant owns exactly two SQL statements (one per
    public method on :class:`~src.api.repos.combined_note_repo.CombinedNoteRepoABC`)
    and the matching record-to-entity conversion.

    Implementations:
    * :class:`RowOnlyNoteFetchStrategy`
    * :class:`WithDirectoriesNoteFetchStrategy`
    * :class:`WithTagsNoteFetchStrategy`
    * :class:`WithAttachmentsNoteFetchStrategy`
    * :class:`WithDirectoriesAndTagsNoteFetchStrategy`
    * :class:`WithDirectoriesAndAttachmentsNoteFetchStrategy`
    * :class:`WithTagsAndAttachmentsNoteFetchStrategy`
    * :class:`WithAllRelationsNoteFetchStrategy`
    """

    @abstractmethod
    async def fetch_one(
        self,
        db: Database,
        note_id: str,
    ) -> Optional[NoteEntity]:
        """Fetch a single note + this strategy's side-table JOINs.

        Args:
            db: live :class:`Database` connection.
            note_id: id of the note to load.

        Returns:
            Optional[NoteEntity]: the resolved note, or `None`
            when no row matches `note_id`.
        """
        ...

    @abstractmethod
    async def fetch_many(
        self,
        db: Database,
        note_ids: List[str],
    ) -> List[NoteEntity]:
        """Bulk-fetch + this strategy's side-table JOINs.

        Args:
            db: live :class:`Database` connection.
            note_ids: ids to resolve; order is preserved in the
                result list.

        Raises:
            ValueError: when any id in `note_ids` is missing.

        Returns:
            List[NoteEntity]: resolved notes in `note_ids` order.
        """
        ...


class _PostgresNoteFetchStrategyBase(NoteFetchStrategyABC):
    """Plumbing shared by every SQL-backed strategy.

    Subclasses provide three things:

    * :attr:`_ONE_SQL` -- ``SELECT ... WHERE id = $1``
    * :attr:`_MANY_SQL` -- ``SELECT ... WHERE id = ANY($1::text[])``
    * :meth:`_decorate` -- build a fresh dict from an
      `asyncpg.Record`, renaming ``id`` to ``note_id`` and
      populating every side-table field the strategy owns.

    The base handles the fetch + the missing-id check on the
    bulk path; subclasses never see the connection or the list of
    requested ids.

    Note:
        `asyncpg.Record` is read-only -- it does not implement
        ``__setitem__`` or ``__delitem__``.  `_decorate` therefore
        returns a brand-new `dict` rather than mutating the
        record in place.
    """

    _ONE_SQL: str
    _MANY_SQL: str

    async def fetch_one(
        self,
        db: Database,
        note_id: str,
    ) -> Optional[NoteEntity]:
        record = await db.fetchrow(self._ONE_SQL, note_id)
        if not record:
            return None
        return self._build_entity(record)

    async def fetch_many(
        self,
        db: Database,
        note_ids: List[str],
    ) -> List[NoteEntity]:
        records = await db.fetch(self._MANY_SQL, note_ids)
        return self._build_entities(records, note_ids)

    @abstractmethod
    def _decorate(self, record: asyncpg.Record) -> Dict[str, Any]:
        """Build the entity kwargs from a raw row.

        Args:
            record: row returned by `Database.fetchrow` /
                `Database.fetch`.  Read-only.

        Returns:
            Dict[str, Any]: kwargs for `NoteEntity(**...)`.  Must
            rename ``id`` -> ``note_id`` and populate every
            side-table field this strategy owns.  The
            `embeddings` / `permissions` defaults are filled in
            by :meth:`_build_entity`, not by `_decorate`.
        """
        ...

    def _build_entity(self, record: asyncpg.Record) -> NoteEntity:
        kwargs = self._decorate(record)
        return NoteEntity(
            **kwargs, embeddings=[], permissions=UNDEFINED,
        )

    def _build_entities(
        self,
        records: List[asyncpg.Record],
        note_ids: List[str],
    ) -> List[NoteEntity]:
        entities = [self._build_entity(r) for r in records]
        by_id = {str(entity.note_id): entity for entity in entities}
        missing = [nid for nid in note_ids if nid not in by_id]
        if missing:
            raise ValueError(
                f"Notes with ids {missing!r} could not be resolved"
            )
        return [by_id[nid] for nid in note_ids]


# ---- row only ----------------------------------------------------------


class RowOnlyNoteFetchStrategy(_PostgresNoteFetchStrategyBase):
    """Basic content / title / author fetch -- no side-table JOINs."""

    _ONE_SQL = """
        SELECT id, title, content, updated_at, author_id
        FROM note.content
        WHERE id = $1
    """

    _MANY_SQL = """
        SELECT id, title, content, updated_at, author_id
        FROM note.content
        WHERE id = ANY($1::text[])
    """

    def _decorate(self, record: asyncpg.Record) -> Dict[str, Any]:
        return {
            "note_id": record["id"],
            "title": record["title"],
            "content": record["content"],
            "updated_at": record["updated_at"],
            "author_id": record["author_id"],
        }


# ---- single enrichment -------------------------------------------------


class WithDirectoriesNoteFetchStrategy(_PostgresNoteFetchStrategyBase):
    """Adds `note.directory_ids` via LEFT JOIN on `note.directory_note`."""

    _DIRS_SELECT = """
        COALESCE(
            array_agg(dn.directory_id)
            FILTER (WHERE dn.directory_id IS NOT NULL),
            '{}'::text[]
        ) AS directory_ids
    """

    _ONE_SQL = f"""
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               {_DIRS_SELECT}
        FROM note.content n
        LEFT JOIN note.directory_note dn
            ON dn.note_id = n.id
        WHERE n.id = $1
        GROUP BY n.id
    """

    _MANY_SQL = f"""
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               {_DIRS_SELECT}
        FROM note.content n
        LEFT JOIN note.directory_note dn
            ON dn.note_id = n.id
        WHERE n.id = ANY($1::text[])
        GROUP BY n.id
    """

    def _decorate(self, record: asyncpg.Record) -> Dict[str, Any]:
        return {
            "note_id": record["id"],
            "title": record["title"],
            "content": record["content"],
            "updated_at": record["updated_at"],
            "author_id": record["author_id"],
            "directory_ids": all_valid_items(
                record, "directory_ids", cast_to=str,
            ),
        }


class WithTagsNoteFetchStrategy(_PostgresNoteFetchStrategyBase):
    """Adds `note.tag_ids` via LEFT JOIN on `note.note_tag`."""

    _TAGS_SELECT = """
        COALESCE(
            array_agg(nt.tag_id)
            FILTER (WHERE nt.tag_id IS NOT NULL),
            '{}'::text[]
        ) AS tag_ids
    """

    _ONE_SQL = f"""
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               {_TAGS_SELECT}
        FROM note.content n
        LEFT JOIN note.note_tag nt ON nt.note_id = n.id
        WHERE n.id = $1
        GROUP BY n.id
    """

    _MANY_SQL = f"""
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               {_TAGS_SELECT}
        FROM note.content n
        LEFT JOIN note.note_tag nt ON nt.note_id = n.id
        WHERE n.id = ANY($1::text[])
        GROUP BY n.id
    """

    def _decorate(self, record: asyncpg.Record) -> Dict[str, Any]:
        return {
            "note_id": record["id"],
            "title": record["title"],
            "content": record["content"],
            "updated_at": record["updated_at"],
            "author_id": record["author_id"],
            "tag_ids": all_valid_items(
                record, "tag_ids", cast_to=str,
            ),
        }


class WithAttachmentsNoteFetchStrategy(_PostgresNoteFetchStrategyBase):
    """Adds `note.attachment_ids` via LEFT JOIN on `note.attachment_note_link`."""

    _ATTACHMENTS_SELECT = """
        COALESCE(
            array_agg(anl.attachment_key)
            FILTER (WHERE anl.attachment_key IS NOT NULL),
            '{}'::text[]
        ) AS attachment_ids
    """

    _ONE_SQL = f"""
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               {_ATTACHMENTS_SELECT}
        FROM note.content n
        LEFT JOIN note.attachment_note_link anl
            ON anl.note_id = n.id
        WHERE n.id = $1
        GROUP BY n.id
    """

    _MANY_SQL = f"""
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               {_ATTACHMENTS_SELECT}
        FROM note.content n
        LEFT JOIN note.attachment_note_link anl
            ON anl.note_id = n.id
        WHERE n.id = ANY($1::text[])
        GROUP BY n.id
    """

    def _decorate(self, record: asyncpg.Record) -> Dict[str, Any]:
        return {
            "note_id": record["id"],
            "title": record["title"],
            "content": record["content"],
            "updated_at": record["updated_at"],
            "author_id": record["author_id"],
            "attachment_ids": all_valid_items(
                record, "attachment_ids", cast_to=str,
            ),
        }


# ---- two-way enrichment ------------------------------------------------


class WithDirectoriesAndTagsNoteFetchStrategy(_PostgresNoteFetchStrategyBase):
    """Adds `directory_ids` + `tag_ids` in one round-trip."""

    _ONE_SQL = """
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               COALESCE(
                   array_agg(DISTINCT dn.directory_id)
                   FILTER (WHERE dn.directory_id IS NOT NULL),
                   '{}'::text[]
               ) AS directory_ids,
               COALESCE(
                   array_agg(DISTINCT nt.tag_id)
                   FILTER (WHERE nt.tag_id IS NOT NULL),
                   '{}'::text[]
               ) AS tag_ids
        FROM note.content n
        LEFT JOIN note.directory_note dn
            ON dn.note_id = n.id
        LEFT JOIN note.note_tag nt ON nt.note_id = n.id
        WHERE n.id = $1
        GROUP BY n.id
    """

    _MANY_SQL = """
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               COALESCE(
                   array_agg(DISTINCT dn.directory_id)
                   FILTER (WHERE dn.directory_id IS NOT NULL),
                   '{}'::text[]
               ) AS directory_ids,
               COALESCE(
                   array_agg(DISTINCT nt.tag_id)
                   FILTER (WHERE nt.tag_id IS NOT NULL),
                   '{}'::text[]
               ) AS tag_ids
        FROM note.content n
        LEFT JOIN note.directory_note dn
            ON dn.note_id = n.id
        LEFT JOIN note.note_tag nt ON nt.note_id = n.id
        WHERE n.id = ANY($1::text[])
        GROUP BY n.id
    """

    def _decorate(self, record: asyncpg.Record) -> Dict[str, Any]:
        return {
            "note_id": record["id"],
            "title": record["title"],
            "content": record["content"],
            "updated_at": record["updated_at"],
            "author_id": record["author_id"],
            "directory_ids": all_valid_items(
                record, "directory_ids", cast_to=str,
            ),
            "tag_ids": all_valid_items(
                record, "tag_ids", cast_to=str,
            ),
        }


class WithDirectoriesAndAttachmentsNoteFetchStrategy(
    _PostgresNoteFetchStrategyBase,
):
    """Adds `directory_ids` + `attachment_ids` in one round-trip."""

    _ONE_SQL = """
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               COALESCE(
                   array_agg(DISTINCT dn.directory_id)
                   FILTER (WHERE dn.directory_id IS NOT NULL),
                   '{}'::text[]
               ) AS directory_ids,
               COALESCE(
                   array_agg(DISTINCT anl.attachment_key)
                   FILTER (WHERE anl.attachment_key IS NOT NULL),
                   '{}'::text[]
               ) AS attachment_ids
        FROM note.content n
        LEFT JOIN note.directory_note dn
            ON dn.note_id = n.id
        LEFT JOIN note.attachment_note_link anl
            ON anl.note_id = n.id
        WHERE n.id = $1
        GROUP BY n.id
    """

    _MANY_SQL = """
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               COALESCE(
                   array_agg(DISTINCT dn.directory_id)
                   FILTER (WHERE dn.directory_id IS NOT NULL),
                   '{}'::text[]
               ) AS directory_ids,
               COALESCE(
                   array_agg(DISTINCT anl.attachment_key)
                   FILTER (WHERE anl.attachment_key IS NOT NULL),
                   '{}'::text[]
               ) AS attachment_ids
        FROM note.content n
        LEFT JOIN note.directory_note dn
            ON dn.note_id = n.id
        LEFT JOIN note.attachment_note_link anl
            ON anl.note_id = n.id
        WHERE n.id = ANY($1::text[])
        GROUP BY n.id
    """

    def _decorate(self, record: asyncpg.Record) -> Dict[str, Any]:
        return {
            "note_id": record["id"],
            "title": record["title"],
            "content": record["content"],
            "updated_at": record["updated_at"],
            "author_id": record["author_id"],
            "directory_ids": all_valid_items(
                record, "directory_ids", cast_to=str,
            ),
            "attachment_ids": all_valid_items(
                record, "attachment_ids", cast_to=str,
            ),
        }


class WithTagsAndAttachmentsNoteFetchStrategy(_PostgresNoteFetchStrategyBase):
    """Adds `tag_ids` + `attachment_ids` in one round-trip."""

    _ONE_SQL = """
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               COALESCE(
                   array_agg(DISTINCT nt.tag_id)
                   FILTER (WHERE nt.tag_id IS NOT NULL),
                   '{}'::text[]
               ) AS tag_ids,
               COALESCE(
                   array_agg(DISTINCT anl.attachment_key)
                   FILTER (WHERE anl.attachment_key IS NOT NULL),
                   '{}'::text[]
               ) AS attachment_ids
        FROM note.content n
        LEFT JOIN note.note_tag nt ON nt.note_id = n.id
        LEFT JOIN note.attachment_note_link anl
            ON anl.note_id = n.id
        WHERE n.id = $1
        GROUP BY n.id
    """

    _MANY_SQL = """
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               COALESCE(
                   array_agg(DISTINCT nt.tag_id)
                   FILTER (WHERE nt.tag_id IS NOT NULL),
                   '{}'::text[]
               ) AS tag_ids,
               COALESCE(
                   array_agg(DISTINCT anl.attachment_key)
                   FILTER (WHERE anl.attachment_key IS NOT NULL),
                   '{}'::text[]
               ) AS attachment_ids
        FROM note.content n
        LEFT JOIN note.note_tag nt ON nt.note_id = n.id
        LEFT JOIN note.attachment_note_link anl
            ON anl.note_id = n.id
        WHERE n.id = ANY($1::text[])
        GROUP BY n.id
    """

    def _decorate(self, record: asyncpg.Record) -> Dict[str, Any]:
        return {
            "note_id": record["id"],
            "title": record["title"],
            "content": record["content"],
            "updated_at": record["updated_at"],
            "author_id": record["author_id"],
            "tag_ids": all_valid_items(
                record, "tag_ids", cast_to=str,
            ),
            "attachment_ids": all_valid_items(
                record, "attachment_ids", cast_to=str,
            ),
        }


# ---- three-way enrichment ---------------------------------------------


class WithAllRelationsNoteFetchStrategy(_PostgresNoteFetchStrategyBase):
    """Adds `directory_ids` + `tag_ids` + `attachment_ids` in one round-trip."""

    _ONE_SQL = """
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               COALESCE(
                   array_agg(DISTINCT dn.directory_id)
                   FILTER (WHERE dn.directory_id IS NOT NULL),
                   '{}'::text[]
               ) AS directory_ids,
               COALESCE(
                   array_agg(DISTINCT nt.tag_id)
                   FILTER (WHERE nt.tag_id IS NOT NULL),
                   '{}'::text[]
               ) AS tag_ids,
               COALESCE(
                   array_agg(DISTINCT anl.attachment_key)
                   FILTER (WHERE anl.attachment_key IS NOT NULL),
                   '{}'::text[]
               ) AS attachment_ids
        FROM note.content n
        LEFT JOIN note.directory_note dn
            ON dn.note_id = n.id
        LEFT JOIN note.note_tag nt ON nt.note_id = n.id
        LEFT JOIN note.attachment_note_link anl
            ON anl.note_id = n.id
        WHERE n.id = $1
        GROUP BY n.id
    """

    _MANY_SQL = """
        SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
               COALESCE(
                   array_agg(DISTINCT dn.directory_id)
                   FILTER (WHERE dn.directory_id IS NOT NULL),
                   '{}'::text[]
               ) AS directory_ids,
               COALESCE(
                   array_agg(DISTINCT nt.tag_id)
                   FILTER (WHERE nt.tag_id IS NOT NULL),
                   '{}'::text[]
               ) AS tag_ids,
               COALESCE(
                   array_agg(DISTINCT anl.attachment_key)
                   FILTER (WHERE anl.attachment_key IS NOT NULL),
                   '{}'::text[]
               ) AS attachment_ids
        FROM note.content n
        LEFT JOIN note.directory_note dn
            ON dn.note_id = n.id
        LEFT JOIN note.note_tag nt ON nt.note_id = n.id
        LEFT JOIN note.attachment_note_link anl
            ON anl.note_id = n.id
        WHERE n.id = ANY($1::text[])
        GROUP BY n.id
    """

    def _decorate(self, record: asyncpg.Record) -> Dict[str, Any]:
        return {
            "note_id": record["id"],
            "title": record["title"],
            "content": record["content"],
            "updated_at": record["updated_at"],
            "author_id": record["author_id"],
            "directory_ids": all_valid_items(
                record, "directory_ids", cast_to=str,
            ),
            "tag_ids": all_valid_items(
                record, "tag_ids", cast_to=str,
            ),
            "attachment_ids": all_valid_items(
                record, "attachment_ids", cast_to=str,
            ),
        }


# ---- module-level singletons + dispatcher ------------------------------


ROW_ONLY_STRATEGY: NoteFetchStrategyABC = RowOnlyNoteFetchStrategy()
WITH_DIRECTORIES_STRATEGY: NoteFetchStrategyABC = (
    WithDirectoriesNoteFetchStrategy()
)
WITH_TAGS_STRATEGY: NoteFetchStrategyABC = WithTagsNoteFetchStrategy()
WITH_ATTACHMENTS_STRATEGY: NoteFetchStrategyABC = (
    WithAttachmentsNoteFetchStrategy()
)
WITH_DIRECTORIES_AND_TAGS_STRATEGY: NoteFetchStrategyABC = (
    WithDirectoriesAndTagsNoteFetchStrategy()
)
WITH_DIRECTORIES_AND_ATTACHMENTS_STRATEGY: NoteFetchStrategyABC = (
    WithDirectoriesAndAttachmentsNoteFetchStrategy()
)
WITH_TAGS_AND_ATTACHMENTS_STRATEGY: NoteFetchStrategyABC = (
    WithTagsAndAttachmentsNoteFetchStrategy()
)
WITH_ALL_RELATIONS_STRATEGY: NoteFetchStrategyABC = (
    WithAllRelationsNoteFetchStrategy()
)


_STRATEGY_BY_FLAGS: Dict[Tuple[bool, bool, bool], NoteFetchStrategyABC] = {
    (False, False, False): ROW_ONLY_STRATEGY,
    (True, False, False): WITH_DIRECTORIES_STRATEGY,
    (False, True, False): WITH_TAGS_STRATEGY,
    (False, False, True): WITH_ATTACHMENTS_STRATEGY,
    (True, True, False): WITH_DIRECTORIES_AND_TAGS_STRATEGY,
    (True, False, True): WITH_DIRECTORIES_AND_ATTACHMENTS_STRATEGY,
    (False, True, True): WITH_TAGS_AND_ATTACHMENTS_STRATEGY,
    (True, True, True): WITH_ALL_RELATIONS_STRATEGY,
}


def strategy_for(
    include: Optional[NoteIncludeOptions],
) -> NoteFetchStrategyABC:
    """Pick the :class:`NoteFetchStrategyABC` for the given include flags.

    Args:
        include: caller-supplied options; `None` is treated as
            "row only".

    Returns:
        NoteFetchStrategyABC: the strategy matching the resolved
        `include_directory_ids` / `include_tag_ids` /
        `include_attachment_ids` triple.
    """
    opts = resolve_include_options(include)
    key = (
        bool(opts.get("include_directory_ids")),
        bool(opts.get("include_tag_ids")),
        bool(opts.get("include_attachment_ids")),
    )
    return _STRATEGY_BY_FLAGS[key]


__all__ = [
    "NoteFetchStrategyABC",
    "RowOnlyNoteFetchStrategy",
    "WithDirectoriesNoteFetchStrategy",
    "WithTagsNoteFetchStrategy",
    "WithAttachmentsNoteFetchStrategy",
    "WithDirectoriesAndTagsNoteFetchStrategy",
    "WithDirectoriesAndAttachmentsNoteFetchStrategy",
    "WithTagsAndAttachmentsNoteFetchStrategy",
    "WithAllRelationsNoteFetchStrategy",
    "ROW_ONLY_STRATEGY",
    "WITH_DIRECTORIES_STRATEGY",
    "WITH_TAGS_STRATEGY",
    "WITH_ATTACHMENTS_STRATEGY",
    "WITH_DIRECTORIES_AND_TAGS_STRATEGY",
    "WITH_DIRECTORIES_AND_ATTACHMENTS_STRATEGY",
    "WITH_TAGS_AND_ATTACHMENTS_STRATEGY",
    "WITH_ALL_RELATIONS_STRATEGY",
    "strategy_for",
]