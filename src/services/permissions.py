from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

from src.api import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    PermissionRepoABC,
    Relationship,
    SubjectRef,
    UserContextABC,
)
from src.api.facades.note_facade import NoteFacadeABC
from src.api.other.relationship import RelationName
from src.api.other.types import LoggingProvider
from src.api.other.undefined import UNDEFINED
from src.api.repos.permission_audit_repo import PermissionAuditRepoABC
from src.api.repos.role_repo import RoleRepoABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.db.entities.permission_audit import PermissionAuditEntity
from src.db.repos.directory.directory_facade import DirectoryFacadeABC
from src.domain.permission_chain import HasResourceAdminPerm
from src.domain.share_policy import GrantPermissionFactory
from src.utils import logging_provider as default_logging_provider


class PermissionServiceABC(ABC):
    """Application service for managing resource sharing relationships.

    Supported resources are notes, directories, shelves and roles.  The
    share API writes access relations (``reader`` / ``writer`` / ``admin``)
    and capability relations (``grant_*`` / ``delegate_*``); the internal
    ``owner`` relation is never writable through this service.
    """

    @abstractmethod
    async def list_relationships(self, resource: ObjectRef, actor: UserContextABC) -> List[Relationship]:
        """List all direct relationships for a resource.

        Requires ``has_admin`` on the resource (notes, directories,
        shelves and roles all define it).
        """
        ...

    @abstractmethod
    async def create_relationship(self, relationship: Relationship, actor: UserContextABC) -> List[Relationship]:
        """Create one relationship and return the updated relationship set.

        Structural hierarchy tuples (``note#parent_directory`` and
        ``directory#parent``) and ``owner`` are rejected.
        """
        ...

    @abstractmethod
    async def delete_relationship(self, relationship: Relationship, actor: UserContextABC) -> List[Relationship]:
        """Delete one relationship and return the updated relationship set."""
        ...

    @abstractmethod
    async def replace_relationships(
        self,
        resource: ObjectRef,
        relationships: Sequence[Relationship],
        actor: UserContextABC,
    ) -> List[Relationship]:
        """Replace all *shareable* relationships with the provided list.

        Owner tuples are protected: they are never accepted in the desired
        set and are never removed by this operation.
        """
        ...


class PermissionServiceImpl(PermissionServiceABC):
    """Repository-backed permission service with capability checks."""

    _structural_message = (
        "parent relations must be changed via directory or note patch, "
        "not the permission service"
    )

    _role_relations = {"grant_member", "delegate_member"}

    @classmethod
    def _is_structural_relation(cls, relationship: Relationship) -> bool:
        """Return True when ``relationship`` is a structural (hierarchy) tuple."""
        resource_type = str(relationship.resource.object_type)
        subject_type = str(relationship.subject.object_type)
        relation = str(relationship.relation)

        if resource_type == str(ObjectTypeEnum.NOTE):
            return (
                relation == str(NoteRelationEnum.PARENT_DIRECTORY)
                and subject_type == str(ObjectTypeEnum.DIRECTORY)
            )
        if resource_type == str(ObjectTypeEnum.DIRECTORY):
            return (
                relation == str(DirectoryRelationEnum.PARENT)
                and subject_type
                in {str(ObjectTypeEnum.DIRECTORY), str(ObjectTypeEnum.SHELF)}
            )
        return False

    def __init__(
        self,
        permission_repo: PermissionRepoABC,
        note_repo: NoteFacadeABC,
        directory_repo: DirectoryFacadeABC,
        shelf_repo: Optional[ShelfRepoABC] = None,
        role_repo: Optional[RoleRepoABC] = None,
        grant_factory: Optional[GrantPermissionFactory] = None,
        audit_repo: Optional[PermissionAuditRepoABC] = None,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._permission_repo = permission_repo
        self._note_repo = note_repo
        self._directory_repo = directory_repo
        self._shelf_repo = shelf_repo
        self._role_repo = role_repo
        self._grant_factory = grant_factory or GrantPermissionFactory()
        self._audit_repo = audit_repo
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    async def list_relationships(self, resource: ObjectRef, actor: UserContextABC) -> List[Relationship]:
        resource = self._validate_resource(resource)
        await self._assert_resource_exists(resource, actor)
        await self._assert_admin(resource, actor)
        return self._sort_relationships(await self._permission_repo.list_relationships(resource))

    async def create_relationship(self, relationship: Relationship, actor: UserContextABC) -> List[Relationship]:
        resource = self._validate_resource(relationship.resource)
        await self._assert_resource_exists(resource, actor)

        normalized = self._normalize_relationship(relationship, expected_resource=resource)
        await self._assert_can_write_relation(resource, actor, normalized.relation)
        await self._permission_repo.insert([normalized])
        await self._audit(actor, "grant", [normalized])
        return self._sort_relationships(await self._permission_repo.list_relationships(resource))

    async def delete_relationship(self, relationship: Relationship, actor: UserContextABC) -> List[Relationship]:
        resource = self._validate_resource(relationship.resource)
        await self._assert_resource_exists(resource, actor)

        normalized = self._normalize_relationship(relationship, expected_resource=resource)
        await self._assert_can_write_relation(resource, actor, normalized.relation)
        await self._permission_repo.delete(normalized)
        await self._audit(actor, "revoke", [normalized])
        return self._sort_relationships(await self._permission_repo.list_relationships(resource))

    async def replace_relationships(
        self,
        resource: ObjectRef,
        relationships: Sequence[Relationship],
        actor: UserContextABC,
    ) -> List[Relationship]:
        resource = self._validate_resource(resource)
        await self._assert_resource_exists(resource, actor)

        normalized_desired = [
            self._normalize_relationship(rel, expected_resource=resource) for rel in relationships
        ]

        current = await self._permission_repo.list_relationships(resource)
        for rel in (*current, *normalized_desired):
            if self._is_structural_relation(rel):
                raise ValueError(self._structural_message)

        # Owner tuples are never part of the replacement set and are never
        # removed by it.  The normalization above already rejects a desired
        # tuple whose relation is ``owner``.
        current_diffable = [
            rel for rel in current if str(rel.relation) != "owner"
        ]

        desired_keys = {self._relationship_key(rel) for rel in normalized_desired}
        current_keys = {self._relationship_key(rel) for rel in current_diffable}

        to_remove = [
            rel for rel in current_diffable
            if self._relationship_key(rel) not in desired_keys
        ]
        to_create = [
            rel for rel in normalized_desired
            if self._relationship_key(rel) not in current_keys
        ]

        # Authorize the whole diff before touching storage so a failure
        # later in the list cannot leave a partial replacement behind.
        for rel in to_remove:
            await self._assert_can_write_relation(resource, actor, rel.relation)
        for rel in to_create:
            await self._assert_can_write_relation(resource, actor, rel.relation)

        for rel in to_remove:
            await self._permission_repo.delete(rel)
        if to_create:
            await self._permission_repo.insert(to_create)

        if to_remove:
            await self._audit(actor, "revoke", to_remove)
        if to_create:
            await self._audit(actor, "grant", to_create)

        return self._sort_relationships(await self._permission_repo.list_relationships(resource))

    def _validate_resource(self, resource: ObjectRef) -> ObjectRef:
        """Validate and normalize a managed resource reference."""
        try:
            object_type = ObjectTypeEnum(str(resource.object_type))
        except ValueError as exc:
            raise ValueError(f"Unsupported resource type: {resource.object_type}") from exc

        object_id = resource.object_id
        if object_id in (UNDEFINED, None):
            raise ValueError("resource.object_id is required")

        if object_type not in {
            ObjectTypeEnum.NOTE,
            ObjectTypeEnum.DIRECTORY,
            ObjectTypeEnum.SHELF,
            ObjectTypeEnum.ROLE,
        }:
            raise ValueError(f"Unsupported resource type: {object_type}")

        return ObjectRef(object_type=object_type, object_id=str(object_id))

    async def _assert_resource_exists(self, resource: ObjectRef, actor: UserContextABC) -> None:
        """Ensure the target resource exists before mutating relations."""
        object_id = str(resource.object_id)

        if resource.object_type == ObjectTypeEnum.NOTE:
            note = await self._note_repo.select_by_id(object_id, actor)
            if note is None:
                raise LookupError(f"Note not found: {object_id}")
            return

        if resource.object_type == ObjectTypeEnum.DIRECTORY:
            directory = await self._directory_repo.fetch_directory(object_id)
            if directory is None:
                raise LookupError(f"Directory not found: {object_id}")
            return

        if resource.object_type == ObjectTypeEnum.SHELF:
            if self._shelf_repo is None:
                raise RuntimeError("shelf_repo is required to manage shelf permissions")
            shelf = await self._shelf_repo.fetch_shelf(object_id)
            if shelf is None:
                raise LookupError(f"Shelf not found: {object_id}")
            return

        if resource.object_type == ObjectTypeEnum.ROLE:
            if self._role_repo is None:
                raise RuntimeError("role_repo is required to manage role permissions")
            role = await self._role_repo.get_role(object_id, actor)
            if role is None:
                raise LookupError(f"Role not found: {object_id}")
            return

        raise ValueError(f"Unsupported resource type: {resource.object_type}")

    async def _assert_admin(self, resource: ObjectRef, actor: UserContextABC) -> None:
        """Authorize reading a resource's full relationship set."""
        chain = HasResourceAdminPerm(
            resource.object_type, str(resource.object_id)
        ).set_permission_repo(self._permission_repo)
        result = await chain.check(actor)
        if not result:
            raise PermissionError(chain.error)

    async def _assert_can_write_relation(
        self,
        resource: ObjectRef,
        actor: UserContextABC,
        relation: RelationName | str,
    ) -> None:
        """Authorize writing ``relation`` using the explicit capability policy."""
        chain = self._grant_factory.for_relation(
            resource.object_type, str(resource.object_id), relation
        ).set_permission_repo(self._permission_repo)
        result = await chain.check(actor)
        if not result:
            raise PermissionError(chain.error)

    def _normalize_relationship(
        self,
        relationship: Relationship,
        expected_resource: ObjectRef | None = None,
    ) -> Relationship:
        """Validate and normalize one relationship payload."""
        if self._is_structural_relation(relationship):
            raise ValueError(self._structural_message)

        resource = self._validate_resource(relationship.resource)
        if expected_resource and (
            str(resource.object_type) != str(expected_resource.object_type)
            or str(resource.object_id) != str(expected_resource.object_id)
        ):
            raise ValueError("Relationship resource does not match target resource")

        try:
            subject_type = ObjectTypeEnum(str(relationship.subject.object_type))
        except ValueError as exc:
            raise ValueError(
                f"Unsupported subject type: {relationship.subject.object_type}"
            ) from exc
        if subject_type not in {ObjectTypeEnum.USER, ObjectTypeEnum.ROLE}:
            raise ValueError(
                "Invalid subject: expected user or role"
            )

        subject_id = relationship.subject.object_id
        if subject_id in (UNDEFINED, None):
            raise ValueError("relationship.subject.object_id is required")

        optional_relation = relationship.subject.optional_relation
        if subject_type == ObjectTypeEnum.USER:
            if optional_relation not in (None, UNDEFINED):
                raise ValueError("user subjects must not carry an optional_relation")
            normalized_optional = None
        else:
            if optional_relation in (None, UNDEFINED):
                normalized_optional = "member"
            elif str(optional_relation) == "member":
                normalized_optional = "member"
            else:
                raise ValueError(
                    "role subjects only support optional_relation='member'"
                )

        relation_name = str(relationship.relation)
        policy = self._grant_factory.policy_for(resource.object_type)
        if relation_name not in policy.capability_by_relation:
            if relation_name == "owner":
                raise ValueError("owner is not writable through the share API")
            raise ValueError(
                f"relation {relation_name!r} is not supported for "
                f"{resource.object_type}"
            )

        return Relationship(
            resource=resource,
            relation=relation_name,  # type: ignore[arg-type]
            subject=SubjectRef(
                object_type=subject_type,
                object_id=str(subject_id),
                optional_relation=(
                    normalized_optional  # type: ignore[arg-type]
                ),
            ),
        )

    async def _audit(
        self,
        actor: UserContextABC,
        action: str,
        relationships: Sequence[Relationship],
    ) -> None:
        """Best-effort audit sidecar; never fails an already-committed mutation."""
        if self._audit_repo is None:
            return

        actor_id = getattr(actor, "user_id", None)
        accessed_as = str(getattr(actor, "accessed_as", "user") or "user")
        entries = [
            PermissionAuditEntity(
                actor_id=actor_id,
                accessed_as=accessed_as,  # type: ignore[arg-type]
                action=action,  # type: ignore[arg-type]
                resource_type=str(rel.resource.object_type),
                resource_id=str(rel.resource.object_id),
                relation=str(rel.relation),
                subject_type=str(rel.subject.object_type),
                subject_id=str(rel.subject.object_id),
                subject_relation=(
                    str(rel.subject.optional_relation)
                    if rel.subject.optional_relation
                    else None
                ),
                metadata={},
            )
            for rel in relationships
        ]

        try:
            await self._audit_repo.record_many(entries)
        except Exception as exc:  # noqa: BLE001 - audit is best-effort
            self.log.warning(
                f"Failed to record permission audit for {action}: {exc}"
            )

    @staticmethod
    def _relationship_key(rel: Relationship) -> tuple[str, str, str, str, str, str]:
        """Build a stable tuple key for relationship comparisons."""
        return (
            str(rel.resource.object_type),
            str(rel.resource.object_id),
            str(rel.relation),
            str(rel.subject.object_type),
            str(rel.subject.object_id),
            str(rel.subject.optional_relation or ""),
        )

    def _sort_relationships(self, relationships: Sequence[Relationship]) -> List[Relationship]:
        """Return relationships sorted deterministically."""
        return sorted(relationships, key=self._relationship_key)