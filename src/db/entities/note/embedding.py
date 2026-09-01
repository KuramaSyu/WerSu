from dataclasses import dataclass
from typing import List

from src.api.other.undefined import *
from src.utils import str_vec_to_list


@dataclass
class NoteEmbeddingEntity:
    """Represents one record of note.embedding which contains the model which craeted the embedding,
    the embedding and the note it belongs to"""
    note_id: UndefinedOr[str]
    model: UndefinedOr[str]
    embedding: UndefinedOr[List[float]]

    def __post_init__(self):
        if isinstance(self.embedding, str):
            # embeddings are strings in DB, hence a conversion here
            self.embedding = str_vec_to_list(self.embedding)




