from abc import ABC, abstractmethod
from typing import List

from asyncpg import Record
from db.entities import NotePermissionEntity
from db.table import TableABC

from utils import asdict


class NotePermissionRepo(ABC):

    @abstractmethod
    async def insert(
        self,
        permission: NotePermissionEntity,
    ) -> NotePermissionEntity:
        """inserts permission
        
        Args:
        -----
        permission: `NotePermissionEntity`
            the permission of a note

        Returns:
        --------
        `NotePermissionEntity`:
            the updated entity (updated ID)
        """
        ...

    @abstractmethod
    async def update(
        self,
        set: NotePermissionEntity,
        where: NotePermissionEntity,
    ) -> NotePermissionEntity:
        """updates permission
        
        Args:
        -----
        set: `NotePermissionEntity`
            the fields to update
        where: `NotePermissionEntity`
            the conditions to match

        Returns:
        --------
        `NotePermissionEntity`:
            the updated entity
        """
        ...

    @abstractmethod
    async def delete(
        self,
        permission: NotePermissionEntity,
    ) -> NotePermissionEntity:
        """delete permission
        
        Args:
        -----
        permission: `NotePermissionEntity`
            the permission of a note

        Returns:
        --------
        `NotePermissionEntity`:
            the deleted entity
        """
        ...

    @abstractmethod
    async def select(
        self,
        permission: NotePermissionEntity,
    ) -> List[NotePermissionEntity]:
        """select permission
        
        Args:
        -----
        permission: `NotePermissionEntity`
            the permission of a note to search for

        Returns:
        --------
        `List[NotePermissionEntity]`:
            the matching entities
        """
        ...


class NotePermissionPostgresRepo(NotePermissionRepo):
    """Provides an implementation using Postgres as the backend database"""
    def __init__(self, table: TableABC[List[Record]]):
        self._table = table

    async def insert(self, permission: NotePermissionEntity) -> NotePermissionEntity:
        record = await self._table.insert(asdict(permission))
        if not record:
            raise Exception("Failed to insert permission")
        return permission

    async def update(self, set: NotePermissionEntity, where: NotePermissionEntity) -> NotePermissionEntity:
        record = await self._table.update(
            set=asdict(set),
            where=asdict(where)
        )
        if not record:
            raise Exception("Failed to update permission")
        return set

    async def delete(self, permission: NotePermissionEntity) -> NotePermissionEntity:
        conditions = asdict(permission)
        if not conditions:
            raise ValueError(f"At least one field must be set to delete a permission: {permission}")
        record = await self._table.delete(
            where=conditions
        )
        if not record:
            raise Exception("Failed to delete permission")
        return permission
    
    async def select(self, permission: NotePermissionEntity) -> List[NotePermissionEntity]:
        records = await self._table.select(
            where=asdict(permission)
        )
        if not records:
            return []
        return [NotePermissionEntity(**record) for record in records]