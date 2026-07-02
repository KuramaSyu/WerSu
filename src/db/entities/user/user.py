from dataclasses import dataclass
from typing import Optional

from src.api.undefined import UndefinedOr
from src.api.user_context import UserTypeT
from src.api.visitor import AcceptsVisitor, EntityVisitor


@dataclass
class UserEntity(AcceptsVisitor):
    discord_id: Optional[int] = None
    avatar: Optional[str] = None
    id: Optional[str] = None
    username: Optional[str] = None
    discriminator: Optional[str] = None
    email: Optional[str] = None
    type: UndefinedOr[UserTypeT] = None

    def visit(self, visitor: EntityVisitor):
        """Dispatch this user to ``visitor.visit_user``."""
        return visitor.visit_user(self)
