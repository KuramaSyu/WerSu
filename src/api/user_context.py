from abc import ABC, abstractmethod
from typing import Any

class UserContextABC(ABC):
    @abstractmethod
    def lookp_subjects(action: str, subjecttype: str, subjectid: str) -> list[Any]:
        """Lookup subjects for a given action, subject type, and subject ID.
        
        Example
        --------
        Lookup for user:alice#view -> `lookp_subjects("view", "doc", "notebook1")` -> ["user:alice", "user:bob"]
        """
        pass

    @abstractmethod
    def get_user_id(self) -> str:
        """Get the user ID of the current user."""
        pass

    @abstractmethod
    def lookup_notes(self, action: str) -> list[Any]:
        """Lookup notes for a given action for the current user using `get_user_id()`.
        
        Example
        --------
        Lookup for view -> `lookup_notes("view")` -> ["note1", "note2"]
        """
        pass

    @abstractmethod
    def add_subject(self, subject: str, subjectid: str) -> None:
        """Add a subject to the user context.
        
        Example
        --------
        Add doc:notebook1 -> `add_subject("doc", "notebook1")` adds the subject notebook1 for the current user using `get_user_id()`.
        """
        pass


