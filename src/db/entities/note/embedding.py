from dataclasses import dataclass
from typing import Sequence

from src.ai.embedding_generator import EmbeddingGenerator, EmbeddingGeneratorABC
from src.api.undefined import *


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
            self.embedding = EmbeddingGeneratorABC.str_vec_to_list(self.embedding)




