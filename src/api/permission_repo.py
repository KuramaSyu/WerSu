from abc import ABC, abstractmethod
from typing import *
from warnings import deprecated
from src.api.relationship import *
from src.api.user_context import UserContextABC

class PermissionRepoABC(ABC):

    @abstractmethod
    async def insert(
        self,
        relationships: List[Relationship]
    ) -> List[Relationship]:
        """inserts permission
        
        Args:
        -----
        relationships: `List[Relationship]`
            the relationships to insert. Provide full subject, permission and object.

        Returns:
        --------
        `List[Relationship]`:
            the inserted relationships
        """
        ...

    @abstractmethod
    async def delete(
        self,
        relationship: Relationship
    ) -> Relationship:
        """delete permission(s). Relationship can be partially `UNDEFINED` to set these as wildcards.
        
        Args:
        -----
        relationship: `Relationship`
            The relationship to delete. Out of this, a filter will be built, where all `UNDEFINDED` values of the given `relation` will
            be treated as wildcards. E.g. you could provide attachment#*@user will delete all permissions for all users for this attachment

        Returns:
        --------
        `List[Relationship]`:
            the deleted relationships
        """
        ...

    @deprecated("lookup() is deprecated, use lookup_relationships() instead")
    @abstractmethod
    async def lookup(
        self,
        relationship: Relationship
    ) -> List[ObjectRef]:
        """select permission
        
        Args:
        -----
        relationship: `Relationship`
            the relationship to lookup. Provide full subject and permission, and for object leave the 
            object_id to `UNDEFINED` and only provide the object_type. This allows to lookup all permissions 
            for a given subject and permission on all objects of a given type.

        Returns:
        --------
        `List[ObjectRef]`:
            the matching objects
        """
        ...

    @abstractmethod
    async def lookup_relationships(
        self,
        relationship: Relationship,
    ) -> List[Relationship]:
        """Select stored direct relationships by a relationship-shaped filter.

        Args:
        -----
        relationship: `Relationship`
            filter where `UNDEFINED` values act as wildcards for ids.

        Returns:
        --------
        `List[Relationship]`:
            matching stored direct relationships.
        """
        ...

    @abstractmethod
    async def lookup_notes(
        self,
        user: UserContextABC,
        permission: str
    ) -> List[ObjectRef]:
        """Retrieves all notes where the given user has the given permission
        
        Args:
        -----
        user: `UserContextABC`
            the user context to lookup permissions for
        permission: `str`
            the permission to lookup

        Returns:
        --------
        `List[ObjectRef]`:
            the matching objects
        """
        ...

    @abstractmethod
    async def list_relationships(self, resource: ObjectRef) -> List[Relationship]:
        """List stored relationships for a specific resource.

        Parameters
        ----------
        resource : ObjectRef
            Resource whose direct relationships should be returned.
            If `object_id` is `UNDEFINED`, all relationships for that
            resource type are returned.

        Returns
        -------
        List[Relationship]
            Stored relationships for `resource` including relation and subject.
        """
        ...

    @abstractmethod
    async def has_permission(
        self,
        user: UserContextABC,
        permission: str,
        resource: ObjectRef,
    ) -> bool:
        """Check whether a user has a permission on a resource.

        Parameters
        ----------
        user : UserContextABC
            Current user context.
        permission : str
            Permission to verify.
        resource : ObjectRef
            Resource to check against.

        Returns
        -------
        bool
            True if permission is granted.
        """
        ...

    @abstractmethod
    async def check(
        self, 
        relationship: Relationship
    ) -> bool:
        """classical check for permissions

        Parameters:
        -----------
        relationship: Relationship
            the obj#relation@subj relationship
        
        Returns:
        --------
        bool:
            whether this relationship evaluates to True or not
        """
        ...

    @abstractmethod
    async def get_permissions(
        self,
        user: UserContextABC,
        resource: ObjectRef,
    ) -> List[str]:
        """List effective permissions for a user on a resource.

        Parameters
        ----------
        user : UserContextABC
            Current user context.
        resource : ObjectRef
            Resource to evaluate.

        Returns
        -------
        List[str]
            Granted permissions.
        """
        ...