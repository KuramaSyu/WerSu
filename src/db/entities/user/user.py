from dataclasses import dataclass
from typing import Optional

from src.api.other.undefined import UNDEFINED, UndefinedOr
from src.api.other.user_context import UserTypeT
from src.api.other.visitor import AcceptsVisitor, EntityVisitor


@dataclass
class UserEntity(AcceptsVisitor):
    discord_id: Optional[int] = None
    avatar: Optional[str] = None
    id: UndefinedOr[str] = UNDEFINED
    username: Optional[str] = None
    discriminator: Optional[str] = None
    email: Optional[str] = None
    type: UndefinedOr[UserTypeT] = UNDEFINED

    def visit(self, visitor: EntityVisitor):
        """Dispatch this user to ``visitor.visit_user``."""
        return visitor.visit_user(self)
