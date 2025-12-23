from dataclasses import dataclass

from src.api.undefined import UndefinedOr

@dataclass
class NotePermissionEntity:
    """Represents one record of note.permission"""
    note_id: UndefinedOr[int]
    role_id: UndefinedOr[int]
