from __future__ import annotations

from dataclasses import dataclass
from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.api.repos.directory_repo import DirectoryChildType, DirectoryHierarchyType, DirectoryParentType, DirectoryRepoABC
from src.api.repos.tag_repo import TagRepoABC
from src.api.services.directory_service import DirectoryIncludeOptions
from src.db.entities.directory.directory import DirectoryEntity
from typing import Any, Dict, List, Optional, Set, Tuple


class _RecordingDirectoryRepo(DirectoryRepoABC):
    """In-memory :class:`DirectoryRepoABC` that records every public call.

    Stores enough state to round-trip the methods the facade depends on
    (insert / update / fetch / hierarchy) so behaviour-driven tests can
    assert both *what was called* and *what the resulting state looks
    like*.

    The hierarchy helpers are implemented against an internal
    ``(child_type, child_id) -> {parent_id}`` map and a
    ``(parent_type, parent_id) -> {child_id}`` map so
    :meth:`get_parents_of` and :meth:`get_children_of` mirror the
    production Postgres semantics used by the facade.
    """

    def __init__(self) -> None:
        # ---- row store -------------------------------------------------
        self.entities: Dict[str, DirectoryEntity] = {}
        self._next_id = 0

        # ---- hierarchy store -------------------------------------------
        # (child_type, child_id) -> set of parent ids
        self._parents: Dict[Tuple[str, str], Set[str]] = {}
        # (parent_type, parent_id) -> set of child ids
        self._children: Dict[Tuple[str, str], Set[str]] = {}

        # ---- recorded calls --------------------------------------------
        self.insert_calls: List[_InsertCall] = []
        self.fetch_calls: List[Tuple[str, Optional[DirectoryIncludeOptions]]] = []
        self.fetch_by_ids_calls: List[List[str]] = []
        self.update_calls: List[_UpdateCall] = []
        self.delete_calls: List[str] = []
        self.set_parent_calls: List[_SetParentCall] = []
        self.get_parent_calls: List[Tuple[DirectoryChildType, str, DirectoryParentType]] = []
        self.get_children_calls: List[Tuple[DirectoryParentType, str, DirectoryHierarchyType, int]] = []
        self.get_children_for_calls: List[Tuple[DirectoryParentType, List[str], DirectoryHierarchyType, int]] = []
        self.get_parent_for_calls: List[Tuple[DirectoryChildType, List[str], DirectoryParentType]] = []
        self.add_child_calls: List[_ChildCall] = []
        self.remove_child_calls: List[_ChildCall] = []

    # ---- helpers --------------------------------------------------------

    def _next_id_str(self, hint: Optional[str] = None) -> str:
        if hint:
            return str(hint)
        self._next_id += 1
        return f"dir-{self._next_id}"

    def _parents_of(
        self, child_type: DirectoryHierarchyType, child_id: str
    ) -> Set[str]:
        return set(self._parents.get((child_type, str(child_id)), set()))

    def _children_of(
        self, parent_type: DirectoryHierarchyType, parent_id: str
    ) -> Set[str]:
        return set(self._children.get((parent_type, str(parent_id)), set()))

    # ---- DirectoryRepoABC ----------------------------------------------

    async def insert_directory(
        self,
        *,
        slug: str,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
    ) -> DirectoryEntity:
        new_id = self._next_id_str()
        entity = DirectoryEntity(
            id=new_id,
            slug=slug,
            display_name=display_name,
            description=description,
            image_url=image_url,
            readme_note_id=readme_note_id,
        )
        self.entities[new_id] = entity
        self.insert_calls.append(
            _InsertCall(
                slug=slug,
                display_name=display_name,
                description=description,
                image_url=image_url,
                readme_note_id=readme_note_id,
            )
        )
        return entity

    async def fetch_directory(
        self,
        id: str,
        *,
        include: Optional[DirectoryIncludeOptions] = None,
    ) -> Optional[DirectoryEntity]:
        self.fetch_calls.append((str(id), include))
        return self.entities.get(str(id))

    async def fetch_directories_by_ids(self, ids: List[str]) -> List[DirectoryEntity]:
        ids = [str(i) for i in ids]
        self.fetch_by_ids_calls.append(ids)
        return [self.entities[i] for i in ids if i in self.entities]

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
        self.update_calls.append(
            _UpdateCall(
                directory_id=str(id),
                slug=slug,
                display_name=display_name,
                description=description,
                image_url=image_url,
                readme_note_id=readme_note_id,
            )
        )
        existing = self.entities.get(str(id))
        if existing is None:
            return None

        def _pick(new: Any, old: Any) -> Any:
            """UNDEFINED -> keep, None -> clear, value -> overwrite."""
            if new is UNDEFINED:
                return old
            return new

        updated = DirectoryEntity(
            id=existing.id,
            slug=_pick(slug, existing.slug),
            display_name=_pick(display_name, existing.display_name),
            description=_pick(description, existing.description),
            image_url=_pick(image_url, existing.image_url),
            readme_note_id=_pick(readme_note_id, existing.readme_note_id),
        )
        self.entities[str(id)] = updated
        return updated

    async def delete_directory(self, id: str) -> bool:
        self.delete_calls.append(str(id))
        existed = self.entities.pop(str(id), None) is not None
        # Drop any hierarchy edges that referenced this id on either side.
        for key in list(self._parents):
            if str(id) in self._parents[key]:
                self._parents[key].discard(str(id))
        for key in list(self._children):
            if str(id) in self._children[key]:
                self._children[key].discard(str(id))
        return existed

    # ---- DirectoryHelperMixin -----------------------------------------

    async def set_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
    ) -> None:
        self.set_parent_calls.append(
            _SetParentCall(
                child_type=child_type,
                child_id=str(child_id),
                parent_type=parent_type,
                parent_ids=list(parent_ids),
            )
        )
        # Replace the parent set on this (child_type, child_id) row.
        cleaned = {str(p) for p in parent_ids if p}
        self._parents[(child_type, str(child_id))] = cleaned
        # Mirror the inverse edge: parent -> child.
        for existing_parent in list(self._children):
            if (
                existing_parent[0] == child_type
                and str(child_id) in self._children[existing_parent]
                and existing_parent[1] not in cleaned
            ):
                self._children[existing_parent].discard(str(child_id))
        for parent_id in cleaned:
            self._children.setdefault(
                (child_type, parent_id), set()
            ).add(str(child_id))

    async def get_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
    ) -> List[str]:
        self.get_parent_calls.append((child_type, str(child_id), parent_type))
        if parent_type == "shelf":
            return []
        if child_type == "both":
            union = set()
            for ct in ("note", "directory"):
                union |= self._parents_of(ct, child_id)  # type: ignore[arg-type]
            return sorted(union)
        return sorted(self._parents_of(child_type, child_id))  # type: ignore[arg-type]

    async def get_children_of(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> List[str]:
        if depth < 0:
            raise ValueError("depth must be >= 0")
        self.get_children_calls.append((parent_type, str(parent_id), child_type, depth))
        if parent_type == "shelf":
            # shelves own no children in this fake; the tests
            # only exercise the directory path.
            return []
        return sorted(self._children_of(child_type, parent_id))  # type: ignore[arg-type]

    async def get_children_for(
        self,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> Dict[str, List[str]]:
        if depth < 0:
            raise ValueError("depth must be >= 0")
        self.get_children_for_calls.append(
            (parent_type, [str(d) for d in parent_ids], child_type, depth)
        )
        result: Dict[str, List[str]] = {}
        if parent_type == "shelf":
            return {str(d): [] for d in parent_ids}
        for d in parent_ids:
            result[str(d)] = sorted(self._children_of(child_type, str(d)))  # type: ignore[arg-type]
        return result

    async def get_parents_for(
        self,
        child_type: DirectoryChildType,
        child_ids: List[str],
        parent_type: DirectoryParentType,
    ) -> Dict[str, List[str]]:
        self.get_parent_for_calls.append(
            (child_type, [str(c) for c in child_ids], parent_type)
        )
        if parent_type == "shelf":
            return {str(c): [] for c in child_ids}
        result: Dict[str, List[str]] = {}
        for c in child_ids:
            result[str(c)] = sorted(self._parents_of(child_type, str(c)))  # type: ignore[arg-type]
        return result

    async def add_child_to(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        self.add_child_calls.append(
            _ChildCall(
                parent_type=parent_type,
                parent_id=str(parent_id),
                child_type=child_type,
                child_id=str(child_id),
            )
        )
        self._parents.setdefault((child_type, str(child_id)), set()).add(str(parent_id))
        self._children.setdefault((child_type, str(parent_id)), set()).add(str(child_id))

    async def remove_child_from(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        self.remove_child_calls.append(
            _ChildCall(
                parent_type=parent_type,
                parent_id=str(parent_id),
                child_type=child_type,
                child_id=str(child_id),
            )
        )
        if (child_type, str(child_id)) in self._parents:
            self._parents[(child_type, str(child_id))].discard(str(parent_id))
        if (child_type, str(parent_id)) in self._children:
            self._children[(child_type, str(parent_id))].discard(str(child_id))


class _RecordingTagRepo(TagRepoABC):
    """Bare-bones :class:`TagRepoABC` that only records calls.

    The facade only ever calls :meth:`replace_tags_of` on the tag
    repo, so we keep this fake minimal: tag identity is not checked,
    only the recorded call arguments.
    """

    def __init__(self) -> None:
        self.replace_calls: List[Tuple[str, str, List[str]]] = []

    async def replace_tags_of(
        self,
        subject_type,
        subject_id,
        tag_ids,
    ) -> None:  # type: ignore[override]
        self.replace_calls.append(
            (subject_type, str(subject_id), [str(t) for t in tag_ids])
        )

    # ---- Not exercised by these tests; stub to keep the ABC satisfied -

    async def create_tag(self, slug, display_name):  # type: ignore[override]
        raise NotImplementedError

    async def get_tag_by_id(self, tag_id):  # type: ignore[override]
        raise NotImplementedError

    async def list_tags(self):  # type: ignore[override]
        raise NotImplementedError

    async def update_tag(self, tag_id, *, slug=None, display_name=None):  # type: ignore[override]
        raise NotImplementedError

    async def delete_tag(self, tag_id):  # type: ignore[override]
        raise NotImplementedError

    async def list_tags_for(self, subject_type, subject_ids):  # type: ignore[override]
        raise NotImplementedError

    async def assign_tag_to(self, subject_type, subject_id, tag_id):  # type: ignore[override]
        raise NotImplementedError

    async def replace_tags_for(self, subject_type, subject_ids, tag_ids):  # type: ignore[override]
        raise NotImplementedError

    async def remove_tag_from(self, subject_type, subject_id, tag_id):  # type: ignore[override]
        raise NotImplementedError


@dataclass
class _InsertCall:
    """Recorded :meth:`DirectoryRepoABC.insert_directory` invocation."""

    slug: str
    display_name: Any
    description: Any
    image_url: Any
    readme_note_id: Any


@dataclass
class _UpdateCall:
    """Recorded :meth:`DirectoryRepoABC.update_directory` invocation."""

    directory_id: str
    slug: Any
    display_name: Any
    description: Any
    image_url: Any
    readme_note_id: Any


@dataclass
class _SetParentCall:
    """Recorded :meth:`DirectoryRepoABC.set_parents_of` invocation."""

    child_type: DirectoryChildType
    child_id: str
    parent_type: DirectoryParentType
    parent_ids: List[str]


@dataclass
class _ChildCall:
    """Recorded :meth:`DirectoryRepoABC.add/remove_child_to` invocation."""

    parent_type: DirectoryParentType
    parent_id: str
    child_type: DirectoryChildType
    child_id: str


__all__ = [
    '_RecordingDirectoryRepo',
    '_RecordingTagRepo',
    '_InsertCall',
    '_UpdateCall',
    '_SetParentCall',
    '_ChildCall',
]
