import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from authzed.api.v1 import (
    AsyncClient,
    BulkExportRelationshipsRequest,
    DeleteRelationshipsRequest,
    RelationshipFilter,
)
from authzed.api.v1.permission_service_pb2 import ExportBulkRelationshipsRequest

from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.db.database import Database
from src.db.entities.directory.directory import DirectoryEntity
from src.db.repos.note.permission import (
    DirectoryRelationEnum,
    NotePermissionRepo,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)


class DirectoryRepo(ABC):
    """Abstract repository interface for directory persistence and relations."""

    @abstractmethod
    async def create_directory(self, entity: DirectoryEntity) -> DirectoryEntity:
        """Create a directory.

        Parameters
        ----------
        entity : DirectoryEntity
            Directory payload containing Postgres fields and optional relations.

        Returns
        -------
        DirectoryEntity
            Created directory entity with generated ID.
        """
        ...

    @abstractmethod
    async def fetch_directory(self, id: str) -> Optional[DirectoryEntity]:
        """Fetch a directory by ID.

        Parameters
        ----------
        id : str
            Directory ID.

        Returns
        -------
        Optional[DirectoryEntity]
            Directory including parent and user relations, or `None` if not found.
        """
        ...

    @abstractmethod
    async def fetch_directories(self, user: UserContextABC) -> List[DirectoryEntity]:
        """Fetch all directories visible to a user.

        Parameters
        ----------
        user : UserContextABC
            Current user context.

        Returns
        -------
        List[DirectoryEntity]
            Directories the user can view.
        """
        ...

    @abstractmethod
    async def list_note_directory_ids(self, note_id: str) -> List[str]:
        """Lookup directories that are related to a specific note.

        Parameters
        ----------
        note_id : str
            Note ID to lookup.

        Returns
        -------
        List[str]
            Distinct directory IDs referenced by the note.
        """
        ...

    @abstractmethod
    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        """Delete a directory.

        Parameters
        ----------
        entity : DirectoryEntity
            Directory containing at least the `id`.

        Returns
        -------
        bool
            `True` if one directory row was deleted in Postgres.
        """
        ...


class DirectoryRepoSpicedbPostgres(DirectoryRepo):
    """Directory repository backed by Postgres and SpiceDB."""

    def __init__(
        self,
        db: Database,
        permission_repo: NotePermissionRepo,
        spicedb_client: AsyncClient,
    ) -> None:
        self._db = db
        self._permission_repo = permission_repo
        self._spicedb_client = spicedb_client

    async def create_directory(self, entity: DirectoryEntity) -> DirectoryEntity:
        record = await self._db.fetchrow(
            """
            INSERT INTO note.directory(name, image_url)
            VALUES ($1, $2)
            RETURNING id, name, image_url
            """,
            entity.name,
            entity.image_url,
        )
        if not record:
            raise RuntimeError("Failed to create directory")

        directory_id = str(record["id"])
        relationships: List[Relationship] = []

        if entity.parent_id not in (UNDEFINED, None):
            relationships.append(
                Relationship(
                    resource=ObjectRef(object_type="directory", object_id=directory_id),
                    relation="parent",
                    subject=SubjectRef(object_type="directory", object_id=entity.parent_id),
                )
            )

        if isinstance(entity.relations, list):
            for rel in entity.relations:
                relationships.append(
                    Relationship(
                        resource=ObjectRef(object_type="directory", object_id=directory_id),
                        relation=rel.relation,  # type: ignore
                        subject=rel.subject,
                    )
                )

        if relationships:
            await self._permission_repo.insert(relationships)

        return DirectoryEntity(
            id=directory_id,
            name=record["name"],
            image_url=record["image_url"],
            parent_id=entity.parent_id,
            relations=entity.relations if isinstance(entity.relations, list) else [],
        )

    async def fetch_directory(self, id: str) -> Optional[DirectoryEntity]:
        record = await self._db.fetchrow(
            """
            SELECT id, name, image_url
            FROM note.directory
            WHERE id = $1
            """,
            id,
        )
        if not record:
            return None

        parent_id, relations = await self._fetch_spicedb_relations(str(record["id"]))

        return DirectoryEntity(
            id=str(record["id"]),
            name=record["name"],
            image_url=record["image_url"],
            parent_id=parent_id,
            relations=relations,
        )

    async def fetch_directories(self, user: UserContextABC) -> List[DirectoryEntity]:
        object_refs = await self._permission_repo.lookup(
            Relationship(
                resource=ObjectRef(object_type="directory", object_id=UNDEFINED),
                relation="view",
                subject=SubjectRef(object_type="user", object_id=user.user_id),
            )
        )

        directory_ids = [str(obj.object_id) for obj in object_refs if obj.object_id not in (UNDEFINED, None)]
        if not directory_ids:
            return []

        rows = await self._db.fetch(
            """
            SELECT id, name, image_url
            FROM note.directory
            WHERE id = ANY($1::text[])
            """,
            directory_ids,
        )
        if not rows:
            return []

        parent_and_relations = await asyncio.gather(
            *(self._fetch_spicedb_relations(str(row["id"])) for row in rows)
        )

        entities: List[DirectoryEntity] = []
        for row, rel_data in zip(rows, parent_and_relations):
            parent_id, relations = rel_data
            entities.append(
                DirectoryEntity(
                    id=str(row["id"]),
                    name=row["name"],
                    image_url=row["image_url"],
                    parent_id=parent_id,
                    relations=relations,
                )
            )
        return entities

    async def list_note_directory_ids(self, note_id: str) -> List[str]:

        # fetch all Relationships with <note_id>:note#parent_directory@directory
        response_stream = self._spicedb_client.ExportBulkRelationships(
            ExportBulkRelationshipsRequest(
                optional_relationship_filter=RelationshipFilter(
                    resource_type="note",
                    optional_resource_id=note_id,
                    optional_relation=NoteRelationEnum.PARENT_DIRECTORY
                )
            )
        )

        directory_ids: set[str] = set()
        async for response in response_stream:
            for relationship in response.relationships:
                subject = relationship.subject.object
                if subject.object_type == ObjectTypeEnum.DIRECTORY and subject.object_id:
                    directory_ids.add(str(subject.object_id))

        return list(directory_ids)

    async def delete_directory(self, entity: DirectoryEntity) -> bool:
        if entity.id in (UNDEFINED, None):
            raise ValueError("Directory ID is required for deletion")

        result = await self._db.execute(
            """
            DELETE FROM note.directory
            WHERE id = $1
            """,
            str(entity.id),
        )

        await self._spicedb_client.DeleteRelationships(
            DeleteRelationshipsRequest(
                relationship_filter=RelationshipFilter(
                    resource_type="directory",
                    optional_resource_id=str(entity.id),
                )
            )
        )
        return result == "DELETE 1"

    async def _fetch_spicedb_relations(self, directory_id: str) -> Tuple[Optional[str], List[Relationship]]:
        """Fetch parent ID and user relations from SpiceDB."""
        response_stream = self._spicedb_client.ExportBulkRelationships(
            ExportBulkRelationshipsRequest(
                optional_relationship_filter=RelationshipFilter(
                    resource_type="directory",
                    optional_resource_id=directory_id,
                )
            )
        )

        parent_id: Optional[str] = None
        relations: List[Relationship] = []

        async for response in response_stream:
            for relationship in response.relationships:
                relation_name = relationship.relation
                subject_ref = SubjectRef(
                    object_type=relationship.subject.object.object_type,  # type: ignore
                    object_id=relationship.subject.object.object_id,
                )

                if relation_name == "parent" and subject_ref.object_type == "directory":
                    parent_id = str(subject_ref.object_id)
                    continue

                if subject_ref.object_type == "user":
                    relations.append(
                        Relationship(
                            resource=ObjectRef(object_type="directory", object_id=directory_id),
                            relation=relation_name,  # type: ignore
                            subject=subject_ref,
                        )
                    )

        return parent_id, relations
