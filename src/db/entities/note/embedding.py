from dataclasses import dataclass
from typing import Sequence


@dataclass
class NoteEmbeddingEntity:
    """Represents one record of note.embedding which contains the model which craeted the embedding,
    the embedding and the note it belongs to"""
    note_id: int
    model: str
    embedding: Sequence[float]
