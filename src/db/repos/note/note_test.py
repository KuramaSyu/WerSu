from typing import Dict, Optional
from db.entities import NoteEntity
from db.repos.note.note import NoteRepoFacadeABC


class NoteTestRepo(NoteRepoFacadeABC):
    """In-memory test implementation of NoteRepoABC for testing"""
    
    def __init__(self):
        self._notes: Dict[int, NoteEntity] = {}
        self._id_counter = 1
    
    async def insert(self, note: NoteEntity) -> NoteEntity:
        """Insert a note into memory and assign an ID"""
        note.note_id = self._id_counter
        self._notes[self._id_counter] = note
        self._id_counter += 1
        return note
    
    async def update(self, note: NoteEntity) -> NoteEntity:
        """Update an existing note in memory"""
        if note.note_id is None or note.note_id not in self._notes:
            raise ValueError(f"Note with ID {note.note_id} not found")
        self._notes[note.note_id] = note
        return note
    
    async def delete(self, note: NoteEntity) -> NoteEntity:
        """Delete a note from memory"""
        if note.note_id is None or note.note_id not in self._notes:
            raise ValueError(f"Note with ID {note.note_id} not found")
        del self._notes[note.note_id]
        return note
    
    async def select(self, note: NoteEntity) -> Optional[NoteEntity]:
        """Select a note from memory by ID"""
        if note.note_id is None:
            raise ValueError("Note ID is required for select operation")
        return self._notes.get(note.note_id)
    
    def clear(self):
        """Clear all notes from memory (useful for test cleanup)"""
        self._notes.clear()
        self._id_counter = 1
