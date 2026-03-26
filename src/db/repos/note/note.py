from abc import ABC, abstractmethod
from dataclasses import replace
from enum import Enum
from typing import List, Optional, Type

import asyncpg

from src.api.types import LoggingProvider, Pagination
from src.api.user_context import UserContextABC
from src.db.entities import NoteEntity
from src.db.database import Database
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.content import NoteContentRepo

from src.db.repos.note.permission import NotePermissionRepo, NoteRelationEnum, ObjectRef, ObjectTypeEnum, Relationship, SubjectRef
from src.db.table import TableABC
from src.api.undefined import UNDEFINED
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.db.repos.note.search_strategy import (
    ContextNoteSearchStrategy,
    DateNoteSearchStrategy,
    FuzzyTitleContentSearchStrategy,
    WebNoteSearchStrategy,
)

class SearchType(Enum):
    NO_SEARCH = 1
    FULL_TEXT_TITLE = 2
    FUZZY = 3
    CONTEXT = 4


class UserContext(UserContextABC):
    def __init__(self, user_id: str):
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        return self._user_id


class NoteRepoFacadeABC(ABC):
    """Represents the ABC for note-operations which operate over multiple relations"""
    @property
    def embedding_table_name(self) -> str:
        return "note.embedding"

    @property
    def content_table_name(self) -> str:
        return "note.content"
    
    @property
    def permission_table_name(self) -> str:
        return "note.permission"

    @abstractmethod
    async def insert(
        self,
        note: NoteEntity,
        user: UserContextABC,
    ) -> NoteEntity:
        """inserts a full note into 
        Note DB, stores relations (note#owner@user) and stores 
        directory relation (note#parent_directory@users_fleeting_dir) or a given 
        directory.
        The embedding will be generated automatically.
        Added embeddings will be ignored.
        
        Args:
        -----
        note: `NoteMetadataEntity`
            the note of a note
        user: `UserContextABC`
            information about the user to create owner relation to note

        Returns:
        --------
        `NoteMetadataEntity`:
            the updated entity (updated ID)
        """
        ...

    @abstractmethod
    async def update(
        self,
        note: NoteEntity,
        ctx: UserContext,
    ) -> NoteEntity:
        """updates note (content only)
        
        Args:
        -----
        note: `NoteEntity`
            the note

        Returns:
        --------
        `NoteEntity`:
            the updated entity
        """
        ...

    @abstractmethod
    async def delete(
        self,
        note_id: str,
        ctx: UserContext,
    ) -> Optional[List[NoteEntity]]:
        """delete note
        
        Args:
        -----
        note_id: `str`
            the ID of the note to delete

        Returns:
        --------
        `NoteMetadataEntity`:
            the updated entity
        """
        ...


    @abstractmethod
    async def select_by_id(
        self,
        note_id: str,
        ctx: UserContext,
    ) -> Optional[NoteEntity]:
        """select a whole note by its ID
        
        Args:
        -----
        note_id: `str`
            the ID of the note

            
        Returns:
        --------
        `NoteMetadataEntity`:
            the updated entity
            
        """
        ...

    @abstractmethod
    async def search_notes(
        self, 
        search_type: SearchType,
        query: str, 
        ctx: UserContext,
        pagination: Pagination
    ) -> List[NoteEntity]:
        """search notes according to the search type
        
        Args:
        -----
        search_type: `SearchType`
            the type of search to perform
        query: `str`
            the search query
        pagination: `Pagination`
            pagination parameters (limit, offset)

        Returns:
        --------
        `List[MinimalNote]`:
            the list of matching minimal notes
        """
        ...


class NoteRepoFacade(NoteRepoFacadeABC):
    def __init__(
        self, 
        db: Database,
        content_repo: NoteContentRepo,
        embedding_repo: NoteEmbeddingRepo,
        permission_repo: NotePermissionRepo,
        directory_repo: DirectoryRepo,
        logging_provider: LoggingProvider,
    ):
        self._db = db
        self._content_repo = content_repo
        self._embedding_repo = embedding_repo
        self._permission_repo = permission_repo
        self._directory_repo = directory_repo
        self.log = logging_provider(__name__, self)

    async def _fetch_note_permissions(
        self,
        note_id: str,
    ) -> List[Relationship]:
        """Fetch all direct relationships stored for a note.

        Parameters
        ----------
        note_id : str
            Note ID to load relationships for.

        Returns
        -------
        List[Relationship]
            Direct relationships on the note (for example `owner` and
            `parent_directory`) as stored in the permission backend.
        """
        relations = await self._permission_repo.list_relationships(
            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
        )
        return sorted(
            relations,
            key=lambda rel: (
                str(rel.relation),
                str(rel.subject.object_type),
                "" if rel.subject.object_id is UNDEFINED else str(rel.subject.object_id),
            ),
        )

    
    async def insert(self, note: NoteEntity, user: UserContextABC):
        DEFAULT_DIRECTORY_NAME = self._directory_repo.get_default_directory_specs()[0].name
        # insert note itself
        query = f"""
        INSERT INTO {self.content_table_name}(title, content, updated_at, author_id)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """
        note_id: str = (await self._db.fetchrow(
            query, 
            note.title, note.content, note.updated_at, note.author_id
        ))["id"] 
        self.log.debug(f"Inserted note with ID: {note_id}")

        # insert embeddings
        assert note.embeddings == [] or note.embeddings is UNDEFINED
        note.embeddings = []
        query = f"""
        INSERT INTO {self.embedding_table_name}(note_id, model, embedding)
        VALUES ($1, $2, $3)
        """
        if note.content:
            embedding = await self._embedding_repo.insert(
                note_id,
                note.title if note.title else "",
                note.content
            )
            note.embeddings.append(embedding)

        # resolve parent directory
        user_directory_ids = await self._directory_repo.list_user_directory_ids(user)
        requested_parent_dir_id = note.parent_dir_id if note.parent_dir_id not in (UNDEFINED, None) else None

        if requested_parent_dir_id is not None:
            requested_parent_dir_id = str(requested_parent_dir_id)
            if requested_parent_dir_id not in user_directory_ids:
                raise ValueError(
                    f"Provided parent_dir_id '{requested_parent_dir_id}' is not accessible for user '{user.user_id}'"
                )
            parent_directory_id = requested_parent_dir_id
        else:
            directories = [
                await self._directory_repo.fetch_directory(directory_id)
                for directory_id in user_directory_ids
            ]
            directories = [d for d in directories if d and d.name == DEFAULT_DIRECTORY_NAME]
            assert len(directories) == 1
            assert directories[0].id not in (UNDEFINED, None)
            parent_directory_id = str(directories[0].id)
        
        # insert permissions
        owner_relation = Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id), 
            relation=NoteRelationEnum.OWNER,
            subject=SubjectRef(ObjectTypeEnum.USER, user.user_id)
        )
        parent_dir_relation = Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
            relation=NoteRelationEnum.PARENT_DIRECTORY,
            subject=SubjectRef(ObjectTypeEnum.DIRECTORY, parent_directory_id)
        )
        await self._permission_repo.insert([owner_relation, parent_dir_relation])
        note.permissions = await self._fetch_note_permissions(note_id=note_id)
        note.note_id = note_id
        return note
    
    async def update(self, note: NoteEntity, ctx: UserContext) -> NoteEntity:
        # update content
        note_entity = await self._content_repo.update(
            set=replace(note, embeddings=UNDEFINED, permissions=UNDEFINED, note_id=UNDEFINED),
            where=NoteEntity(note_id=note.note_id)
        )

        # add removed embeddings and permissions
        note_entity.embeddings = note.embeddings or []
        note_entity.permissions = note.permissions or []
        return note_entity

    async def delete(self, note_id: str, ctx: UserContext) -> Optional[List[NoteEntity]]:
        return await self._content_repo.delete(NoteEntity(note_id=note_id, author_id=ctx.user_id))
    
    async def select_by_id(self, note_id: str, ctx: UserContext) -> Optional[NoteEntity]:
        record = await self._content_repo.select_by_id(note_id)
        if not record:
            return None
        
        # fetch embeddings
        embeddings = await self._embedding_repo.select(
            NoteEmbeddingEntity(
                note_id=note_id,
                model=UNDEFINED,
                embedding=UNDEFINED,
            )
        )
        record.embeddings = embeddings

        record.permissions = await self._fetch_note_permissions(note_id=note_id)
        return record

    async def search_notes(
        self, 
        search_type: SearchType,
        query: str, 
        ctx: UserContext,
        pagination: Pagination
    ) -> List[NoteEntity]:
        # these parameters are common to all strategies __init__ fn
        common_init_parameters = {
            "db": self._db,
            "query": query,
            "limit": pagination.limit,
            "offset": pagination.offset,
            "user_context": ctx,
            "note_permissions": self._permission_repo,
        }
        if search_type == SearchType.NO_SEARCH:
            strategy = DateNoteSearchStrategy(**common_init_parameters)
        elif search_type == SearchType.FULL_TEXT_TITLE:
            strategy = WebNoteSearchStrategy(**common_init_parameters)
        elif search_type == SearchType.FUZZY:
            strategy = FuzzyTitleContentSearchStrategy(**common_init_parameters)
        elif search_type == SearchType.CONTEXT:
            strategy = ContextNoteSearchStrategy(
                **common_init_parameters, 
                generator=self._embedding_repo.embedding_generator
            )
        else: 
            raise ValueError(f"Unknown SearchType: {search_type}")

        note_entities = await strategy.search()
        # set permissions to [] if they are UNDEFINED
        for note in note_entities:
            if note.permissions is UNDEFINED:
                note.permissions = []
        # convert to Dict[ID, NoteEntity] for easier access
        note_entities_dict = {note.note_id: note for note in note_entities if note.note_id is not UNDEFINED}

        # fetch permissions in batches by user directories (avoid N note lookups)
        # (dir is parent of note -> only this is needed right now. extend later)
        user_directory_ids = await self._directory_repo.list_user_directory_ids(ctx)
        for directory_id in user_directory_ids:
            objects = await self._permission_repo.lookup(Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, UNDEFINED),
                relation=NoteRelationEnum.PARENT_DIRECTORY,
                subject=SubjectRef(ObjectTypeEnum.DIRECTORY, directory_id)
            ))
            for obj in objects:
                note_id = obj.object_id
                if note_id not in (UNDEFINED, None) and note_entities_dict.get(note_id):
                    note_entities_dict[note_id].permissions.append(Relationship(
                        resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
                        relation=NoteRelationEnum.PARENT_DIRECTORY,
                        subject=SubjectRef(ObjectTypeEnum.DIRECTORY, directory_id)
                    ))
                    
        return note_entities





    

    