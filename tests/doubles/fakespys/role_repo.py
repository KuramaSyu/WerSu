from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from src.api.other.undefined import UNDEFINED, UndefinedOr, is_undefined
from src.api.other.user_context import UserContextABC
from src.db.entities.user.role import RoleEntity, RoleFilter, UserRoleMembershipEntity
from typing import Dict, List, Optional
import uuid


@dataclass
class _FakeSpyRoleRepo:
    """In-process stand-in for :class:`RoleRepoABC`."""

    _roles: Dict[str, RoleEntity] = field(default_factory=dict)
    create_calls: List[RoleEntity] = field(default_factory=list)
    update_calls: List[RoleEntity] = field(default_factory=list)
    delete_calls: List[str] = field(default_factory=list)
    add_calls: List[tuple[str, str]] = field(default_factory=list)
    remove_calls: List[tuple[str, str]] = field(default_factory=list)

    async def create_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:

        if is_undefined(role.name):
            raise ValueError("role.name is required")
        new_id = str(uuid.uuid4())
        self._roles[new_id] = RoleEntity(
            id=new_id,
            name=str(role.name),
            description=role.description if not is_undefined(role.description) else UNDEFINED,
            created_at=datetime.now(),
        )
        self.create_calls.append(self._roles[new_id])
        return self._roles[new_id]

    async def update_role(self, role: RoleEntity, ctx: UserContextABC) -> RoleEntity:
        self.update_calls.append(role)
        existing = self._roles.get(str(role.id))
        if existing is None:
            raise ValueError(f"Role not found: {role.id}")
        merged = RoleEntity(
            id=existing.id,
            name=str(role.name) if not is_undefined(role.name) else existing.name,
            description=role.description if not is_undefined(role.description) else existing.description,
            created_at=existing.created_at,
        )
        self._roles[str(role.id)] = merged
        return merged

    async def delete_role(self, role_id: str, ctx: UserContextABC) -> None:
        self.delete_calls.append(role_id)
        if role_id not in self._roles:
            raise ValueError(f"Role not found: {role_id}")
        del self._roles[role_id]

    async def get_role(
        self,
        role_id: str,
        ctx: UserContextABC,
    ) -> Optional[RoleEntity]:
        return self._roles.get(role_id)

    async def get_roles(
        self,
        filter: RoleFilter,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        out = list(self._roles.values())
        if not is_undefined(filter.name):
            out = [r for r in out if r.name == filter.name]
        return out

    async def add_user_to_role(
        self,
        user_id: str,
        role_id: str,
        ctx: UserContextABC,
    ) -> UserRoleMembershipEntity:
        self.add_calls.append((user_id, role_id))
        return UserRoleMembershipEntity(
            user_id=user_id, role_id=role_id, granted_at=UNDEFINED
        )

    async def remove_user_from_role(
        self,
        user_id: str,
        role_id: str,
        ctx: UserContextABC,
    ) -> None:
        self.remove_calls.append((user_id, role_id))

    async def get_roles_for_user(
        self,
        user_id: str,
        ctx: UserContextABC,
    ) -> List[RoleEntity]:
        return list(self._roles.values())

    async def get_users_for_role(
        self,
        role_id: str,
        ctx: UserContextABC,
    ) -> List[UserRoleMembershipEntity]:
        return []


__all__ = [
    '_FakeSpyRoleRepo',
]
