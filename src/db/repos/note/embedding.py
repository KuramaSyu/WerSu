from abc import ABC, abstractmethod
import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Protocol

from asyncpg import Record
from src.api.other.undefined import UNDEFINED

from src.db.entities import NoteEmbeddingEntity
from src.db.table import TableABC
from src.api import LoggingProvider
from src.utils import logging_provider

from src.utils import asdict, str_vec_to_list, tensor_to_str_vec


if TYPE_CHECKING:
    from src.ai.embedding_generator import EmbeddingGeneratorABC
else:
    class EmbeddingGeneratorABC(Protocol):
        @property
        def model_name(self) -> str:
            ...

        def generate(self, text: str) -> Any:
            ...


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
    async def _update(
        self,
        set: NoteEmbeddingEntity,
        where: NoteEmbeddingEntity,
    ) -> NoteEmbeddingEntity:
        """updates embedding (just inserting it, given with the `set` parameter)
        
        Args:
        -----
        set: `NoteEmbeddingEntity`
            the fields to update (note_id and model should be UNDEFINED, only embedding should be set)
        where: `NoteEmbeddingEntity`
            the conditions to find the embedding to update (only note_id should be set, model and embedding should be UNDEFINED)

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated entity
        """
        ...

    @abstractmethod
    async def update(
        self,
        note_id: str,
        title: str,
        content: str,
    ) -> NoteEmbeddingEntity:
        """generates the embedding and upserts it (update if exists, insert if not)
        
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
    async def _update(
        self,
        set: NoteEmbeddingEntity,
        where: NoteEmbeddingEntity,
    ) -> NoteEmbeddingEntity:
        """updates embedding (just inserting it, given with the `set` parameter)

        Args:
        -----
        set: `NoteEmbeddingEntity`
            the fields to update (note_id and model should be UNDEFINED, only embedding should be set)
        where: `NoteEmbeddingEntity`
            the conditions to find the embedding to update (note_id and model should be set, embedding should be UNDEFINED)

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
    """Postgres-backed implementation."""
    def __init__(self, table: TableABC, embedding_generator: EmbeddingGeneratorABC, log: LoggingProvider | None = None) -> None:
        self._table = table
        self._embedding_generator = embedding_generator
        self._log = log or logging_provider(__name__, self)

    async def _generate_embedding_async(
        self,
        note_id: str,
        title: str,
        content: str,
    ) -> Any:
        """Run the (sync) generator on a worker thread and log timings."""
        embedding_content = f"{title}\n{content}"
        model = self._embedding_generator.model_name
        self._log.debug(
            f"embedding: dispatching note_id={note_id} model={model} "
            f"chars={len(embedding_content)} to background thread"
        )
        start = time.perf_counter()
        try:
            embedding = await asyncio.to_thread(
                self._embedding_generator.generate, embedding_content,
            )
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._log.exception(
                f"embedding: generation failed note_id={note_id} "
                f"model={model} after {elapsed_ms:.1f}ms"
            )
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self._log.debug(
            f"embedding: generation ok note_id={note_id} model={model} "
            f"took={elapsed_ms:.1f}ms"
        )
        return embedding

    async def _store_embedding(
        self,
        note_id: str,
        embedding: Any,
    ) -> NoteEmbeddingEntity:
        embedding_str = tensor_to_str_vec(embedding)
        self._log.debug(
            f"embedding: persisting note_id={note_id} "
            f"model={self._embedding_generator.model_name}"
        )
        start = time.perf_counter()
        record = await self._table.insert({
            "note_id": note_id,
            "model": self._embedding_generator.model_name,
            "embedding": embedding_str,
        })
        persist_ms = (time.perf_counter() - start) * 1000.0
        if not record:
            self._log.error(
                f"embedding: persist returned no rows note_id={note_id} "
                f"after {persist_ms:.1f}ms"
            )
            raise Exception("Failed to insert embedding")
        assert len(record) > 0
        self._log.debug(
            f"embedding: persist ok note_id={note_id} took={persist_ms:.1f}ms"
        )
        return NoteEmbeddingEntity(
            note_id=record[0]["note_id"],
            model=self._embedding_generator.model_name,
            embedding=str_vec_to_list(record[0]["embedding"]),
        )

    async def insert(self, note_id: str, title: str, content: str) -> NoteEmbeddingEntity:
        self._log.debug(f"embedding: insert note_id={note_id}")
        embedding = await self._generate_embedding_async(note_id, title, content)
        result = await self._store_embedding(note_id, embedding)
        self._log.debug(f"embedding: insert complete note_id={note_id}")
        return result

    async def update(self, note_id: str, title: str, content: str) -> NoteEmbeddingEntity:
        """Upsert the embedding for an existing note."""
        model = self._embedding_generator.model_name
        self._log.debug(
            f"embedding: update note_id={note_id} model={model}"
        )
        embedding = await self._generate_embedding_async(note_id, title, content)
        update_fields = NoteEmbeddingEntity(
            note_id=UNDEFINED,
            model=UNDEFINED,
            embedding=self._embedding_generator.tensor_to_sequence(embedding),
        )
        try:
            return await self._update(
                set=update_fields,
                where=NoteEmbeddingEntity(note_id, model, UNDEFINED),
            )
        except ValueError:
            return await self._store_embedding(note_id, embedding)

    async def _update(self, set: NoteEmbeddingEntity, where: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        set_dict = asdict(set)
        if isinstance(set.embedding, list):
            set_dict["embedding"] = self._embedding_generator.list_to_str_vec(set.embedding)
        where_dict = asdict(where)
        if isinstance(where.embedding, list):
            where_dict["embedding"] = self._embedding_generator.list_to_str_vec(where.embedding)
        record = await self._table.update(set=set_dict, where=where_dict)
        if not record:
            raise ValueError(f"Failed to update embedding for note_id: {where.note_id}")
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
    