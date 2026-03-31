from src.api.undefined import UNDEFINED
from src.db.entities.directory.directory import DirectoryEntity
from src.grpc_mod.converter.note_entity_converter import _to_permission_object_type
from src.grpc_mod.proto.note_pb2 import Directory, PermissionRelationship, PermissionResource, PermissionSubject


def to_grpc_directory(directory_entity: DirectoryEntity | None) -> Directory:
    """Convert a DirectoryEntity to its gRPC Directory message."""

    if directory_entity is None:
        return Directory()

    relationships: list[PermissionRelationship] = []
    if isinstance(directory_entity.relations, list):
        relationships = [
            PermissionRelationship(
                relation=str(rel.relation),
                subject=PermissionSubject(
                    object_type=_to_permission_object_type(str(rel.subject.object_type)),
                    object_id=str(rel.subject.object_id),
                ),
                resource=PermissionResource(
                    object_type=_to_permission_object_type(str(rel.resource.object_type)),
                    object_id=str(rel.resource.object_id),
                ),
            )
            for rel in directory_entity.relations
        ]

    kwargs = {
        "id": "" if directory_entity.id in (UNDEFINED, None) else str(directory_entity.id),
        "name": "" if directory_entity.name in (UNDEFINED, None) else str(directory_entity.name),
        "display_name": "" if directory_entity.display_name in (UNDEFINED, None) else str(directory_entity.display_name),
        "description": "" if directory_entity.description in (UNDEFINED, None) else str(directory_entity.description),
        "image_url": "" if directory_entity.image_url in (UNDEFINED, None) else str(directory_entity.image_url),
        "relationships": relationships,
    }

    if directory_entity.parent_id not in (UNDEFINED, None):
        kwargs["parent_id"] = str(directory_entity.parent_id)

    return Directory(**kwargs)
