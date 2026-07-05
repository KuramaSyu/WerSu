import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PermissionObjectType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PERMISSION_OBJECT_TYPE_UNSPECIFIED: _ClassVar[PermissionObjectType]
    PERMISSION_OBJECT_TYPE_NOTE: _ClassVar[PermissionObjectType]
    PERMISSION_OBJECT_TYPE_DIRECTORY: _ClassVar[PermissionObjectType]
    PERMISSION_OBJECT_TYPE_USER: _ClassVar[PermissionObjectType]
    PERMISSION_OBJECT_TYPE_ATTACHMENT: _ClassVar[PermissionObjectType]
PERMISSION_OBJECT_TYPE_UNSPECIFIED: PermissionObjectType
PERMISSION_OBJECT_TYPE_NOTE: PermissionObjectType
PERMISSION_OBJECT_TYPE_DIRECTORY: PermissionObjectType
PERMISSION_OBJECT_TYPE_USER: PermissionObjectType
PERMISSION_OBJECT_TYPE_ATTACHMENT: PermissionObjectType

class GetNoteRequest(_message.Message):
    __slots__ = ("id", "user_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    user_id: str
    def __init__(self, id: _Optional[str] = ..., user_id: _Optional[str] = ...) -> None: ...

class GetSearchNotesRequest(_message.Message):
    __slots__ = ("search_type", "query", "limit", "offset", "user_id")
    class SearchType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        Undefined: _ClassVar[GetSearchNotesRequest.SearchType]
        NoSearch: _ClassVar[GetSearchNotesRequest.SearchType]
        FullTextTitle: _ClassVar[GetSearchNotesRequest.SearchType]
        Fuzzy: _ClassVar[GetSearchNotesRequest.SearchType]
        Context: _ClassVar[GetSearchNotesRequest.SearchType]
    Undefined: GetSearchNotesRequest.SearchType
    NoSearch: GetSearchNotesRequest.SearchType
    FullTextTitle: GetSearchNotesRequest.SearchType
    Fuzzy: GetSearchNotesRequest.SearchType
    Context: GetSearchNotesRequest.SearchType
    SEARCH_TYPE_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    search_type: GetSearchNotesRequest.SearchType
    query: str
    limit: int
    offset: int
    user_id: str
    def __init__(self, search_type: _Optional[_Union[GetSearchNotesRequest.SearchType, str]] = ..., query: _Optional[str] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ..., user_id: _Optional[str] = ...) -> None: ...

class MinimalNote(_message.Message):
    __slots__ = ("id", "title", "author_id", "updated_at", "stripped_content", "permissions")
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_ID_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    STRIPPED_CONTENT_FIELD_NUMBER: _ClassVar[int]
    PERMISSIONS_FIELD_NUMBER: _ClassVar[int]
    id: str
    title: str
    author_id: str
    updated_at: _timestamp_pb2.Timestamp
    stripped_content: str
    permissions: _containers.RepeatedCompositeFieldContainer[PermissionRelationship]
    def __init__(self, id: _Optional[str] = ..., title: _Optional[str] = ..., author_id: _Optional[str] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., stripped_content: _Optional[str] = ..., permissions: _Optional[_Iterable[_Union[PermissionRelationship, _Mapping]]] = ...) -> None: ...

class Note(_message.Message):
    __slots__ = ("id", "title", "content", "updated_at", "author_id", "permissions")
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_ID_FIELD_NUMBER: _ClassVar[int]
    PERMISSIONS_FIELD_NUMBER: _ClassVar[int]
    id: str
    title: str
    content: str
    updated_at: _timestamp_pb2.Timestamp
    author_id: str
    permissions: _containers.RepeatedCompositeFieldContainer[PermissionRelationship]
    def __init__(self, id: _Optional[str] = ..., title: _Optional[str] = ..., content: _Optional[str] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., author_id: _Optional[str] = ..., permissions: _Optional[_Iterable[_Union[PermissionRelationship, _Mapping]]] = ...) -> None: ...

class NoteResponse(_message.Message):
    __slots__ = ("note", "id_token_map")
    class IdTokenMapEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NOTE_FIELD_NUMBER: _ClassVar[int]
    ID_TOKEN_MAP_FIELD_NUMBER: _ClassVar[int]
    note: Note
    id_token_map: _containers.ScalarMap[str, str]
    def __init__(self, note: _Optional[_Union[Note, _Mapping]] = ..., id_token_map: _Optional[_Mapping[str, str]] = ...) -> None: ...

class NoteEmbedding(_message.Message):
    __slots__ = ("model", "embedding")
    MODEL_FIELD_NUMBER: _ClassVar[int]
    EMBEDDING_FIELD_NUMBER: _ClassVar[int]
    model: str
    embedding: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, model: _Optional[str] = ..., embedding: _Optional[_Iterable[float]] = ...) -> None: ...

class PostNoteRequest(_message.Message):
    __slots__ = ("title", "content", "author_id")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_ID_FIELD_NUMBER: _ClassVar[int]
    title: str
    content: str
    author_id: str
    def __init__(self, title: _Optional[str] = ..., content: _Optional[str] = ..., author_id: _Optional[str] = ...) -> None: ...

class DeleteNoteRequest(_message.Message):
    __slots__ = ("id", "author_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    author_id: str
    def __init__(self, id: _Optional[str] = ..., author_id: _Optional[str] = ...) -> None: ...

class AlterNoteRequest(_message.Message):
    __slots__ = ("id", "title", "content", "author_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    title: str
    content: str
    author_id: str
    def __init__(self, id: _Optional[str] = ..., title: _Optional[str] = ..., content: _Optional[str] = ..., author_id: _Optional[str] = ...) -> None: ...

class Directory(_message.Message):
    __slots__ = ("id", "name", "display_name", "description", "image_url", "parent_id", "relationships")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    IMAGE_URL_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    RELATIONSHIPS_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    display_name: str
    description: str
    image_url: str
    parent_id: str
    relationships: _containers.RepeatedCompositeFieldContainer[PermissionRelationship]
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., display_name: _Optional[str] = ..., description: _Optional[str] = ..., image_url: _Optional[str] = ..., parent_id: _Optional[str] = ..., relationships: _Optional[_Iterable[_Union[PermissionRelationship, _Mapping]]] = ...) -> None: ...

class GetDirectoryRequest(_message.Message):
    __slots__ = ("id", "user_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    user_id: str
    def __init__(self, id: _Optional[str] = ..., user_id: _Optional[str] = ...) -> None: ...

class GetDirectoriesRequest(_message.Message):
    __slots__ = ("user_id", "parent_id", "limit", "offset")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    parent_id: str
    limit: int
    offset: int
    def __init__(self, user_id: _Optional[str] = ..., parent_id: _Optional[str] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ...) -> None: ...

class CreateDirectoryRequest(_message.Message):
    __slots__ = ("name", "display_name", "description", "image_url", "parent_id", "user_id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    IMAGE_URL_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    display_name: str
    description: str
    image_url: str
    parent_id: str
    user_id: str
    def __init__(self, name: _Optional[str] = ..., display_name: _Optional[str] = ..., description: _Optional[str] = ..., image_url: _Optional[str] = ..., parent_id: _Optional[str] = ..., user_id: _Optional[str] = ...) -> None: ...

class AlterDirectoryRequest(_message.Message):
    __slots__ = ("id", "name", "display_name", "description", "image_url", "parent_id", "user_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    IMAGE_URL_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    display_name: str
    description: str
    image_url: str
    parent_id: str
    user_id: str
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., display_name: _Optional[str] = ..., description: _Optional[str] = ..., image_url: _Optional[str] = ..., parent_id: _Optional[str] = ..., user_id: _Optional[str] = ...) -> None: ...

class DeleteDirectoryRequest(_message.Message):
    __slots__ = ("id", "user_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    user_id: str
    def __init__(self, id: _Optional[str] = ..., user_id: _Optional[str] = ...) -> None: ...

class GetNotesOfDirectoryRequest(_message.Message):
    __slots__ = ("directory_id", "limit", "offset", "user_id")
    DIRECTORY_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    directory_id: str
    limit: int
    offset: int
    user_id: str
    def __init__(self, directory_id: _Optional[str] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ..., user_id: _Optional[str] = ...) -> None: ...

class PermissionSubject(_message.Message):
    __slots__ = ("object_type", "object_id")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    object_type: PermissionObjectType
    object_id: str
    def __init__(self, object_type: _Optional[_Union[PermissionObjectType, str]] = ..., object_id: _Optional[str] = ...) -> None: ...

class PermissionResource(_message.Message):
    __slots__ = ("object_type", "object_id")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    object_type: PermissionObjectType
    object_id: str
    def __init__(self, object_type: _Optional[_Union[PermissionObjectType, str]] = ..., object_id: _Optional[str] = ...) -> None: ...

class PermissionRelationship(_message.Message):
    __slots__ = ("relation", "subject", "resource")
    RELATION_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    relation: str
    subject: PermissionSubject
    resource: PermissionResource
    def __init__(self, relation: _Optional[str] = ..., subject: _Optional[_Union[PermissionSubject, _Mapping]] = ..., resource: _Optional[_Union[PermissionResource, _Mapping]] = ...) -> None: ...

class GetPermissionsRequest(_message.Message):
    __slots__ = ("object_type", "object_id", "user_id")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    object_type: PermissionObjectType
    object_id: str
    user_id: str
    def __init__(self, object_type: _Optional[_Union[PermissionObjectType, str]] = ..., object_id: _Optional[str] = ..., user_id: _Optional[str] = ...) -> None: ...

class CreatePermissionRequest(_message.Message):
    __slots__ = ("object_type", "object_id", "relationship", "user_id")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    RELATIONSHIP_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    object_type: PermissionObjectType
    object_id: str
    relationship: PermissionRelationship
    user_id: str
    def __init__(self, object_type: _Optional[_Union[PermissionObjectType, str]] = ..., object_id: _Optional[str] = ..., relationship: _Optional[_Union[PermissionRelationship, _Mapping]] = ..., user_id: _Optional[str] = ...) -> None: ...

class DeletePermissionRequest(_message.Message):
    __slots__ = ("object_type", "object_id", "relationship", "user_id")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    RELATIONSHIP_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    object_type: PermissionObjectType
    object_id: str
    relationship: PermissionRelationship
    user_id: str
    def __init__(self, object_type: _Optional[_Union[PermissionObjectType, str]] = ..., object_id: _Optional[str] = ..., relationship: _Optional[_Union[PermissionRelationship, _Mapping]] = ..., user_id: _Optional[str] = ...) -> None: ...

class ReplacePermissionsRequest(_message.Message):
    __slots__ = ("object_type", "object_id", "relationships", "user_id")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    RELATIONSHIPS_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    object_type: PermissionObjectType
    object_id: str
    relationships: _containers.RepeatedCompositeFieldContainer[PermissionRelationship]
    user_id: str
    def __init__(self, object_type: _Optional[_Union[PermissionObjectType, str]] = ..., object_id: _Optional[str] = ..., relationships: _Optional[_Iterable[_Union[PermissionRelationship, _Mapping]]] = ..., user_id: _Optional[str] = ...) -> None: ...

class PermissionsResponse(_message.Message):
    __slots__ = ("object_type", "object_id", "relationships")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    RELATIONSHIPS_FIELD_NUMBER: _ClassVar[int]
    object_type: PermissionObjectType
    object_id: str
    relationships: _containers.RepeatedCompositeFieldContainer[PermissionRelationship]
    def __init__(self, object_type: _Optional[_Union[PermissionObjectType, str]] = ..., object_id: _Optional[str] = ..., relationships: _Optional[_Iterable[_Union[PermissionRelationship, _Mapping]]] = ...) -> None: ...

class NoteVersionSummary(_message.Message):
    __slots__ = ("version_id", "note_id", "version_index", "created_at", "author_id", "is_snapshot", "snapshot_id")
    VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    NOTE_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_INDEX_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_ID_FIELD_NUMBER: _ClassVar[int]
    IS_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    version_id: str
    note_id: str
    version_index: int
    created_at: _timestamp_pb2.Timestamp
    author_id: str
    is_snapshot: bool
    snapshot_id: str
    def __init__(self, version_id: _Optional[str] = ..., note_id: _Optional[str] = ..., version_index: _Optional[int] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., author_id: _Optional[str] = ..., is_snapshot: _Optional[bool] = ..., snapshot_id: _Optional[str] = ...) -> None: ...

class GetNoteVersionsRequest(_message.Message):
    __slots__ = ("note_id", "limit", "offset", "user_id")
    NOTE_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    note_id: str
    limit: int
    offset: int
    user_id: str
    def __init__(self, note_id: _Optional[str] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ..., user_id: _Optional[str] = ...) -> None: ...

class GetDirectoryActivityRequest(_message.Message):
    __slots__ = ("directory_id", "max_depth", "limit", "offset", "user_id")
    DIRECTORY_ID_FIELD_NUMBER: _ClassVar[int]
    MAX_DEPTH_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    directory_id: str
    max_depth: int
    limit: int
    offset: int
    user_id: str
    def __init__(self, directory_id: _Optional[str] = ..., max_depth: _Optional[int] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ..., user_id: _Optional[str] = ...) -> None: ...

class NoteVersionContent(_message.Message):
    __slots__ = ("note_id", "version_index", "created_at", "author_id", "title", "content")
    NOTE_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_INDEX_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    note_id: str
    version_index: int
    created_at: _timestamp_pb2.Timestamp
    author_id: str
    title: str
    content: str
    def __init__(self, note_id: _Optional[str] = ..., version_index: _Optional[int] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., author_id: _Optional[str] = ..., title: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...

class GetNoteVersionContentRequest(_message.Message):
    __slots__ = ("note_id", "version_index", "user_id")
    NOTE_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_INDEX_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    note_id: str
    version_index: int
    user_id: str
    def __init__(self, note_id: _Optional[str] = ..., version_index: _Optional[int] = ..., user_id: _Optional[str] = ...) -> None: ...

class RestoreNoteVersionRequest(_message.Message):
    __slots__ = ("note_id", "version_index", "user_id")
    NOTE_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_INDEX_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    note_id: str
    version_index: int
    user_id: str
    def __init__(self, note_id: _Optional[str] = ..., version_index: _Optional[int] = ..., user_id: _Optional[str] = ...) -> None: ...
