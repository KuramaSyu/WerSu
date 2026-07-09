"""Storage contract for the permission/relationship backend.

The :class:`PermissionRepoABC` is the only thing the service layer is
allowed to depend on for permission lookups, so any backend (SpiceDB,
an in-memory store for tests, ...) can be swapped in by providing a
new implementation of this ABC.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List
from warnings import deprecated

from src.api.relationship import ObjectRef, Relationship
from src.api.user_context import UserContextABC


class PermissionRepoABC(ABC):
    """Persistence contract for permissions and direct relationships.

    Implementations:
    * :class:`src.db.repos.permissions.permission.NotePermissionRepoSpicedb`
    * :class:`src.db.repos.permissions.permission.NotePermissionRepoInMemory`
    """

    @abstractmethod
    async def insert(
        self,
        relationships: List[Relationship],
    ) -> List[Relationship]:
        """Insert ``relationships`` and return the persisted rows.

        Args:
            relationships: relationships to insert.  ``resource``,
                ``relation`` and ``subject`` must all be fully set;
                no field may be :obj:`~src.api.undefined.UNDEFINED`.

        Returns:
            List[Relationship]: the inserted relationships, with any
            server-side fields populated.
        """
        ...

    @abstractmethod
    async def delete(
        self,
        relationship: Relationship,
    ) -> Relationship:
        """Delete relationships matching ``relationship`` as a filter.

        Any :obj:`~src.api.undefined.UNDEFINED` field acts as a wildcard
        for that part of the filter.  For example, a relationship shaped
        ``attachment#*@user`` (with ``object_id`` set) deletes every
        permission on that attachment for every user.

        Args:
            relationship: filter describing the relationships to delete.

        Returns:
            Relationship: the (single) deleted relationship.
        """
        ...

    @deprecated("lookup() is deprecated, use lookup_relationships() instead")
    @abstractmethod
    async def lookup(
        self,
        relationship: Relationship,
    ) -> List[ObjectRef]:
        """Resolve the objects matching ``relationship``.

        .. deprecated::
            Use :meth:`lookup_relationships` instead.

        Args:
            relationship: filter where ``subject`` and ``relation`` are
                fully set, ``resource.object_type`` is set, and
                ``resource.object_id`` is :obj:`~src.api.undefined.UNDEFINED`
                to match all objects of that type.

        Returns:
            List[ObjectRef]: the matching objects.
        """
        ...

    @abstractmethod
    async def lookup_relationships(
        self,
        relationship: Relationship,
    ) -> List[Relationship]:
        """Return stored direct relationships matching ``relationship``.

        Args:
            relationship: filter where any :obj:`~src.api.undefined.UNDEFINED`
                id acts as a wildcard.  ``relation`` may also be
                :obj:`~src.api.undefined.UNDEFINED` to match across relations.

        Returns:
            List[Relationship]: the matching stored relationships.
        """
        ...

    @abstractmethod
    async def lookup_notes(
        self,
        user: UserContextABC,
        permission: str,
    ) -> List[ObjectRef]:
        """Return every note where ``user`` has ``permission``.

        Args:
            user: caller whose permissions should be evaluated.
            permission: permission name to look up (e.g. ``"view"``).

        Returns:
            List[ObjectRef]: the matching notes.
        """
        ...

    @abstractmethod
    async def list_relationships(self, resource: ObjectRef) -> List[Relationship]:
        """Return every stored relationship whose resource matches ``resource``.

        Args:
            resource: resource to filter on.  If ``object_id`` is
                :obj:`~src.api.undefined.UNDEFINED`, every relationship
                of that resource type is returned.

        Returns:
            List[Relationship]: stored relationships for ``resource``,
            including their relation and subject.
        """
        ...

    @abstractmethod
    async def has_permission(
        self,
        user: UserContextABC,
        permission: str,
        resource: ObjectRef,
    ) -> bool:
        """Return whether ``user`` has ``permission`` on ``resource``.

        Args:
            user: caller whose permissions should be evaluated.
            permission: permission name to verify.
            resource: resource to check against.

        Returns:
            bool: ``True`` if the permission is granted.
        """
        ...

    @abstractmethod
    async def check(
        self,
        relationship: Relationship,
    ) -> bool:
        """Evaluate a single ``resource#relation@subject`` triple.

        Args:
            relationship: the ``obj#relation@subj`` triple to evaluate.

        Returns:
            bool: whether the relationship evaluates to ``True``.
        """
        ...

    @abstractmethod
    async def get_permissions(
        self,
        user: UserContextABC,
        resource: ObjectRef,
    ) -> List[str]:
        """Return every effective permission ``user`` holds on ``resource``.

        Args:
            user: caller whose permissions should be evaluated.
            resource: resource to evaluate.

        Returns:
            List[str]: the granted permission names.
        """
        ...

    @abstractmethod
    async def resolve_children(
        self,
        directory_id: str,
        *,
        max_depth: int = 10,
        exclusive: bool = True,
    ) -> "ResolvedChildren":
        """Walk a directory subtree and collect every child resource.

        Walks ``directory#parent@directory`` recursively to gather the
        set of sub-directories under ``directory_id``, then expands
        one more hop via ``note#parent_directory@directory`` and
        ``attachment#parent_note@note`` to gather the notes and
        attachments that live inside the subtree.

        When ``exclusive`` is ``True`` (the default) a note or
        attachment is included only when its **only** parent relation
        points back into the resolved subtree.  Notes parented under
        any directory outside the subtree, and attachments parented
        under any note outside the subtree, are filtered out so the
        caller never deletes something shared with a sibling tree.

        Args:
            directory_id: id of the root directory.
            max_depth: recursion cap for the directory subtree;
                ``0`` means only the root directory itself.
            exclusive: when ``True``, drop notes/attachments that
                have a parent outside the subtree.

        Returns:
            :class:`ResolvedChildren`: the discovered ids.

        Raises:
            ValueError: ``max_depth`` is negative.
        """
        ...


@dataclass
class ResolvedChildren:
    """Subtree-resolved ids returned by
    :meth:`PermissionRepoABC.resolve_children`.

    Attributes:
        sub_directory_ids: directory ids reachable from the root
            via ``directory#parent@directory``, **including** the
            root directory itself.
        note_ids: note ids whose only ``parent_directory`` points
            into ``sub_directory_ids`` (when ``exclusive=True``).
        attachment_ids: attachment ids whose only ``parent_note``
            points into ``note_ids`` (when ``exclusive=True``).
    """

    sub_directory_ids: List[str] = field(default_factory=list)
    note_ids: List[str] = field(default_factory=list)
    attachment_ids: List[str] = field(default_factory=list)


@dataclass
class DirectoryChild:
    """One entry returned by :meth:`DirectoryService.dry_delete`.

    Attributes:
        id: id of the child resource.
        kind: one of ``"directory"``, ``"note"``, ``"attachment"``.
        name: human-readable name.  Directories use their
            ``name`` field; notes use their ``title``; attachments
            use their ``filename`` (or ``key`` when the filename is
            not set).
    """

    id: str
    kind: str
    name: str


__all__ = [
    "PermissionRepoABC",
    "ResolvedChildren",
    "DirectoryChild",
]