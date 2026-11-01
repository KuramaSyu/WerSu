from dataclasses import dataclass
from typing import Optional

from src.api.other.undefined import UNDEFINED, UndefinedOr, is_undefined
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
    display_name: Optional[str] = None
    type: UndefinedOr[UserTypeT] = UNDEFINED

    def visit(self, visitor: EntityVisitor):
        """Dispatch this user to ``visitor.visit_user``."""
        return visitor.visit_user(self)


@dataclass
class FilterUser:
    """Filter criteria for user lookups.

    Every field is :data:`UndefinedOr`, so callers can leave fields
    unset.  Set fields are AND-ed -- the filter matches a user only
    when every set field equals the user's value for that field.  An
    empty filter matches no user.

    Attributes:
        user_id: target user's id.  :obj:`UNDEFINED` (the default)
            means "don't filter on user_id".
        username: target user's username.  :obj:`UNDEFINED` means
            "don't filter on username".
    """

    user_id: UndefinedOr[str] = UNDEFINED
    username: UndefinedOr[str] = UNDEFINED

    def is_empty(self) -> bool:
        """Return ``True`` when no field is set."""
        return is_undefined(self.user_id) and is_undefined(self.username)
