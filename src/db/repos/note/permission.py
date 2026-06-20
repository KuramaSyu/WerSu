"""Compatibility exports for older tests and imports.

The permission repo implementation lives in `src.db.repos.permissions`.
This module keeps the historical `src.db.repos.note.permission` import path
working without duplicating permission logic.
"""

from src.api.relationship import DirectoryRelationEnum, NoteRelationEnum, ObjectTypeEnum
from src.db.repos.permissions.permission import NotePermissionRepoInMemory, NotePermissionRepoSpicedb

__all__ = [
    "DirectoryRelationEnum",
    "NotePermissionRepoInMemory",
    "NotePermissionRepoSpicedb",
    "NoteRelationEnum",
    "ObjectTypeEnum",
]
