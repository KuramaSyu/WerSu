from abc import ABC, abstractmethod
from typing import List, Sequence

from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.note import NoteRepoFacadeABC, UserContext
from src.db.repos.note.permission import (
    DirectoryRelationEnum,
    NotePermissionRepo,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)


class PermissionServiceABC(ABC):
    """Application service for managing note and directory relationships.

    Notes
    -----
    Implementations are expected to validate resource shape and enforce
    authorization based on the provided actor context.
    """

    @abstractmethod
    async def list_relationships(self, resource: ObjectRef, actor: UserContextABC) -> List[Relationship]:
        """List all relationships for a resource.

        Parameters
        ----------
        resource : ObjectRef
            Resource to read relationships for.
        actor : UserContextABC
            Current user context.

        Returns
        -------
        List[Relationship]
            Relationships directly stored for the resource.

        Raises
        ------
        ValueError
            If resource data is invalid.
        LookupError
            If the resource does not exist.
        PermissionError
            If the actor is not allowed to manage permissions.
        """
        ...

    @abstractmethod
    async def create_relationship(self, relationship: Relationship, actor: UserContextABC) -> List[Relationship]:
        """Create one relationship and return the updated relationship set.

        Parameters
        ----------
        relationship : Relationship
            Relationship to create.
        actor : UserContextABC
            Current user context.

        Returns
        -------
        List[Relationship]
            Relationships stored for the same resource after creation.

        Raises
        ------
        ValueError
            If relationship data is invalid.
        LookupError
            If the resource does not exist.
        PermissionError
            If the actor is not allowed to manage permissions.
        """
        ...

    @abstractmethod
    async def delete_relationship(self, relationship: Relationship, actor: UserContextABC) -> List[Relationship]:
        """Delete one relationship and return the updated relationship set.

        Parameters
        ----------
        relationship : Relationship
            Relationship to delete.
        actor : UserContextABC
            Current user context.

        Returns
        -------
        List[Relationship]
            Relationships stored for the same resource after deletion.

        Raises
        ------
        ValueError
            If relationship data is invalid.
        LookupError
            If the resource does not exist.
        PermissionError
            If the actor is not allowed to manage permissions.
        """
        ...

    @abstractmethod
    async def replace_relationships(
        self,
        resource: ObjectRef,
        relationships: Sequence[Relationship],
        actor: UserContextABC,
    ) -> List[Relationship]:
        """Replace all resource relationships with the provided list.

        Parameters
        ----------
        resource : ObjectRef
            Resource whose direct relationships should be replaced.
        relationships : Sequence[Relationship]
            Full relationship set that should remain after replacement.
        actor : UserContextABC
            Current user context.

        Returns
        -------
        List[Relationship]
            Relationships stored for `resource` after replacement.

        Raises
        ------
        ValueError
            If resource or relationship data is invalid.
        LookupError
            If the resource does not exist.
        PermissionError
            If the actor is not allowed to manage permissions.
        """
        ...


class PermissionServiceRepo(PermissionServiceABC):
    """Repository-backed permission service with authorization checks."""

    _note_user_relations = {
        NoteRelationEnum.OWNER,
        NoteRelationEnum.ADMIN,
        NoteRelationEnum.WRITER,
        NoteRelationEnum.READER,
    }
    _note_directory_relations = {NoteRelationEnum.PARENT_DIRECTORY}

    _directory_user_relations = {
        DirectoryRelationEnum.ADMIN,
        DirectoryRelationEnum.WRITER,
        DirectoryRelationEnum.READER,
    }
    _directory_directory_relations = {DirectoryRelationEnum.PARENT}

    def __init__(
        self,
        permission_repo: NotePermissionRepo,
        note_repo: NoteRepoFacadeABC,
        directory_repo: DirectoryRepo,
    ) -> None:
        self._permission_repo = permission_repo
        self._note_repo = note_repo
        self._directory_repo = directory_repo

    async def list_relationships(self, resource: ObjectRef, actor: UserContextABC) -> List[Relationship]:
        resource = self._validate_resource(resource)
        await self._assert_resource_exists(resource, actor)
        await self._assert_can_manage_permissions(resource, actor)
        return self._sort_relationships(await self._permission_repo.list_relationships(resource))

    async def create_relationship(self, relationship: Relationship, actor: UserContextABC) -> List[Relationship]:
        resource = self._validate_resource(relationship.resource)
        await self._assert_resource_exists(resource, actor)
        await self._assert_can_manage_permissions(resource, actor)

        normalized = self._normalize_relationship(relationship, expected_resource=resource)
        await self._permission_repo.insert([normalized])
        return self._sort_relationships(await self._permission_repo.list_relationships(resource))

    async def delete_relationship(self, relationship: Relationship, actor: UserContextABC) -> List[Relationship]:
        resource = self._validate_resource(relationship.resource)
        await self._assert_resource_exists(resource, actor)
        await self._assert_can_manage_permissions(resource, actor)

        normalized = self._normalize_relationship(relationship, expected_resource=resource)
        await self._permission_repo.delete(normalized)
        return self._sort_relationships(await self._permission_repo.list_relationships(resource))

    async def replace_relationships(
        self,
        resource: ObjectRef,
        relationships: Sequence[Relationship],
        actor: UserContextABC,
    ) -> List[Relationship]:
        resource = self._validate_resource(resource)
        await self._assert_resource_exists(resource, actor)
        await self._assert_can_manage_permissions(resource, actor)

        normalized_desired = [
            self._normalize_relationship(rel, expected_resource=resource) for rel in relationships
        ]

        current = await self._permission_repo.list_relationships(resource)
        # Use stable tuple keys to diff relationships without nested comparisons.
        desired_keys = {self._relationship_key(rel) for rel in normalized_desired}
        current_keys = {self._relationship_key(rel) for rel in current}

        # Remove relationships that should no longer exist.
        for rel in current:
            if self._relationship_key(rel) not in desired_keys:
                await self._permission_repo.delete(rel)

        # Insert only missing relationships; existing entries are left untouched.
        to_create = [rel for rel in normalized_desired if self._relationship_key(rel) not in current_keys]
        if to_create:
            await self._permission_repo.insert(to_create)

        return self._sort_relationships(await self._permission_repo.list_relationships(resource))

    def _validate_resource(self, resource: ObjectRef) -> ObjectRef:
        """Validate and normalize a managed resource reference.

        Raises
        ------
        ValueError
            If object type is unsupported or object ID is missing.
        """
        object_type = ObjectTypeEnum(str(resource.object_type))
        object_id = resource.object_id
        if object_id in (UNDEFINED, None):
            raise ValueError("resource.object_id is required")

        if object_type not in {ObjectTypeEnum.NOTE, ObjectTypeEnum.DIRECTORY}:
            raise ValueError(f"Unsupported resource type: {object_type}")

        return ObjectRef(object_type=object_type, object_id=str(object_id))

    async def _assert_resource_exists(self, resource: ObjectRef, actor: UserContextABC) -> None:
        """Ensure the target resource exists before mutating relations.

        Raises
        ------
        LookupError
            If the target note or directory cannot be found.
        """
        object_id = str(resource.object_id)
        if resource.object_type == ObjectTypeEnum.NOTE:
            note = await self._note_repo.select_by_id(object_id, UserContext(actor.user_id))
            if note is None:
                raise LookupError(f"Note not found: {object_id}")
            return

        directory = await self._directory_repo.fetch_directory(object_id)
        if directory is None:
            raise LookupError(f"Directory not found: {object_id}")

    async def _assert_can_manage_permissions(self, resource: ObjectRef, actor: UserContextABC) -> None:
        """Ensure the actor can manage permissions on the resource.

        Raises
        ------
        PermissionError
            If the actor lacks permission management rights.
        """
        # Prefer checking effective permission first.
        if await self._permission_repo.has_permission(actor, "write", resource):
            return

        # Fallback to direct elevated relations (owner/admin/writer).
        relationships = await self._permission_repo.list_relationships(resource)
        for rel in relationships:
            if str(rel.subject.object_type) != ObjectTypeEnum.USER:
                continue
            if str(rel.subject.object_id) != actor.user_id:
                continue

            if str(rel.relation) in {
                str(NoteRelationEnum.OWNER),
                str(NoteRelationEnum.ADMIN),
                str(NoteRelationEnum.WRITER),
                str(DirectoryRelationEnum.ADMIN),
                str(DirectoryRelationEnum.WRITER),
            }:
                return

        raise PermissionError("User is not allowed to manage permissions for this resource")

    def _normalize_relationship(
        self,
        relationship: Relationship,
        expected_resource: ObjectRef | None = None,
    ) -> Relationship:
        """Validate and normalize one relationship payload.

        Raises
        ------
        ValueError
            If relation, resource, or subject values are invalid.
        """
        resource = self._validate_resource(relationship.resource)
        if expected_resource and (
            str(resource.object_type) != str(expected_resource.object_type)
            or str(resource.object_id) != str(expected_resource.object_id)
        ):
            raise ValueError("Relationship resource does not match target resource")

        subject_type = ObjectTypeEnum(str(relationship.subject.object_type))
        subject_id = relationship.subject.object_id
        if subject_id in (UNDEFINED, None):
            raise ValueError("relationship.subject.object_id is required")

        relation_name = str(relationship.relation)

        if resource.object_type == ObjectTypeEnum.NOTE:
            # Notes allow user roles and one directory-link relation.
            relation_enum = NoteRelationEnum(relation_name)
            if relation_enum in self._note_user_relations and subject_type == ObjectTypeEnum.USER:
                return Relationship(
                    resource=resource,
                    relation=relation_enum,
                    subject=SubjectRef(object_type=subject_type, object_id=str(subject_id)),
                )
            if relation_enum in self._note_directory_relations and subject_type == ObjectTypeEnum.DIRECTORY:
                return Relationship(
                    resource=resource,
                    relation=relation_enum,
                    subject=SubjectRef(object_type=subject_type, object_id=str(subject_id)),
                )

            raise ValueError(
                "Invalid note relationship: relation/subject combination is not allowed"
            )

        # Directories allow user roles plus an optional parent directory relation.
        relation_enum = DirectoryRelationEnum(relation_name)
        if relation_enum in self._directory_user_relations and subject_type == ObjectTypeEnum.USER:
            return Relationship(
                resource=resource,
                relation=relation_enum,
                subject=SubjectRef(object_type=subject_type, object_id=str(subject_id)),
            )
        if relation_enum in self._directory_directory_relations and subject_type == ObjectTypeEnum.DIRECTORY:
            return Relationship(
                resource=resource,
                relation=relation_enum,
                subject=SubjectRef(object_type=subject_type, object_id=str(subject_id)),
            )

        raise ValueError("Invalid directory relationship: relation/subject combination is not allowed")

    @staticmethod
    def _relationship_key(rel: Relationship) -> tuple[str, str, str, str, str]:
        """Build a stable tuple key for relationship comparisons."""
        return (
            str(rel.resource.object_type),
            str(rel.resource.object_id),
            str(rel.relation),
            str(rel.subject.object_type),
            str(rel.subject.object_id),
        )

    def _sort_relationships(self, relationships: Sequence[Relationship]) -> List[Relationship]:
        """Return relationships sorted deterministically."""
        return sorted(relationships, key=self._relationship_key)