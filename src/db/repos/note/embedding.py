from abc import ABC, abstractmethod

from typing import TYPE_CHECKING, Any, List, Protocol, Sequence

from asyncpg import Record
from src.db.entities import NoteEmbeddingEntity
from src.db.table import TableABC

from src.utils import asdict


if TYPE_CHECKING:
    from src.ai.embedding_generator import EmbeddingGeneratorABC
else:
    class EmbeddingGeneratorABC(Protocol):
        @property
        def model_name(self) -> str:
            ...

        def generate(self, text: str) -> Any:
            ...


def _tensor_to_str_vec(tensor: Any) -> str:
    return f"[{','.join(str(x) for x in tensor.tolist())}]"


def _str_vec_to_list(vec_str: str) -> Sequence[float]:
    vec_str = vec_str.strip().lstrip("[").rstrip("]")
    if not vec_str:
        return []
    return [float(x) for x in vec_str.split(",")]

class NoteEmbeddingRepo(ABC):

    @abstractmethod
    async def insert(
        self,
        note_id: str,
        title: str,
        content: str,
    ) -> NoteEmbeddingEntity:
        """generates the embedding and inserts it
        
        Args:
        -----
        note_id: `str`
            the ID of the note
        title: `str`
            the note title, used to generate the embedding
        content: `str`
            the note content, used to generate the embedding

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated embedding (updated ID)
        """
        ...

    @abstractmethod
    async def update(
        self,
        set: NoteEmbeddingEntity,
        where: NoteEmbeddingEntity,
    ) -> NoteEmbeddingEntity:
        """updates embedding
        
        Args:
        -----
        embedding: `NoteEmbeddingEntity`
            the embedding of a note

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated entity
        """
        ...

    @abstractmethod
    async def delete(
        self,
        embedding: NoteEmbeddingEntity,
    ) -> NoteEmbeddingEntity:
        """delete embedding
        
        Args:
        -----
        embedding: `NoteEmbeddingEntity`
            the embedding of a note

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated entity
        """
        ...

    @abstractmethod
    async def select(
        self,
        embedding: NoteEmbeddingEntity,
    ) -> List[NoteEmbeddingEntity]:
        """select embeddings
        
        Args:
        -----
        embedding: `NoteEmbeddingEntity`
            the embedding of a note

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated entity
        """
        ...

    @property
    @abstractmethod
    def embedding_generator(self) -> EmbeddingGeneratorABC:
        """Get the embedding generator used by this repository."""
        ...

class NoteEmbeddingPostgresRepo(NoteEmbeddingRepo):
    """Provides an impementation using Postgres as the backend database"""
    def __init__(self, table: TableABC[List[Record]], embedding_generator: EmbeddingGeneratorABC):
        self._table = table
        self._embedding_generator = embedding_generator

    async def insert(self, note_id: str, title: str, content: str) -> NoteEmbeddingEntity:
        # generate embedding
        embedding_content = f"{title}\n{content}"
        embedding = self._embedding_generator.generate(embedding_content)
        embedding_str = _tensor_to_str_vec(embedding)

        # insert embedding
        record = await self._table.insert({
            "note_id": note_id,
            "model": self._embedding_generator.model_name,
            "embedding": embedding_str,
        })
        if not record:
            raise Exception("Failed to insert embedding")

        # create embedding entity
        assert len(record) > 0
        embedding = NoteEmbeddingEntity(
            note_id=record[0]["note_id"],
            model=self._embedding_generator.model_name,
            embedding=_str_vec_to_list(record[0]["embedding"]),
        )
        return embedding

    async def update(self, set: NoteEmbeddingEntity, where: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        record = await self._table.update(
            set=asdict(set),
            where=asdict(where)
        )
        if not record:
            raise Exception("Failed to update embedding")
        return set

    async def delete(self, embedding: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        conditions = asdict(embedding)
        if not conditions:
            raise ValueError(f"At least one field must be set to delete an embedding: {embedding}")
        record = await self._table.delete(
            where=conditions
        )
        if not record:
            raise Exception("Failed to delete embedding")
        return embedding
    
    async def select(self, embedding: NoteEmbeddingEntity) -> List[NoteEmbeddingEntity]:
        records = await self._table.select(
            where=asdict(embedding)
        )
        if not records:
            return []
        return [NoteEmbeddingEntity(**record) for record in records]

    @property
    def embedding_generator(self) -> EmbeddingGeneratorABC:
        return self._embedding_generator
    