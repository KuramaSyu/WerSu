from dataclasses import dataclass
from typing import Optional


@dataclass
class UserEntity:
    discord_id: int
    avatar: Optional[str] = None
    id: Optional[str] = None
    username: Optional[str] = None
    discriminator: Optional[str] = None
    email: Optional[str] = None
