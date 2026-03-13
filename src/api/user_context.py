from abc import ABC, abstractmethod
from typing import Any

class UserContextABC(ABC):
    @abstractmethod
    def get_user_id(self) -> str:
        """Returns the user ID of the current user"""
        ...


