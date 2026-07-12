"""Postgres implementation of :class:`PostgresDirectoryRepoABC`.

Every SQL statement lives here.  The class deliberately does **not**
consult SpiceDB; caller layers (notably the directory facade) wire
in the permission repo when SpiceDB visibility matters.

Tables touched:

* ``note.directory`` -- the directory row itself.
* ``note.directory_subdirectory`` -- parent / child graph between
  directories (the directory tree).
* ``note.directory_note`` -- directory / note bindings.
* ``note.directory_tag`` -- tag association for directories.

The directory tree and the note bindings are kept in two
single-purpose tables (introduced in
:mod:`src.db.migrations.20260711-directory-hierarchy`) so every
row unambiguously describes one relationship; the previous XOR
table is gone.

Each public fetch method is fully scoped: a dedicated SQL statement
that targets exactly the row + the optional joins the caller asked
for.  No string-building over a base query.
"""

from __future__ import annotations

from typing import List, Optional

import asyncpg  # type: ignore[import]

from src.api.directory_service import DirectoryIncludeOptions
from src.api.directory_repo import DirectoryRepoABC
from src.api.undefined import (
    UNDEFINED,
    UndefinedNoneOr,
    UndefinedOr,
    is_undefined,
    unwrap_undefined_or,
)
from src.db.entities.directory.directory import DirectoryEntity
from src.db.table import TableABC


class PostgresDirectoryRepo(DirectoryRepoABC):
    """Postgres implementation of the directory-repository contract.

    Args:
        directory_table: ``TableABC`` over ``note.directory``.
        subdirectory_table: ``TableABC`` over
            ``note.directory_subdirectory`` -- the parent / child
            graph between directories.
        directory_note_table: ``TableABC`` over
            ``note.directory_note`` -- the directory / note
            bindings.
        tags_table: ``TableABC`` over ``note.directory_tag``.  The
            directory-repo surface exposes tag CRUD via
            :meth:`tag_ids_of_directory` / :meth:`replace_directory_tags`,
            so the table is required.
    """

    _DIRECTORY_COLUMNS = (
        "id, slug, display_name, description, image_url, readme_note_id"
    )

    def __init__(
        self,
        directory_table: TableABC,
        subdirectory_table: TableABC,
        directory_note_table: TableABC,
        tags_table: TableABC,
    ) -> None:
        self._directory_table = directory_table
        self._subdirectory_table = subdirectory_table
        self._directory_note_table = directory_note_table
        self._tags_table = tags_table

    @property
    def directory_table(self) -> TableABC:
        """Return the ``note.directory`` :class:`TableABC`."""
        return self._directory_table

    @property
    def subdirectory_table(self) -> TableABC:
        """Return the ``note.directory_subdirectory`` :class:`TableABC`."""
        return self._subdirectory_table

    @property
    def directory_note_table(self) -> TableABC:
        """Return the ``note.directory_note`` :class:`TableABC`."""
        return self._directory_note_table

    @property
    def tags_table(self) -> TableABC:
        """Return the ``note.directory_tag`` :class:`TableABC`."""
        return self._tags_table

    # ---- inserts / updates / deletes ----------------------------------

    async def insert_directory(
        self,
        *,
        slug: str,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
    ) -> DirectoryEntity:
        """Insert a single row and return the persisted entity."""
        rows = await self._directory_table.insert(
            {
                "slug": slug,
                "display_name": self._resolve_undefined_none(display_name),
                "description": self._resolve_undefined_none(description),
                "image_url": self._resolve_undefined_none(image_url),
                "readme_note_id": self._resolve_undefined_none(readme_note_id),
            },
            returning=self._DIRECTORY_COLUMNS,
        )
        if not rows:
            raise RuntimeError("Failed to create directory")
        return self._row_to_entity(rows[0])

    async def fetch_directory(
        self,
        id: str,
        *,
        include: Optional[DirectoryIncludeOptions] = None,
    ) -> Optional[DirectoryEntity]:
        """Fetch one directory row, optionally with hierarchy joins.

        One dedicated SQL per combination of ``include_*`` flags --
        no string-built queries.  The shapes are:

        * nothing requested (or all ``False``): cheap row read.
        * single list: row + ``LEFT JOIN ... GROUP BY`` producing one
          of ``parent_directory_ids``, ``child_directory_ids`` or
          ``child_note_ids``.
        * parents + both child lists: row + two ``LEFT JOIN``s in one
          ``GROUP BY``.

        Counts of direct child directories / notes are derived from
        ``len(directory.child_directory_ids)`` and
        ``len(directory.child_note_ids)`` when those lists were
        fetched.  No aggregate ``COUNT(*)`` SQL is issued; the
        entity does not carry a count field.
        """
        want_parents = bool(include and include.get("include_parents"))
        want_child_dirs = bool(include and include.get("include_child_dirs"))
        want_child_notes = bool(include and include.get("include_child_notes"))

        if not any((want_parents, want_child_dirs, want_child_notes)):
            record = await self._directory_table.fetch_by_id(str(id))
            if not record:
                return None
            return self._row_to_entity(record)

        # Pick a dedicated SQL per combination of list flags.  No
        # count-only SQL is needed -- callers derive counts from the
        # list lengths.
        if want_parents and not (want_child_dirs or want_child_notes):
            return await self._fetch_directory_with_parents(str(id))
        if want_child_dirs and want_child_notes and not want_parents:
            return await self._fetch_directory_with_children(str(id))
        if (
            want_child_dirs
            and not want_child_notes
            and not want_parents
        ):
            return await self._fetch_directory_with_child_directories(
                str(id)
            )
        if (
            want_child_notes
            and not want_child_dirs
            and not want_parents
        ):
            return await self._fetch_directory_with_child_notes(str(id))

        # The all-three combination collapses into the dedicated
        # `parents + both children` SQL.
        return await self._fetch_directory_full(
            str(id),
            include_parents=want_parents,
            include_child_dirs=want_child_dirs,
            include_child_notes=want_child_notes,
        )

    async def _fetch_directory_with_parents(
        self, id: str,
    ) -> Optional[DirectoryEntity]:
        """Row + full parent directory id list."""
        records = await self._directory_table.fetch(
            """
            SELECT d.id, d.slug, d.display_name, d.description,
                   d.image_url, d.readme_note_id,
                   COALESCE(
                       array_agg(s.directory_id)
                       FILTER (WHERE s.directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS parent_directory_ids
            FROM note.directory d
            LEFT JOIN note.directory_subdirectory s
                ON s.child_directory_id = d.id
            WHERE d.id = $1
            GROUP BY d.id, d.slug, d.display_name, d.description,
                     d.image_url, d.readme_note_id
            """,
            id,
        )
        if not records:
            return None
        row = records[0]
        entity = self._row_to_entity(row)
        entity.parent_directory_ids = [
            str(v) for v in (row.get("parent_directory_ids") or [])
            if v is not None
        ]
        return entity

    async def _fetch_directory_with_child_directories(
        self, id: str,
    ) -> Optional[DirectoryEntity]:
        """Row + direct child directory ids."""
        records = await self._directory_table.fetch(
            """
            SELECT d.id, d.slug, d.display_name, d.description,
                   d.image_url, d.readme_note_id,
                   COALESCE(
                       array_agg(s.child_directory_id)
                       FILTER (WHERE s.child_directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS child_directory_ids
            FROM note.directory d
            LEFT JOIN note.directory_subdirectory s
                ON s.directory_id = d.id
            WHERE d.id = $1
            GROUP BY d.id, d.slug, d.display_name, d.description,
                     d.image_url, d.readme_note_id
            """,
            id,
        )
        if not records:
            return None
        row = records[0]
        entity = self._row_to_entity(row)
        entity.child_directory_ids = [
            str(v) for v in (row.get("child_directory_ids") or [])
            if v is not None
        ]
        return entity

    async def _fetch_directory_with_child_notes(
        self, id: str,
    ) -> Optional[DirectoryEntity]:
        """Row + direct child note ids."""
        records = await self._directory_table.fetch(
            """
            SELECT d.id, d.slug, d.display_name, d.description,
                   d.image_url, d.readme_note_id,
                   COALESCE(
                       array_agg(n.note_id)
                       FILTER (WHERE n.note_id IS NOT NULL),
                       '{}'::text[]
                   ) AS child_note_ids
            FROM note.directory d
            LEFT JOIN note.directory_note n
                ON n.directory_id = d.id
            WHERE d.id = $1
            GROUP BY d.id, d.slug, d.display_name, d.description,
                     d.image_url, d.readme_note_id
            """,
            id,
        )
        if not records:
            return None
        row = records[0]
        entity = self._row_to_entity(row)
        entity.child_note_ids = [
            str(v) for v in (row.get("child_note_ids") or [])
            if v is not None
        ]
        return entity

    async def _fetch_directory_with_children(
        self, id: str,
    ) -> Optional[DirectoryEntity]:
        """Row + child directory ids + child note ids (one JOIN each)."""
        records = await self._directory_table.fetch(
            """
            SELECT d.id, d.slug, d.display_name, d.description,
                   d.image_url, d.readme_note_id,
                   COALESCE(
                       array_agg(s.child_directory_id)
                       FILTER (WHERE s.child_directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS child_directory_ids,
                   COALESCE(
                       array_agg(n.note_id)
                       FILTER (WHERE n.note_id IS NOT NULL),
                       '{}'::text[]
                   ) AS child_note_ids
            FROM note.directory d
            LEFT JOIN note.directory_subdirectory s
                ON s.directory_id = d.id
            LEFT JOIN note.directory_note n
                ON n.directory_id = d.id
            WHERE d.id = $1
            GROUP BY d.id, d.slug, d.display_name, d.description,
                     d.image_url, d.readme_note_id
            """,
            id,
        )
        if not records:
            return None
        row = records[0]
        entity = self._row_to_entity(row)
        entity.child_directory_ids = [
            str(v) for v in (row.get("child_directory_ids") or [])
            if v is not None
        ]
        entity.child_note_ids = [
            str(v) for v in (row.get("child_note_ids") or [])
            if v is not None
        ]
        return entity

    async def _fetch_directory_full(
        self,
        id: str,
        *,
        include_parents: bool,
        include_child_dirs: bool,
        include_child_notes: bool,
    ) -> Optional[DirectoryEntity]:
        """Row + every list include via a 3-way LEFT JOIN + GROUP BY.

        Parents + child directories both come from
        ``note.directory_subdirectory`` (joined twice with
        different ``ON`` conditions); child notes come from
        ``note.directory_note``.  The three ``array_agg`` calls
        with ``FILTER`` collapse the cross-product of rows into
        the three id lists.
        """
        records = await self._directory_table.fetch(
            """
            SELECT d.id, d.slug, d.display_name, d.description,
                   d.image_url, d.readme_note_id,
                   COALESCE(
                       array_agg(parent_s.directory_id)
                       FILTER (WHERE parent_s.directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS parent_directory_ids,
                   COALESCE(
                       array_agg(child_s.child_directory_id)
                       FILTER (WHERE child_s.child_directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS child_directory_ids,
                   COALESCE(
                       array_agg(child_n.note_id)
                       FILTER (WHERE child_n.note_id IS NOT NULL),
                       '{}'::text[]
                   ) AS child_note_ids
            FROM note.directory d
            LEFT JOIN note.directory_subdirectory parent_s
                ON parent_s.child_directory_id = d.id
            LEFT JOIN note.directory_subdirectory child_s
                ON child_s.directory_id = d.id
            LEFT JOIN note.directory_note child_n
                ON child_n.directory_id = d.id
            WHERE d.id = $1
            GROUP BY d.id, d.slug, d.display_name, d.description,
                     d.image_url, d.readme_note_id
            """,
            id,
        )
        if not records:
            return None
        row = records[0]
        entity = self._row_to_entity(row)
        if include_parents:
            entity.parent_directory_ids = [
                str(v) for v in (row.get("parent_directory_ids") or [])
                if v is not None
            ]
        if include_child_dirs:
            entity.child_directory_ids = [
                str(v) for v in (row.get("child_directory_ids") or [])
                if v is not None
            ]
        if include_child_notes:
            entity.child_note_ids = [
                str(v) for v in (row.get("child_note_ids") or [])
                if v is not None
            ]
        return entity

    async def fetch_directories_by_ids(
        self,
        ids: List[str],
    ) -> List[DirectoryEntity]:
        """Fetch multiple directory rows in one query."""
        if not ids:
            return []
        records = await self._directory_table.fetch(
            f"""
            SELECT {self._DIRECTORY_COLUMNS}
            FROM {self.directory_table.name}
            WHERE id = ANY($1)
            """,
            ids,
        )
        return [self._row_to_entity(r) for r in records or []]

    async def update_directory(
        self,
        id: str,
        *,
        slug: UndefinedOr[str] = UNDEFINED,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
    ) -> Optional[DirectoryEntity]:
        """Apply a partial update honouring UNDEFINED / None semantics."""
        if is_undefined(id):
            raise ValueError("Directory ID is required for update")

        sets: dict[str, object] = {}
        if not is_undefined(slug):
            sets["slug"] = str(slug)
        if not is_undefined(display_name):
            sets["display_name"] = unwrap_undefined_or(display_name, None)
        if not is_undefined(description):
            sets["description"] = unwrap_undefined_or(description, None)
        if not is_undefined(image_url):
            sets["image_url"] = unwrap_undefined_or(image_url, None)
        if not is_undefined(readme_note_id):
            sets["readme_note_id"] = (
                None if readme_note_id is None else str(readme_note_id)
            )

        if sets:
            await self._directory_table.update(
                set=sets, where={"id": str(id)}, returning=""
            )

        return await self._fetch_directory_row_only(str(id))

    async def _fetch_directory_row_only(
        self, id: str,
    ) -> Optional[DirectoryEntity]:
        record = await self._directory_table.fetch_by_id(id)
        if not record:
            return None
        return self._row_to_entity(record)

    async def delete_directory(self, id: str) -> bool:
        if is_undefined(id):
            raise ValueError("Directory ID is required for deletion")
        records = await self._directory_table.delete({"id": str(id)})
        return bool(records)

    # ---- parent / child / tag bindings -------------------------------

    async def set_parent_directories(
        self,
        directory_id: str,
        parent_ids: List[str],
    ) -> None:
        """Replace every parent of ``directory_id`` with ``parent_ids``.

        Idempotent: rows that already match are skipped via
        ``ON CONFLICT DO NOTHING``; rows that no longer belong get
        deleted before the new set is inserted.
        """
        current = set(await self.parent_directory_ids_of(directory_id))
        desired = {str(p) for p in parent_ids if p}

        # Drop parents that are no longer wanted.
        for old in current - desired:
            await self._subdirectory_table.delete(
                {
                    "directory_id": old,
                    "child_directory_id": str(directory_id),
                }
            )

        # Insert the new ones.
        for new_parent in desired:
            await self._subdirectory_table.insert(
                {
                    "directory_id": new_parent,
                    "child_directory_id": str(directory_id),
                },
                on_conflict="DO NOTHING",
            )

    async def parent_directory_ids_of(
        self, directory_id: str,
    ) -> List[str]:
        """Return every parent directory id, deduplicated and sorted."""
        records = await self._subdirectory_table.select(
            where={"child_directory_id": directory_id},
            select="directory_id",
        )
        ids = {
            str(r.get("directory_id"))
            for r in records or []
            if r.get("directory_id")
        }
        return sorted(ids)

    async def direct_child_directory_ids_of(
        self, directory_id: str,
    ) -> List[str]:
        records = await self._subdirectory_table.fetch(
            f"""
            SELECT child_directory_id FROM {self._subdirectory_table.name}
            WHERE directory_id = $1
            """,
            directory_id,
        )
        return sorted(
            {
                str(r.get("child_directory_id"))
                for r in records or []
                if r.get("child_directory_id")
            }
        )

    async def list_note_ids(
        self, directory_id: str,
    ) -> List[str]:
        """Direct child note ids of ``directory_id``.

        Sourced from ``note.directory_note``; empty list when
        none.  Mirrors :meth:`direct_child_directory_ids_of` on the
        directory side of the tree.
        """
        records = await self._directory_note_table.fetch(
            f"""
            SELECT note_id FROM {self._directory_note_table.name}
            WHERE directory_id = $1
            """,
            directory_id,
        )
        return sorted(
            {
                str(r.get("note_id"))
                for r in records or []
                if r.get("note_id")
            }
        )

    async def count_direct_child_directories(
        self, directory_id: str,
    ) -> int:
        return len(await self.direct_child_directory_ids_of(directory_id))

    async def bind_note(self, directory_id: str, note_id: str) -> None:
        await self._directory_note_table.insert(
            {"directory_id": str(directory_id), "note_id": str(note_id)},
            on_conflict="DO NOTHING",
        )

    async def unbind_note(self, directory_id: str, note_id: str) -> None:
        await self._directory_note_table.delete(
            {"directory_id": str(directory_id), "note_id": str(note_id)}
        )

    async def tag_ids_of_directory(
        self, directory_id: str,
    ) -> List[str]:
        records = await self._tags_table.select(
            where={"directory_id": str(directory_id)},
            select="tag_id",
        )
        return sorted(
            {
                str(r.get("tag_id"))
                for r in records or []
                if r.get("tag_id")
            }
        )

    async def replace_directory_tags(
        self, directory_id: str, tag_ids: List[str],
    ) -> None:
        """Wipe the directory's tags and reinsert ``tag_ids``."""
        await self._tags_table.delete({"directory_id": str(directory_id)})
        for tag_id in tag_ids:
            if not tag_id:
                continue
            await self._tags_table.insert(
                {
                    "directory_id": str(directory_id),
                    "tag_id": str(tag_id),
                },
                on_conflict="DO NOTHING",
            )

    # ---- subtree walks -----------------------------------------------

    async def get_children(
        self,
        directory_id: str,
        type: str,
        *,
        descendants: bool = False,
        max_depth: int = 10,
    ) -> List[str]:
        valid_types = {"notes", "directories", "both"}
        if type not in valid_types:
            raise ValueError(f"type must be one of {valid_types}, got {type!r}")
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")

        if not descendants:
            if type in ("notes", "both"):
                note_ids = await self.list_note_ids(directory_id)
            else:
                note_ids = []
            if type in ("directories", "both"):
                dir_ids = await self.direct_child_directory_ids_of(directory_id)
            else:
                dir_ids = []
            return sorted(set(note_ids) | set(dir_ids))

        visited: set[str] = set()
        note_ids_set: set[str] = set()
        dir_ids_set: set[str] = {str(directory_id)}
        queue: list[tuple[str, int]] = [(str(directory_id), 0)]
        while queue:
            current_id, depth = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)
            if depth > max_depth:
                continue
            if type in ("notes", "both"):
                for note_id in await self.list_note_ids(current_id):
                    note_ids_set.add(note_id)
            if depth >= max_depth:
                continue
            for child_id in await self.direct_child_directory_ids_of(
                current_id
            ):
                dir_ids_set.add(child_id)
                if child_id not in visited:
                    queue.append((child_id, depth + 1))
        if type == "notes":
            return sorted(note_ids_set)
        if type == "directories":
            return sorted(dir_ids_set)
        return sorted(note_ids_set | dir_ids_set)

    async def get_descendants(
        self,
        root_id: str,
        type: str,
        *,
        max_depth: int = 10,
    ) -> List[str]:
        return await self.get_children(
            root_id, type, descendants=True, max_depth=max_depth
        )

    # --- helpers -------------------------------------------------------

    @staticmethod
    def _resolve_undefined_none(value: UndefinedNoneOr[str]) -> Optional[str]:
        """Map a nullable ``UndefinedNoneOr`` into a SQL-friendly value.

        * ``UNDEFINED`` -> SQL ``NULL`` (no value supplied).
        * ``None``       -> SQL ``NULL`` (explicitly cleared).
        * concrete str  -> ``str``.
        """
        if is_undefined(value):
            return None
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _row_to_entity(row: object) -> DirectoryEntity:
        """Map one ``note.directory`` record to a :class:`DirectoryEntity`.

        The ``Table.fetch`` machinery may surface ``asyncpg.Record``
        (production) or a plain ``dict`` (in-memory fakes).  Handle
        both uniformly so callers never see a raw driver-specific
        type.
        """
        def _get(key: str) -> object:
            if isinstance(row, asyncpg.Record):
                return row.get(key)  # type: ignore[dict-item]
            if isinstance(row, dict):
                return row.get(key)
            raise TypeError(f"Unsupported row type: {type(row)}")

        return DirectoryEntity(
            id=str(_get("id")) if _get("id") is not None else UNDEFINED,
            slug=(
                str(_get("slug"))
                if _get("slug") is not None
                else None
            ),
            display_name=(
                str(_get("display_name"))
                if _get("display_name") is not None
                else None
            ),
            description=(
                str(_get("description"))
                if _get("description") is not None
                else None
            ),
            image_url=(
                str(_get("image_url"))
                if _get("image_url") is not None
                else None
            ),
            readme_note_id=(
                str(_get("readme_note_id"))
                if _get("readme_note_id") is not None
                else None
            ),
            relations=UNDEFINED,
        )


__all__ = ["PostgresDirectoryRepo"]
