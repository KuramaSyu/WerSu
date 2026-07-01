"""Storage contract for the permission/relationship backend.

The :class:`PermissionRepoABC` is the only thing the service layer is
allowed to depend on for permission lookups, so any backend (SpiceDB,
an in-memory store for tests, ...) can be swapped in by providing a
new implementation of this ABC.
"""

from abc import ABC, abstractmethod
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