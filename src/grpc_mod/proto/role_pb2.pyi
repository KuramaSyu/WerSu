import datetime

from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
import sharing_pb2 as _sharing_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Role(_message.Message):
    __slots__ = ("id", "name", "description", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    description: _sharing_pb2.NullableString
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[_Union[_sharing_pb2.NullableString, _Mapping]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class UserRoleMembership(_message.Message):
    __slots__ = ("user_id", "role_id", "granted_at")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    GRANTED_AT_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    role_id: str
    granted_at: _timestamp_pb2.Timestamp
    def __init__(self, user_id: _Optional[str] = ..., role_id: _Optional[str] = ..., granted_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class RoleFilter(_message.Message):
    __slots__ = ("name", "member_id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    MEMBER_ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    member_id: str
    def __init__(self, name: _Optional[str] = ..., member_id: _Optional[str] = ...) -> None: ...

class CreateRoleRequest(_message.Message):
    __slots__ = ("user_id", "name", "description")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    name: str
    description: _sharing_pb2.NullableString
    def __init__(self, user_id: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[_Union[_sharing_pb2.NullableString, _Mapping]] = ...) -> None: ...

class UpdateRoleRequest(_message.Message):
    __slots__ = ("user_id", "id", "name", "description")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    id: str
    name: str
    description: _sharing_pb2.NullableString
    def __init__(self, user_id: _Optional[str] = ..., id: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[_Union[_sharing_pb2.NullableString, _Mapping]] = ...) -> None: ...

class DeleteRoleRequest(_message.Message):
    __slots__ = ("user_id", "id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    id: str
    def __init__(self, user_id: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class GetRoleRequest(_message.Message):
    __slots__ = ("user_id", "id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    id: str
    def __init__(self, user_id: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class GetRolesRequest(_message.Message):
    __slots__ = ("user_id", "filter")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    FILTER_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    filter: RoleFilter
    def __init__(self, user_id: _Optional[str] = ..., filter: _Optional[_Union[RoleFilter, _Mapping]] = ...) -> None: ...

class AddUserToRoleRequest(_message.Message):
    __slots__ = ("user_id", "role_id", "subject_user_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    role_id: str
    subject_user_id: str
    def __init__(self, user_id: _Optional[str] = ..., role_id: _Optional[str] = ..., subject_user_id: _Optional[str] = ...) -> None: ...

class RemoveUserFromRoleRequest(_message.Message):
    __slots__ = ("user_id", "role_id", "subject_user_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    role_id: str
    subject_user_id: str
    def __init__(self, user_id: _Optional[str] = ..., role_id: _Optional[str] = ..., subject_user_id: _Optional[str] = ...) -> None: ...

class GetRolesForUserRequest(_message.Message):
    __slots__ = ("user_id", "subject_user_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    subject_user_id: str
    def __init__(self, user_id: _Optional[str] = ..., subject_user_id: _Optional[str] = ...) -> None: ...

class GetUsersForRoleRequest(_message.Message):
    __slots__ = ("user_id", "role_id")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    role_id: str
    def __init__(self, user_id: _Optional[str] = ..., role_id: _Optional[str] = ...) -> None: ...
