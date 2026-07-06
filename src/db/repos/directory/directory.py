import asyncio
from typing import List, Optional, Tuple

from authzed.api.v1 import (
    AsyncClient,
    BulkExportRelationshipsRequest,
    DeleteRelationshipsRequest,
    RelationshipFilter,
)
from authzed.api.v1.permission_service_pb2 import ExportBulkRelationshipsRequest

from src.api.directory_repo import DefaultDirectorySpec, DirectoryRepo
from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.db.database import Database
from src.db.entities.directory.directory import DirectoryEntity
from src.utils import convert_entity_for_db
from src.db.repos import PermissionRepoABC
from src.api import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)


class DirectoryRepoSpicedbPostgres(DirectoryRepo):
    """Directory repository backed by Postgres and SpiceDB."""

    def __init__(
        self,
        db: Database,
        permission_repo: PermissionRepoABC,
        spicedb_client: AsyncClient,
    ) -> None:
        self._db = db
        self._permission_repo = permission_repo
        self._spicedb_client = spicedb_client

    async def create_directory(self, entity: DirectoryEntity) -> DirectoryEntity:
        """
        Notes
        ------
        - entity.relations are iterated, and resource.object_id is set to the created directory ID before insertion into SpiceDB
        """
        entity_data = convert_entity_for_db(entity)
        readme_note_id = (
            None if entity_data.readme_note_id is UNDEFINED
            else entity_data.readme_note_id
        )
        record = await self._db.fetchrow(
            """
            INSERT INTO note.directory(name, display_name, description, image_url, readme_note_id)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, name, display_name, description, image_url, readme_note_id
            """,
            entity_data.name,
            entity_data.display_name,
            entity_data.description,
            entity_data.image_url,
            readme_note_id,
        )
        if not record:
            raise RuntimeError("Failed to create directory")

        directory_id = str(record["id"])
        relationships: List[Relationship] = []

        if entity_data.parent_id not in (UNDEFINED, None):
            relationships.append(
                Relationship(
                    resource=ObjectRef(object_type="directory", object_id=directory_id),
                    relation="parent",
                    subject=SubjectRef(object_type="directory", object_id=entity_data.parent_id),
                )
            )

        if isinstance(entity_data.relations, list):
            for rel in entity_data.relations:
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
            display_name=record["display_name"],
            description=record["description"],
            image_url=record["image_url"],
            parent_id=entity_data.parent_id,
            readme_note_id=record["readme_note_id"],
            relations=entity_data.relations if isinstance(entity_data.relations, list) else [],
        )

    async def fetch_directory(self, id: str) -> Optional[DirectoryEntity]:
        record = await self._db.fetchrow(
            """
            SELECT id, name, display_name, description, image_url, readme_note_id
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
            display_name=record["display_name"],
            description=record["description"],
            image_url=record["image_url"],
            parent_id=parent_id,
            readme_note_id=record["readme_note_id"],
            relations=relations,
        )

    async def update_directory(self, entity: DirectoryEntity) -> Optional[DirectoryEntity]:
        if entity.id in (UNDEFINED, None):
            raise ValueError("Directory ID is required for update")

        updates: list[str] = []
        args: list[str | None] = []

        if entity.name is not UNDEFINED:
            updates.append(f"name = ${len(args) + 1}")
            args.append(None if entity.name is None else str(entity.name))
        if entity.display_name is not UNDEFINED:
            updates.append(f"display_name = ${len(args) + 1}")
            args.append(None if entity.display_name is None else str(entity.display_name))
        if entity.description is not UNDEFINED:
            updates.append(f"description = ${len(args) + 1}")
            args.append(None if entity.description is None else str(entity.description))
        if entity.image_url is not UNDEFINED:
            updates.append(f"image_url = ${len(args) + 1}")
            args.append(None if entity.image_url is None else str(entity.image_url))
        if entity.readme_note_id is not UNDEFINED:
            updates.append(f"readme_note_id = ${len(args) + 1}")
            args.append(None if entity.readme_note_id is None else str(entity.readme_note_id))

        if updates:
            args.append(str(entity.id))
            query = f"""
            UPDATE note.directory
            SET {", ".join(updates)}
            WHERE id = ${len(args)}
            """
            await self._db.execute(query, *args)

        if entity.parent_id is not UNDEFINED:
            existing = await self.fetch_directory(str(entity.id))
            if existing is None:
                return None

            if existing.parent_id not in (UNDEFINED, None):
                await self._permission_repo.delete(
                    Relationship(
                        resource=ObjectRef(object_type="directory", object_id=str(entity.id)),
                        relation=DirectoryRelationEnum.PARENT,
                        subject=SubjectRef(object_type="directory", object_id=str(existing.parent_id)),
                    )
                )

            if entity.parent_id not in (None, ""):
                await self._permission_repo.insert(
                    [
                        Relationship(
                            resource=ObjectRef(object_type="directory", object_id=str(entity.id)),
                            relation=DirectoryRelationEnum.PARENT,
                            subject=SubjectRef(object_type="directory", object_id=str(entity.parent_id)),
                        )
                    ]
                )

        return await self.fetch_directory(str(entity.id))

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
            SELECT id, name, display_name, description, image_url, readme_note_id
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
                    display_name=row["display_name"],
                    description=row["description"],
                    image_url=row["image_url"],
                    parent_id=parent_id,
                    readme_note_id=row["readme_note_id"],
                    relations=relations,
                )
            )
        return entities

    async def list_user_directory_ids(self, user: UserContextABC) -> List[str]:
        object_refs = await self._permission_repo.lookup(
            Relationship(
                resource=ObjectRef(object_type="directory", object_id=UNDEFINED),
                relation=DirectoryRelationEnum.VIEW,
                subject=SubjectRef(object_type="user", object_id=user.user_id),
            )
        )
        return [
            str(obj.object_id)
            for obj in object_refs
            if obj.object_id not in (UNDEFINED, None)
        ]

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

    async def resolve_files_of_directory(
        self,
        directory_id: Optional[str],
        actor: UserContextABC,
        max_depth: int = 10,
    ) -> List[str]:
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")

        if directory_id in (None, ""):
            start_directories = await self.list_user_directory_ids(actor)
        else:
            start_directories = [str(directory_id)]
            can_view = await self._permission_repo.has_permission(
                actor,
                "view",
                ObjectRef(ObjectTypeEnum.DIRECTORY, str(directory_id)),
            )
            if not can_view:
                raise PermissionError("User does not have view access to the directory")

        if not start_directories:
            return []

        note_ids: set[str] = set()
        for start in start_directories:
            sub_notes, _ = await self.resolve_subtree(start, max_depth=max_depth)
            note_ids.update(sub_notes)
        return sorted(note_ids)

    async def resolve_subtree(
        self,
        directory_id: str,
        max_depth: int = 10,
    ) -> Tuple[List[str], List[str]]:
        """Walk a directory subtree via SpiceDB and collect ids.

        No permission check is performed here -- the activity log
        queries the full subtree and the service layer applies
        per-row visibility.  The walk matches
        :meth:`resolve_files_of_directory`'s queue + visited
        pattern so the two stay in lockstep.
        """
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")

        visited: set[str] = set()
        note_ids: set[str] = set()
        directory_ids: set[str] = {str(directory_id)}
        queue: list[tuple[str, int]] = [(str(directory_id), 0)]

        # tree traversal: pop a directory, collect its notes, enqueue its children.
        while queue:
            current_id, depth = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)
            if depth > max_depth:
                continue

            note_relations = await self._permission_repo.lookup_relationships(
                Relationship(
                    resource=ObjectRef(ObjectTypeEnum.NOTE, UNDEFINED),
                    relation=NoteRelationEnum.PARENT_DIRECTORY,
                    subject=SubjectRef(ObjectTypeEnum.DIRECTORY, current_id),
                )
            )
            for rel in note_relations:
                if rel.resource.object_id not in (UNDEFINED, None):
                    note_ids.add(str(rel.resource.object_id))

            if depth >= max_depth:
                continue

            child_relations = await self._permission_repo.lookup_relationships(
                Relationship(
                    resource=ObjectRef(ObjectTypeEnum.DIRECTORY, UNDEFINED),
                    relation=DirectoryRelationEnum.PARENT,
                    subject=SubjectRef(ObjectTypeEnum.DIRECTORY, current_id),
                )
            )
            for rel in child_relations:
                if rel.resource.object_id in (UNDEFINED, None):
                    continue
                child_id = str(rel.resource.object_id)
                directory_ids.add(child_id)
                if child_id not in visited:
                    queue.append((child_id, depth + 1))

        return sorted(note_ids), sorted(directory_ids)

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
