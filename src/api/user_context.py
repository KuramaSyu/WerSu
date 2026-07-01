"""Abstract base for the caller identity passed into the service layer.

The :class:`UserContextABC` carries the id of the user making the
current request.  Implementations can attach more data (e.g. roles
or session info), but the contract only exposes the id.
"""

from abc import ABC, abstractmethod


class UserContextABC(ABC):
    """Identity of the caller for a single request.

    Implementations:
    * :class:`src.db.repos.note.note.UserContext`
    * :class:`src.db.repos.note.note.UnimplementedUserContext`
    """

    @property
    @abstractmethod
    def user_id(self) -> str:
        """Return the id of the user making the current request."""
        ...


