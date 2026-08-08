"""Auth-side user entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from src.api.other.undefined import UNDEFINED, UndefinedOr
from src.api.other.user_context import UserTypeT
from src.api.other.visitor import AcceptsVisitor, EntityVisitor

from .third_party import ThirdPartyEntity


@dataclass
class UserAuthEntity(AcceptsVisitor):
    """A user as the auth layer sees them.

    Discord lives on :attr:`third_parties` (with the discriminator
    in :attr:`ThirdPartyEntity.extra_fields`); use
    :meth:`discord_id` / :meth:`discriminator` to read it.
    """

    id: UndefinedOr[str] = UNDEFINED
    avatar: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    type: UndefinedOr[UserTypeT] = UNDEFINED
    third_parties: List[ThirdPartyEntity] = field(default_factory=list)

    def third_party(self, provider: str) -> Optional[ThirdPartyEntity]:
        """Return the linked :class:`ThirdPartyEntity` for `provider`, or ``None``."""
        for tp in self.third_parties:
            if tp.provider == provider:
                return tp
        return None

    def discord_id(self) -> Optional[int]:
        """Return the linked Discord id, or ``None``."""
        tp = self.third_party("discord")
        if tp is None:
            return None
        try:
            return int(tp.provider_user_id)
        except (TypeError, ValueError):
            return None

    def discriminator(self) -> Optional[str]:
        """Return the user's Discord discriminator, or ``None``."""
        tp = self.third_party("discord")
        if tp is None:
            return None
        value = tp.get_extra("discriminator")
        return str(value) if value is not None else None

    def visit(self, visitor: EntityVisitor) -> Any:
        """Dispatch this user to ``visitor.visit_user_auth``."""
        return visitor.visit_user_auth(self)


__all__ = ["UserAuthEntity"]
