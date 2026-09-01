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

from typing import Dict, List, Optional

from src.api.services.directory_service import DirectoryIncludeOptions
from src.api.repos.directory_repo import DirectoryRepoABC
from src.api.other.undefined import (
    UNDEFINED,
    UndefinedNoneOr,
    UndefinedOr,
    is_undefined,
    resolve_undefined_none,
    unwrap_undefined_or,
)
from src.api.repos.directory_repo import (
    DirectoryChildType,
    DirectoryHierarchyType,
    DirectoryParentType,
)
from src.db.entities.directory.directory import DirectoryEntity
from src.db.table import TableABC
from src.utils import row_get


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
                "display_name": resolve_undefined_none(display_name),
                "description": resolve_undefined_none(description),
                "image_url": resolve_undefined_none(image_url),
                "readme_note_id": resolve_undefined_none(readme_note_id),
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

        # Pick a dedicated SQL per combination of list flags.
        # this is to increase performance by just performing one SQL op
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
        """Row + child directory ids + child note ids (one JOIN each).

        Note:
            The two LEFT JOINs against ``note.directory_subdirectory``
            and ``note.directory_note`` produce a Cartesian product
            of ``N_subdirs * N_notes`` rows; without ``DISTINCT`` each
            child id would be repeated M (or N) times in the arrays
            and inflate the frontend counts.  ``array_agg(DISTINCT)``
            collapses those duplicates back to the real id set.
        """
        records = await self._directory_table.fetch(
            """
            SELECT d.id, d.slug, d.display_name, d.description,
                   d.image_url, d.readme_note_id,
                   COALESCE(
                       array_agg(DISTINCT s.child_directory_id)
                       FILTER (WHERE s.child_directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS child_directory_ids,
                   COALESCE(
                       array_agg(DISTINCT n.note_id)
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

        Note:
            The three LEFT JOINs produce a Cartesian product
            (``N_parents * N_children_dirs * N_children_notes``
            rows); without ``DISTINCT`` each id would be repeated
            across every cross-joined row and inflate the
            frontend counts.  ``array_agg(DISTINCT)`` collapses
            those duplicates back to the real id sets.
        """
        records = await self._directory_table.fetch(
            """
            SELECT d.id, d.slug, d.display_name, d.description,
                   d.image_url, d.readme_note_id,
                   COALESCE(
                       array_agg(DISTINCT parent_s.directory_id)
                       FILTER (WHERE parent_s.directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS parent_directory_ids,
                   COALESCE(
                       array_agg(DISTINCT child_s.child_directory_id)
                       FILTER (WHERE child_s.child_directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS child_directory_ids,
                   COALESCE(
                       array_agg(DISTINCT child_n.note_id)
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

    # ---- parent / child bindings (new API: parent_type + child_type) -

    async def set_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
    ) -> None:
        """Replace the entire parent set of ``child_id`` (typed).

        Uses a set-match (current vs desired) so a no-op call is
        cheap.  For directory<->directory writes the cycle check
        runs first; for shelf<->directory or shelf/book writes
        the check is a no-op because shelves are flat.

        ``parent_type`` is the type of every id in ``parent_ids``;
        mixing directory and shelf parents in one call is not
        supported -- callers must issue separate calls per type.
        """
        if child_type == "directory" and parent_type == "directory":
            # Cycle check: walking the descendants of each new
            # parent must NOT include ``child_id``; otherwise the
            # new edge would close a loop in the directory DAG.
            descendants_of_child = set(
                await self.get_children_of(
                    "directory", str(child_id), "directory", depth=10
                )
            )
            for new_parent in parent_ids:
                if (
                    str(new_parent) == str(child_id)
                    or str(new_parent) in descendants_of_child
                ):
                    raise ValueError(
                        f"adding parent {new_parent!r} for directory "
                        f"{child_id!r} would create a cycle"
                    )

        current = set(
            await self.get_parents_of(
                child_type, str(child_id), parent_type
            )
        )
        desired = {str(p) for p in parent_ids if p}

        for old in current - desired:
            await self._delete_binding(parent_type, old, child_type, str(child_id))
        for new_parent in desired.difference(current):
            await self._insert_binding(
                parent_type, new_parent, child_type, str(child_id)
            )

    async def get_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
    ) -> List[str]:
        """Return parent ids of ``child_id`` filtered by ``parent_type``.

        ``child_type`` selects which child table to scan; the
        ``parent_type`` argument exists for symmetry with the
        other helpers -- for child="directory" the parent is
        always a directory (or shelf); the implementation below
        dispatches on the parent_type because the shelf path
        lives on :class:`ShelfRepoABC`.  This method only owns
        the directory<->directory + directory<->note lookups;
        for shelf parents callers should ask the shelf repo.
        """
        if parent_type == "shelf":
            # Shelves are not visible from the directory repo.
            return []
        # parent_type == "directory"
        if child_type == "directory":
            records = await self._subdirectory_table.select(
                where={"child_directory_id": str(child_id)},
                select="directory_id",
            )
            return sorted(
                str(row_get(r, "directory_id"))
                for r in records or []
                if row_get(r, "directory_id")
            )
        if child_type == "note":
            records = await self._directory_note_table.select(
                where={"note_id": str(child_id)},
                select="directory_id",
            )
            return sorted(
                str(row_get(r, "directory_id"))
                for r in records or []
                if row_get(r, "directory_id")
            )
        raise ValueError(f"invalid child_type: {child_type!r}")

    async def get_children_of(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> List[str]:
        """Return child ids under ``parent_id`` (typed parents + children).

        Args:
            parent_type: kind of ``parent_id``.
            parent_id: starting parent id.
            child_type: kind of children to return
                (``"note"`` / ``"directory"`` / ``"both"``).
            depth: recursion depth; ``1`` means direct children
                only (the parent itself is never returned).
                ``depth=0`` returns ``[]``.

        Returns:
            List[str]: matching child ids, deduplicated and sorted.

        Raises:
            ValueError: invalid depth / parent_type / child_type
            combination.
        """
        if depth < 0:
            raise ValueError("depth must be >= 0")
        if parent_type == "shelf":
            # Shelves are flat: a single hop.  ``depth`` is
            # honoured as an upper bound (depth=0 -> ``[]``,
            # depth>=1 -> the books).
            if child_type == "directory" or child_type == "both":
                # Caller should use ShelfRepoABC.get_books_of for
                # a focused book listing, but we forward here for
                # the bulk paths (e.g. ``get_children_for``).
                records = await self._directory_table.fetch(
                    f"""
                    SELECT s.book_id
                    FROM note.shelf_book s
                    WHERE s.shelf_id = $1
                    """,
                    str(parent_id),
                )
                book_ids = sorted(
                    str(row_get(r, "book_id"))
                    for r in records or []
                    if row_get(r, "book_id")
                )
                if child_type == "directory":
                    return book_ids
                return book_ids  # 'both' on a shelf == directories
            raise ValueError(
                f"shelves cannot have notes as children "
                f"(child_type={child_type!r})"
            )

        # parent_type == "directory"
        if depth == 0:
            return []
        visited: set[str] = set()
        queued: set[str] = {str(parent_id)}
        queue: list[tuple[str, int]] = [(str(parent_id), 0)]
        note_ids: set[str] = set()
        directory_ids: set[str] = set()

        while queue:
            current_id, current_depth = queue.pop(0)
            if current_id in visited or current_depth >= depth:
                continue
            visited.add(current_id)

            if child_type in ("note", "both"):
                records = await self._directory_note_table.select(
                    where={"directory_id": current_id},
                    select="note_id",
                )
                note_ids.update(
                    str(row_get(r, "note_id"))
                    for r in records or []
                    if row_get(r, "note_id")
                )
            records = await self._subdirectory_table.select(
                where={"directory_id": current_id},
                select="child_directory_id",
            )
            child_directory_ids = [
                str(row_get(r, "child_directory_id"))
                for r in records or []
                if row_get(r, "child_directory_id")
            ]
            if child_type in ("directory", "both"):
                directory_ids.update(child_directory_ids)
            if current_depth + 1 < depth:
                for child_id in child_directory_ids:
                    if child_id not in queued:
                        queued.add(child_id)
                        queue.append((child_id, current_depth + 1))

        if child_type == "note":
            return sorted(note_ids)
        if child_type == "directory":
            return sorted(directory_ids)
        return sorted(note_ids | directory_ids)

    async def get_children_for(
        self,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> Dict[str, List[str]]:
        """Per-input-parent children from the typed hierarchy tables."""
        result: Dict[str, List[str]] = {}
        for parent_id in parent_ids:
            result[str(parent_id)] = await self.get_children_of(
                parent_type, str(parent_id), child_type, depth=depth
            )
        return result

    async def get_parents_for(
        self,
        child_type: DirectoryChildType,
        child_ids: List[str],
        parent_type: DirectoryParentType,
    ) -> Dict[str, List[str]]:
        """Per-input-child parents of the requested parent type."""
        result: Dict[str, List[str]] = {}
        for child_id in child_ids:
            result[str(child_id)] = await self.get_parents_of(
                child_type, str(child_id), parent_type
            )
        return result

    async def add_child_to(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        """Add a single typed parent<->child binding.

        For directory<->directory edges this runs the cycle
        check before writing -- refusing to write if the new
        edge would close a loop in the DAG.

        For ``(parent_type="shelf", child_type="directory")`` we
        validate the pair but the shelf repo owns the actual
        row -- the caller must use :class:`ShelfRepoABC` for
        shelf writes.  This method raises ``ValueError`` to
        make the split explicit.
        """
        if parent_type == "shelf" and child_type == "note":
            raise ValueError(
                "shelves cannot host notes directly "
                "(parent_type='shelf', child_type='note')"
            )
        if parent_type == "shelf":
            raise ValueError(
                "shelf bindings are owned by ShelfRepoABC; "
                "use ShelfRepoABC.add_book()"
            )
        if parent_type == "directory" and child_type == "directory":
            # Cycle check: parent_id must not be a descendant of
            # child_id (otherwise parent_id -> ... -> child_id
            # -> parent_id closes a loop).
            descendants = set(
                await self.get_children_of(
                    "directory", str(child_id), "directory", depth=10
                )
            )
            if (
                str(parent_id) == str(child_id)
                or str(parent_id) in descendants
            ):
                raise ValueError(
                    f"adding directory parent {parent_id!r} for "
                    f"directory {child_id!r} would create a cycle"
                )
        await self._insert_binding(parent_type, str(parent_id), child_type, str(child_id))

    async def remove_child_from(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        """Remove a single typed parent<->child binding."""
        if parent_type == "shelf":
            raise ValueError(
                "shelf bindings are owned by ShelfRepoABC; "
                "use ShelfRepoABC.remove_book()"
            )
        await self._delete_binding(parent_type, str(parent_id), child_type, str(child_id))

    # ---- typed binding helpers ----------------------------------------

    async def _insert_binding(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        """Insert one parent<->child row into the matching table."""
        if parent_type == "directory" and child_type == "directory":
            await self._subdirectory_table.insert(
                {"directory_id": str(parent_id), "child_directory_id": str(child_id)},
                on_conflict="DO NOTHING",
            )
            return
        if parent_type == "directory" and child_type == "note":
            await self._directory_note_table.insert(
                {"directory_id": str(parent_id), "note_id": str(child_id)},
                on_conflict="DO NOTHING",
            )
            return
        raise ValueError(
            f"unsupported parent/child type pair: "
            f"parent={parent_type!r}, child={child_type!r}"
        )

    async def _delete_binding(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        """Delete one parent<->child row from the matching table."""
        if parent_type == "directory" and child_type == "directory":
            await self._subdirectory_table.delete(
                {"directory_id": str(parent_id), "child_directory_id": str(child_id)}
            )
            return
        if parent_type == "directory" and child_type == "note":
            await self._directory_note_table.delete(
                {"directory_id": str(parent_id), "note_id": str(child_id)}
            )
            return
        raise ValueError(
            f"unsupported parent/child type pair: "
            f"parent={parent_type!r}, child={child_type!r}"
        )

    async def get_descendants(
        self,
        root_id: str,
        type: DirectoryHierarchyType,
        *,
        max_depth: int = 10,
    ) -> List[str]:
        return await self.get_children_of("directory", root_id, type, depth=max_depth)

    # --- helpers -------------------------------------------------------

    @staticmethod
    def _row_to_entity(row: object) -> DirectoryEntity:
        """Map one ``note.directory`` record to a :class:`DirectoryEntity`.

        The ``Table.fetch`` machinery may surface ``asyncpg.Record``
        (production) or a plain ``dict`` (in-memory fakes).  Handle
        both uniformly so callers never see a raw driver-specific
        type.
        """
        def _get(key: str) -> object:
            return row_get(row, key)

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
