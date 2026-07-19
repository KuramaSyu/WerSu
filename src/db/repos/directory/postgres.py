"""Postgres implementation of :class:`PostgresDirectoryRepoABC`.

Every SQL statement lives here.  The class deliberately does **not**
consult SpiceDB; caller layers (notably the directory facade) wire
in the permission repo when SpiceDB visibility matters.

Tables touched:

* ``note.directory`` -- the directory row itself.
* ``note.directory_subdirectory`` -- parent / child graph between
  directories (the directory tree).
* ``note.directory_note`` -- directory / note bindings.

Tag CRUD no longer lives here -- it is owned by
:class:`src.db.repos.tag.postgres.PostgresTagRepo`.  The
directory facade composes that repo in addition to this one.

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

from src.api.services.directory_service import DirectoryIncludeOptions
from src.api.repos.directory_repo import DirectoryRepoABC
from src.api.other.undefined import (
    UNDEFINED,
    UndefinedNoneOr,
    UndefinedOr,
    is_undefined,
    unwrap_undefined_or,
)
from src.api.repos.directory_repo import DirectoryChildType, DirectoryHierarchyType
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
    """

    _DIRECTORY_COLUMNS = (
        "id, slug, display_name, description, image_url, readme_note_id"
    )

    def __init__(
        self,
        directory_table: TableABC,
        subdirectory_table: TableABC,
        directory_note_table: TableABC,
    ) -> None:
        self._directory_table = directory_table
        self._subdirectory_table = subdirectory_table
        self._directory_note_table = directory_note_table

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

    async def set_parent_directories_of(
        self,
        subject_type: DirectoryChildType,
        subject_id: str,
        parent_ids: List[str],
    ) -> None:
        """Firstly a set match is performed to avaoid dublicate writes. Then changes are inserted/deleted"""
        # current parents of subject id
        current: Set[str]
        if subject_type == "directory":
            current = set(await self.get_parent_of("directory", subject_id))
        else:
            current = set(await self.get_parent_of("note", subject_id))

        # given, new parents for subject id
        desired = {str(p) for p in parent_ids if p}

        # Drop parents that are no longer wanted.
        for old in current - desired:
            if subject_type == "directory":
                await self._subdirectory_table.delete(
                    {
                        "directory_id": old,
                        "child_directory_id": subject_id,
                    }
                )
            else:
                await self._directory_note_table.delete(
                    {
                        "directory_id": old,
                        "note_id": subject_id,
                    }
                )

        # Insert the new ones.
        for new_parent in desired.difference(current):
            if subject_type == "directory":
                await self._subdirectory_table.insert(
                    {
                        "directory_id": new_parent,
                        "child_directory_id": subject_id,
                    },
                    on_conflict="DO NOTHING",
                )
            else:
                await self._directory_note_table.insert(
                    {
                        "directory_id": new_parent,
                        "note_id": subject_id,
                    },
                    on_conflict="DO NOTHING",
                )

    async def get_parent_of(
        self,
        type: DirectoryHierarchyType,
        directory_id: str,
    ) -> List[str]:
        """Return parent ids for the requested child type."""
        parent_ids: set[str] = set()
        if type in ("directory", "both"):
            records = await self._subdirectory_table.select(
                where={"child_directory_id": directory_id},
                select="directory_id",
            )
            parent_ids.update(
                str(r.get("directory_id"))
                for r in records or []
                if r.get("directory_id")
            )
        if type in ("note", "both"):
            records = await self._directory_note_table.select(
                where={"note_id": directory_id},
                select="directory_id",
            )
            parent_ids.update(
                str(r.get("directory_id"))
                for r in records or []
                if r.get("directory_id")
            )
        return sorted(parent_ids)

    async def get_children_of(
        self,
        type: DirectoryHierarchyType,
        directory_id: str,
        depth: int = 1,
    ) -> List[str]:
        """Return child ids for the requested type up to ``depth`` levels."""
        if depth < 0:
            raise ValueError("depth must be >= 0")
        if depth == 0:
            return []

        visited: set[str] = set()
        queued: set[str] = {str(directory_id)}
        queue: list[tuple[str, int]] = [(str(directory_id), 0)]
        note_ids: set[str] = set()
        directory_ids: set[str] = set()

        while queue:
            current_id, current_depth = queue.pop(0)
            if current_id in visited or current_depth >= depth:
                continue
            visited.add(current_id)

            if type in ("note", "both"):
                records = await self._directory_note_table.select(
                    where={"directory_id": current_id},
                    select="note_id",
                )
                note_ids.update(
                    str(r.get("note_id"))
                    for r in records or []
                    if r.get("note_id")
                )
            records = await self._subdirectory_table.select(
                where={"directory_id": current_id},
                select="child_directory_id",
            )
            child_directory_ids = [
                str(r.get("child_directory_id"))
                for r in records or []
                if r.get("child_directory_id")
            ]
            if type in ("directory", "both"):
                directory_ids.update(child_directory_ids)
            if current_depth + 1 < depth:
                for child_id in child_directory_ids:
                    if child_id not in queued:
                        queued.add(child_id)
                        queue.append((child_id, current_depth + 1))

        if type == "note":
            return sorted(note_ids)
        if type == "directory":
            return sorted(directory_ids)
        return sorted(note_ids | directory_ids)

    async def get_children_for(
        self,
        type: DirectoryHierarchyType,
        directory_ids: List[str],
        depth: int = 1,
    ) -> List[str]:
        """Return child ids for multiple directories."""
        if not directory_ids:
            return []
        child_ids: set[str] = set()
        for directory_id in directory_ids:
            child_ids.update(
                await self.get_children_of(type, str(directory_id), depth=depth)
            )
        return sorted(child_ids)

    async def get_parent_for(
        self,
        type: DirectoryHierarchyType,
        child_ids: List[str],
    ) -> List[str]:
        """Return parent ids for multiple child ids."""
        if not child_ids:
            return []
        parent_ids: set[str] = set()
        for child_id in child_ids:
            parent_ids.update(await self.get_parent_of(type, str(child_id)))
        return sorted(parent_ids)

    async def add_child_to_directory(
        self,
        type: DirectoryChildType,
        directory_id: str,
        child_id: str,
    ) -> None:
        """Add a note or child directory binding."""
        if type == "note":
            await self._directory_note_table.insert(
                {"directory_id": str(directory_id), "note_id": str(child_id)},
                on_conflict="DO NOTHING",
            )
            return
        if type == "directory":
            await self._subdirectory_table.insert(
                {
                    "directory_id": str(directory_id),
                    "child_directory_id": str(child_id),
                },
                on_conflict="DO NOTHING",
            )
            return
        raise ValueError("type must be 'note' or 'directory'")

    async def remove_child_from_directory(
        self,
        type: DirectoryChildType,
        directory_id: str,
        child_id: str,
    ) -> None:
        """Remove a note or child directory binding."""
        if type == "note":
            await self._directory_note_table.delete(
                {"directory_id": str(directory_id), "note_id": str(child_id)}
            )
            return
        if type == "directory":
            await self._subdirectory_table.delete(
                {
                    "directory_id": str(directory_id),
                    "child_directory_id": str(child_id),
                }
            )
            return
        raise ValueError("type must be 'note' or 'directory'")

    async def get_descendants(
        self,
        root_id: str,
        type: DirectoryHierarchyType,
        *,
        max_depth: int = 10,
    ) -> List[str]:
        return await self.get_children_of(type, root_id, depth=max_depth)

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
