from dataclasses import dataclass

from src.api.undefined import UndefinedOr

@dataclass
class NotePermissionEntity:
    """Represents one record of note.permission"""
    note_id: UndefinedOr[str]
    role_id: UndefinedOr[int]
