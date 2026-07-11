from dataclasses import replace
from datetime import datetime
from typing import List, Optional


from src.api.note_facade import NoteRepoFacadeABC, SearchType
from src.api.relationship import AttachmentRelationEnum
from src.api.types import LoggingProvider, Pagination
from src.api.user_context import UserContextABC
from src.db.entities import NoteEntity
from src.db.database import Database
from src.db.repos.directory.directory import DirectoryRepo
from src.db.repos.note.content import NoteContentRepo

from src.db.repos.permissions import PermissionRepoABC
from src.api import NoteRelationEnum, ObjectRef, ObjectTypeEnum, Relationship, SubjectRef
from src.api.undefined import UNDEFINED, unwrap_undefined_or
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.db.repos.note.versioning import NoteVersionRepoABC
from src.db.repos.note.search_strategy import (
    ContextNoteSearchStrategy,
    DateNoteSearchStrategy,
    FuzzyTitleContentSearchStrategy,
    WebNoteSearchStrategy,
)


class NoteFacade(NoteRepoFacadeABC):
    def __init__(
        self, 
        db: Database,
        content_repo: NoteContentRepo,
        embedding_repo: NoteEmbeddingRepo,
        permission_repo: PermissionRepoABC,
        directory_repo: DirectoryRepo,
        logging_provider: LoggingProvider,
        version_repo: NoteVersionRepoABC | None = None,
    ):
        self._db = db
        self._content_repo = content_repo
        self._embedding_repo = embedding_repo
        self._permission_repo = permission_repo
        self._directory_repo = directory_repo
        self._version_repo = version_repo
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
        # direct relationships of note with user relations and parent directories
        relations = await self._permission_repo.list_relationships(
            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
        )

        # attachments store parent_note relation, 
        # we need to lookup attachments separately
        # lookup attachments:???#parent_note@note:note_id
        attachment_relations = await self._permission_repo.lookup_relationships(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ATTACHMENT, UNDEFINED),
                relation=AttachmentRelationEnum.PARENT_NOTE,
                subject=SubjectRef(ObjectTypeEnum.NOTE, note_id)
            )
        )
        # merge user/directory relations with attachment relations
        return sorted(
            relations + attachment_relations,
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
        if note.content:
            embedding = await self._embedding_repo.insert(
                note_id,
                note.title if note.title else "",
                note.content
            )
            note.embeddings.append(embedding)

        # add a parent directory
        user_directory_ids = await self._directory_repo.list_user_directory_ids(user)

        if note.parent_dir_id:
            # user has specified a parent directory -> use this
            requested_parent_dir = str(note.parent_dir_id)
            if requested_parent_dir not in user_directory_ids:
                raise ValueError(
                    f"Provided parent_dir_id '{requested_parent_dir}' is not accessible for user '{user.user_id}'"
                )
            parent_directory_id = requested_parent_dir
        else:
            # user has not specified a parent directory -> use the default directory
            # this should resolve to fleeting notes. there is no error handling if it does not exist
            directories = [
                await self._directory_repo.fetch_directory(directory_id)
                for directory_id in user_directory_ids
            ]
            directories = [d for d in directories if d and d.name == DEFAULT_DIRECTORY_NAME]
            assert len(directories) == 1
            assert directories[0].id not in (UNDEFINED, None)
            parent_directory_id = str(directories[0].id)
        
        # insert permissions
        # user -> fileowner relation
        owner_relation = Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id), 
            relation=NoteRelationEnum.OWNER,
            subject=SubjectRef(ObjectTypeEnum.USER, user.user_id)
        )
        # directory -> parent_directory relation
        parent_dir_relation = Relationship(
            resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
            relation=NoteRelationEnum.PARENT_DIRECTORY,
            subject=SubjectRef(ObjectTypeEnum.DIRECTORY, parent_directory_id)
        )
        await self._permission_repo.insert([owner_relation, parent_dir_relation])
        note.permissions = await self._fetch_note_permissions(note_id=note_id)

        # check that the parent dir relation was inserted
        given_dir_is_parent_dir_of_note = any(
            str(rel.relation) == str(NoteRelationEnum.PARENT_DIRECTORY)
            and str(rel.subject.object_type) == str(ObjectTypeEnum.DIRECTORY)
            and str(rel.subject.object_id) == str(parent_directory_id)
            for rel in note.permissions
        )
        if not given_dir_is_parent_dir_of_note:
            self.log.warning(
                "Parent directory relationship missing in fetched permissions; adding it to response"
            )
            note.permissions.append(parent_dir_relation)
        note.note_id = note_id

        # record initial snapshot after we have a note id
        if self._version_repo is not None:
            await self._version_repo.record_initial_snapshot(
                note_id=note_id,
                title=unwrap_undefined_or(note.title, default=None),
                content=unwrap_undefined_or(note.content, default=None),
                author_id=unwrap_undefined_or(note.author_id, default=user.user_id),
                created_at=note.updated_at or datetime.now(),
            )
        return note
    
    async def update(self, note: NoteEntity, ctx: UserContextABC) -> NoteEntity:
        # fetch current state for versioning before applying updates
        current = await self._content_repo.select_by_id(str(note.note_id))

        # update content
        _updated_note_entity = await self._content_repo.update(
            set=replace(note, embeddings=UNDEFINED, permissions=UNDEFINED, note_id=UNDEFINED),
            where=NoteEntity(note_id=note.note_id)
        )

        # update embedding
        if note.content and note.note_id:
            embedding = await self._embedding_repo.update(
                note.note_id,
                note.title if note.title else "",
                note.content
            )
            note.embeddings = [embedding]

        # gRPC conversion expects a list; keep permissions predictable.
        if note.permissions is UNDEFINED:
            note.permissions = []

        # record version entry using previous and current data
        if self._version_repo is not None:
            new_title = note.title if note.title is not UNDEFINED else current.title
            new_content = note.content if note.content is not UNDEFINED else current.content
            new_author_id = note.author_id if note.author_id is not UNDEFINED else current.author_id
            new_updated_at = note.updated_at if note.updated_at is not UNDEFINED else datetime.now()

            await self._version_repo.append_version(
                note_id=str(note.note_id),
                old_title=current.title,
                old_content=current.content,
                new_title=new_title,
                new_content=new_content,
                author_id=str(new_author_id),
                created_at=new_updated_at,
            )
        
        return note

    async def delete(self, note_id: str, ctx: UserContextABC) -> Optional[List[NoteEntity]]:
        return await self._content_repo.delete(NoteEntity(note_id=note_id, author_id=ctx.user_id))
    
    async def select_by_id(
        self,
        note_id: str,
        ctx: UserContextABC,
        *,
        include_permissions: bool = True,
    ) -> Optional[NoteEntity]:
        record = await self._content_repo.select_by_id(note_id)
        if not record:
            return None

        # fetch embeddings
        # end user don't care about embeddings -> only a backend thing

        # embeddings = await self._embedding_repo.select(
        #     NoteEmbeddingEntity(
        #         note_id=note_id,
        #         model=UNDEFINED,
        #         embedding=UNDEFINED,
        #     )
        # )
        # record.embeddings = embeddings

        if include_permissions:
            record.permissions = await self._fetch_note_permissions(note_id=note_id)
        return record

    async def select_by_ids(
        self,
        note_ids: List[str],
        ctx: UserContextABC,
        *,
        include_permissions: bool = True,
    ) -> List[NoteEntity]:
        """Resolve a batch of notes by id.

        Delegates the projection to
        :meth:`~src.db.repos.note.content.NoteContentRepo.select_by_ids`
        and then enriches each hit with its direct + attachment
        relations the same way :meth:`select_by_id` does.

        Args:
            note_ids: ids to resolve.  Order is preserved in the
                result list.  Empty input is a programming error.
            ctx: caller identity; unused today but kept symmetric
                with :meth:`select_by_id` so future permission
                filters can be applied per-fetch.
            include_permissions: when `False`, skip the
                per-note permission lookup on every hit.
                Defaults to `True`.

        Raises:
            ValueError: when `note_ids` is empty or any id is missing.

        Returns:
            List[NoteEntity]: resolved notes in `note_ids` order.
        """
        entities = await self._content_repo.select_by_ids(note_ids)
        if not include_permissions:
            return entities
        for note in entities:
            note.permissions = await self._fetch_note_permissions(
                note_id=str(note.note_id),
            )
        return entities

    async def search_notes(
        self,
        search_type: SearchType,
        query: str,
        ctx: UserContextABC,
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
                    note_entities_dict[note_id].permissions.append(Relationship(  # type: ignore
                        resource=ObjectRef(ObjectTypeEnum.NOTE, note_id),
                        relation=NoteRelationEnum.PARENT_DIRECTORY,
                        subject=SubjectRef(ObjectTypeEnum.DIRECTORY, directory_id)
                    ))
                    
        return note_entities





    

    