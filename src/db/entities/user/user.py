from dataclasses import dataclass
from typing import Literal, Optional

from src.api.undefined import UndefinedOr


@dataclass
class UserEntity:
    discord_id: Optional[int] = None
    avatar: Optional[str] = None
    id: Optional[str] = None
    username: Optional[str] = None
    discriminator: Optional[str] = None
    email: Optional[str] = None
    user_kind: UndefinedOr[Literal["human", "temporary", "system"]] = None
