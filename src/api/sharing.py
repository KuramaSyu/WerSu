from typing import *
from abc import ABC, abstractmethod

from src.api.user_context import UserContextABC
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity

class SharingRepo(ABC):
    """
    Repo for share entities. Basically a bare wrapper to access the DB and insert/manipulate share entities.
    It does not check any permissions. The service layer is responsible for that.
    """
    @abstractmethod
    async def create_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        """
        Create a share entity for a note. For this, the provided share entity is used. UNDEFINED
        fields will be set automatically, if there is a reasonable default. Otherwise is raises an error.
        

        Raises
        --------
        `ValueError`: If the provided share entity is missing required fields or has invalid values.

        Returns
        ----------
        `NoteShareEntity`: Basically the same entity, but with ID und other UNDEFINED fields set.
        """
        ...

    @abstractmethod
    async def update_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        """
        Update a share entity for a note. For this, the provided share entity is used. UNDEFINED
        fields will not be updated. If you want to explicitly set a field to None/Null, use None instead of UNDEFINED.
        The ID field is reuired to identify the share to update. 

        Possible update fields
        -----------------------
        - description
        - online_since
        - online_until

        Raises
        --------
        `ValueError`: If the provided share entity is missing required fields or has invalid values.

        Returns
        ----------
        `NoteShareEntity`: Basically the same entity, but with updated fields.
        """
        ...

    async def get_share_by_id(self, share_id: str, ctx: UserContextABC) -> NoteShareEntity:
        """
        Fetch a share entity by ID.

        Raises
        --------
        `ValueError`: If the provided share ID is invalid or if the share does not exist.
        """
        shares = await self.get_shares_by_id([share_id], ctx)
        if not shares:
            raise ValueError(f"Share not found: {share_id}")
        return shares[0]

    @abstractmethod
    async def get_shares_by_id(self, share_ids: List[str], ctx: UserContextABC) -> List[NoteShareEntity]:
        """
        Fetch share entities by ID.

        Raises
        --------
        `ValueError`: If any provided share ID is invalid or does not exist.
        """
        ...

    async def get_share(self, filter: FilterShareNote, ctx: UserContextABC) -> NoteShareEntity:
        """
        Fetch the first share matching a filter.

        Raises
        --------
        `ValueError`: If no matching share exists.
        """
        shares = await self.get_shares(filter, ctx)
        if not shares:
            raise ValueError("Share not found")
        return shares[0]

    @abstractmethod
    async def get_shares(self, filter: FilterShareNote, ctx: UserContextABC) -> List[NoteShareEntity]:
        """
        Fetch shares matching the provided filter.
        """
        ...

    async def delete_share(self, share_id: str, ctx: UserContextABC) -> None:
        """
        Delete a share entity for a note. For this, the provided share ID is used.

        Raises
        --------
        `ValueError`: If the provided share ID is invalid or if the share does not exist.
        """
        return await self.delete_shares([share_id], ctx)

    @abstractmethod
    async def delete_shares(self, share_ids: List[str], ctx: UserContextABC) -> None:
        """
        Delete all share entities for the provided share IDs.

        Raises
        --------
        `ValueError`: If the provided share IDs are invalid.
        """
        ...





class SharingService(ABC):
    @abstractmethod
    async def create_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        """
        Create a share entity for a note. For this, the provided share entity is used. UNDEFINED
        fields will be set automatically, if there is a reasonable default. Otherwise is raises an error.
        

        Raises
        --------
        `ValueError`: If the provided share entity is missing required fields or has invalid values.

        Permissions
        ------------
        It checks, if the user has permission `edit_permissions` on the to be shared note. 

        Returns
        ----------
        `NoteShareEntity`: Basically the same entity, but with ID und other UNDEFINED fields set.
        """
        ...

    @abstractmethod
    async def update_share(self, share: NoteShareEntity, ctx: UserContextABC) -> NoteShareEntity:
        """
        Update a share entity for a note. For this, the provided share entity is used. UNDEFINED
        fields will not be updated. If you want to explicitly set a field to None/Null, use None instead of UNDEFINED.
        The ID field is reuired to identify the share to update. 

        Possible update fields
        -----------------------
        - description
        - online_since
        - online_until

        Raises
        --------
        `ValueError`: If the provided share entity is missing required fields or has invalid values.
        `PermissionError`: If the user does not have permission to update the share entity.

        Permissions
        ------------
        It checks, if the user has permission `edit_permissions` on the to be shared note.

        Returns
        ----------
        `NoteShareEntity`: Basically the same entity, but with updated fields.
        """
        ...

    async def get_share_by_id(self, share_id: str, ctx: UserContextABC) -> NoteShareEntity:
        """
        Fetch a share entity by ID.

        Permissions
        ------------
        It checks, if the user has permission `edit_permissions` on the shared note.

        Raises
        --------
        `ValueError`: If the provided share ID is invalid or if the share does not exist.
        """
        shares = await self.get_shares_by_id([share_id], ctx)
        if not shares:
            raise ValueError(f"Share not found: {share_id}")
        return shares[0]

    @abstractmethod
    async def get_shares_by_id(self, share_ids: List[str], ctx: UserContextABC) -> List[NoteShareEntity]:
        """
        Fetch share entities by ID.

        Permissions
        ------------
        Entries for notes where the user lacks `edit_permissions` are filtered out.

        Raises
        --------
        `ValueError`: If any provided share ID is invalid or does not exist.
        """
        ...

    async def get_share(self, filter: FilterShareNote, ctx: UserContextABC) -> NoteShareEntity:
        """
        Fetch the first share matching a filter.

        Raises
        --------
        `ValueError`: If no matching editable share exists.
        """
        shares = await self.get_shares(filter, ctx)
        if not shares:
            raise ValueError("Share not found")
        return shares[0]

    @abstractmethod
    async def get_shares(self, filter: FilterShareNote, ctx: UserContextABC) -> List[NoteShareEntity]:
        """
        Fetch editable shares matching the provided filter.

        Permissions
        ------------
        Entries for notes where the user lacks `edit_permissions` are filtered out.
        """
        ...

    async def delete_share(self, share_id: str, ctx: UserContextABC) -> None:
        """
        Delete a share entity for a note. For this, the provided share ID is used.

        Permissions
        ------------
        It checks, if the user has permission `edit_permissions` on the to be shared note. 

        Raises
        --------
        `ValueError`: If the provided share ID is invalid or if the share does not exist.
        `PermissionError`: If the user does not have permission to delete the share entity.
        """
        return await self.delete_shares([share_id], ctx)

    @abstractmethod
    async def delete_shares(self, share_ids: List[str], ctx: UserContextABC) -> None:
        """
        Delete all share entities for the provided share IDs.

        Permissions
        ------------
        It checks, if the user has permission `edit_permissions` on the to be shared note. 

        Raises
        --------
        `ValueError`: If the provided share IDs are invalid.
        `PermissionError`: If the user does not have permission to delete the share entities.
        """
        ...
