from dataclasses import dataclass
from typing import Sequence

from src.api.undefined import *


def _str_vec_to_list(vec_str: str) -> Sequence[float]:
    vec_str = vec_str.strip().lstrip("[").rstrip("]")
    if not vec_str:
        return []
    return [float(x) for x in vec_str.split(",")]


@dataclass
class NoteEmbeddingEntity:
    """Represents one record of note.embedding which contains the model which craeted the embedding,
    the embedding and the note it belongs to"""
    note_id: str
    model: UndefinedOr[str]
    embedding: UndefinedOr[Sequence[float]]

    def __post_init__(self):
        if isinstance(self.embedding, str):
            # embeddings are strings in DB, hence a conversion here
            self.embedding = _str_vec_to_list(self.embedding)




