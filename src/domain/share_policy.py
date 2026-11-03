"""Share policy: the single mapping from share levels to SpiceDB capabilities.

SpiceDB tuples may only point at **relations**.  Every capability that can be
granted or delegated is therefore a relation; the ``can_*`` names below are
computed permissions and are only ever used with ``has_permission``.

The policy in this module is intentionally declarative: the service layer maps
``(resource, level)`` -> relation -> capability and the factory returns a
generic :class:`HasCapabilityPerm`.  No authorization matrix lives in Python;
the SpiceDB schema remains the source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Optional

from src.api.other.relationship import ObjectTypeEnum, RelationName


class ShareLevel(StrEnum):
    """Levels that can be written through the share API."""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    OWNER = "owner"  # mapping only; never writable through the share API


#: ``level -> access relation`` for note/directory/shelf.
_ACCESS_LEVEL_TO_RELATION: Mapping[ShareLevel, RelationName] = {
    ShareLevel.READ: "reader",
    ShareLevel.WRITE: "writer",
    ShareLevel.ADMIN: "admin",
    ShareLevel.OWNER: "owner",
}

#: ``relation -> capability permission`` for note/directory/shelf.
_ACCESS_CAPABILITIES: Mapping[RelationName, str] = {
    "reader": "can_grant_read",
    "writer": "can_grant_write",
    "admin": "can_grant_admin",
    "grant_read": "can_delegate_read",
    "grant_write": "can_delegate_write",
    "grant_admin": "can_delegate_admin",
    "delegate_read": "can_assign_delegate_read",
    "delegate_write": "can_assign_delegate_write",
    "delegate_admin": "can_assign_delegate_admin",
}

#: Role capabilities.  Membership (``member``) itself is intentionally not
#: handled by ``PermissionService``; it stays with ``RoleService``.
_ROLE_CAPABILITIES: Mapping[RelationName, str] = {
    "grant_member": "can_delegate_member",
    "delegate_member": "can_assign_delegate_member",
}


@dataclass(frozen=True)
class ResourceSharePolicy:
    """Everything the service/factory needs to know about one resource type."""

    resource_type: ObjectTypeEnum
    relation_by_level: Mapping[ShareLevel, RelationName]
    capability_by_relation: Mapping[RelationName, str]
    supported_levels: frozenset[ShareLevel]

    def relation_for_level(self, level: ShareLevel) -> RelationName:
        """Return the access relation for ``level``.

        Raises:
            ValueError: if the resource type does not support ``level``.
        """
        if level not in self.supported_levels:
            raise ValueError(
                f"{self.resource_type.value} does not support share level "
                f"{level.value!r}"
            )
        try:
            return self.relation_by_level[level]
        except KeyError as exc:
            raise ValueError(
                f"{self.resource_type.value} has no relation for level "
                f"{level.value!r}"
            ) from exc

    def capability_for_relation(self, relation: RelationName) -> str:
        """Return the capability permission required to write ``relation``.

        Raises:
            ValueError: if ``relation`` is not writable through the share API.
        """
        try:
            return self.capability_by_relation[relation]
        except KeyError as exc:
            raise ValueError(
                f"relation {relation!r} is not supported for "
                f"{self.resource_type.value}"
            ) from exc


def _access_policy(resource_type: ObjectTypeEnum) -> ResourceSharePolicy:
    return ResourceSharePolicy(
        resource_type=resource_type,
        relation_by_level=_ACCESS_LEVEL_TO_RELATION,
        capability_by_relation=_ACCESS_CAPABILITIES,
        supported_levels=frozenset(
            {ShareLevel.READ, ShareLevel.WRITE, ShareLevel.ADMIN}
        ),
    )


#: Public registry consumed by :class:`GrantPermissionFactory`.
SHARE_POLICIES: Mapping[ObjectTypeEnum, ResourceSharePolicy] = {
    ObjectTypeEnum.NOTE: _access_policy(ObjectTypeEnum.NOTE),
    ObjectTypeEnum.DIRECTORY: _access_policy(ObjectTypeEnum.DIRECTORY),
    ObjectTypeEnum.SHELF: _access_policy(ObjectTypeEnum.SHELF),
    ObjectTypeEnum.ROLE: ResourceSharePolicy(
        resource_type=ObjectTypeEnum.ROLE,
        relation_by_level={},
        capability_by_relation=_ROLE_CAPABILITIES,
        supported_levels=frozenset(),
    ),
}


class GrantPermissionFactory:
    """Translate a share relation/level into a permission-chain leaf.

    The factory is deliberately strict: unknown resources, unknown relations,
    the internal ``owner`` relation, and unsupported levels all raise
    :class:`ValueError` instead of silently falling back to a weaker check.
    """

    def __init__(
        self,
        policies: Optional[Mapping[ObjectTypeEnum, ResourceSharePolicy]] = None,
    ) -> None:
        self._policies = dict(policies or SHARE_POLICIES)

    def policy_for(self, resource_type: ObjectTypeEnum | str) -> ResourceSharePolicy:
        """Return the policy for ``resource_type``.

        Raises:
            ValueError: if the resource type is not shareable.
        """
        try:
            key = ObjectTypeEnum(str(resource_type))
        except ValueError as exc:
            raise ValueError(f"unsupported resource type: {resource_type!r}") from exc
        try:
            return self._policies[key]
        except KeyError as exc:
            raise ValueError(f"unsupported resource type: {resource_type!r}") from exc

    def for_relation(
        self,
        resource_type: ObjectTypeEnum | str,
        resource_id: str,
        relation: RelationName | str,
    ):
        """Return the chain element that checks permission to write ``relation``."""
        policy = self.policy_for(resource_type)
        relation_name = str(relation)
        if relation_name == "owner":
            raise ValueError("owner is not writable through the share API")

        # ``capability_for_relation`` raises for unknown/unsupported relations.
        capability = policy.capability_for_relation(relation_name)  # type: ignore[arg-type]

        # Imported lazily to keep ``share_policy`` free of a import-time cycle
        # with ``permission_chain``.
        from src.domain.permission_chain import HasCapabilityPerm

        return HasCapabilityPerm(policy.resource_type, str(resource_id), capability)

    def for_access_level(
        self,
        resource_type: ObjectTypeEnum | str,
        resource_id: str,
        level: ShareLevel | str,
    ):
        """Return the chain element that checks permission to grant ``level``."""
        policy = self.policy_for(resource_type)
        try:
            share_level = level if isinstance(level, ShareLevel) else ShareLevel(level)
        except ValueError as exc:
            raise ValueError(f"unknown share level: {level!r}") from exc

        relation = policy.relation_for_level(share_level)
        return self.for_relation(policy.resource_type, resource_id, relation)


__all__ = [
    "GrantPermissionFactory",
    "ResourceSharePolicy",
    "SHARE_POLICIES",
    "ShareLevel",
]