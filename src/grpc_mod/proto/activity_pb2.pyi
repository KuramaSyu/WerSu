import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AccessedAs(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ACCESSED_AS_UNSPECIFIED: _ClassVar[AccessedAs]
    ACCESSED_AS_USER: _ClassVar[AccessedAs]
    ACCESSED_AS_SYSTEM: _ClassVar[AccessedAs]

class MostUsedAlgorithm(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MOST_USED_ALGORITHM_UNSPECIFIED: _ClassVar[MostUsedAlgorithm]
    MOST_USED_ALGORITHM_COUNT: _ClassVar[MostUsedAlgorithm]
    MOST_USED_ALGORITHM_LOG_COUNT: _ClassVar[MostUsedAlgorithm]
ACCESSED_AS_UNSPECIFIED: AccessedAs
ACCESSED_AS_USER: AccessedAs
ACCESSED_AS_SYSTEM: AccessedAs
MOST_USED_ALGORITHM_UNSPECIFIED: MostUsedAlgorithm
MOST_USED_ALGORITHM_COUNT: MostUsedAlgorithm
MOST_USED_ALGORITHM_LOG_COUNT: MostUsedAlgorithm

class Activity(_message.Message):
    __slots__ = ("id", "actor_id", "accessed_as", "action", "note_id", "directory_id", "role_id", "at", "metadata_json")
    ID_FIELD_NUMBER: _ClassVar[int]
    ACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    ACCESSED_AS_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    NOTE_ID_FIELD_NUMBER: _ClassVar[int]
    DIRECTORY_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    id: str
    actor_id: str
    accessed_as: AccessedAs
    action: str
    note_id: str
    directory_id: str
    role_id: str
    at: _timestamp_pb2.Timestamp
    metadata_json: str
    def __init__(self, id: _Optional[str] = ..., actor_id: _Optional[str] = ..., accessed_as: _Optional[_Union[AccessedAs, str]] = ..., action: _Optional[str] = ..., note_id: _Optional[str] = ..., directory_id: _Optional[str] = ..., role_id: _Optional[str] = ..., at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., metadata_json: _Optional[str] = ...) -> None: ...

class ActivityScore(_message.Message):
    __slots__ = ("note_id", "score", "title", "stripped_content")
    NOTE_ID_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    STRIPPED_CONTENT_FIELD_NUMBER: _ClassVar[int]
    note_id: str
    score: float
    title: str
    stripped_content: str
    def __init__(self, note_id: _Optional[str] = ..., score: _Optional[float] = ..., title: _Optional[str] = ..., stripped_content: _Optional[str] = ...) -> None: ...

class ActivityFilter(_message.Message):
    __slots__ = ("note_id", "actor_id", "role_id", "accessed_as", "actions", "days", "limit", "offset", "unique_per_day", "directory_ids")
    NOTE_ID_FIELD_NUMBER: _ClassVar[int]
    ACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_ID_FIELD_NUMBER: _ClassVar[int]
    ACCESSED_AS_FIELD_NUMBER: _ClassVar[int]
    ACTIONS_FIELD_NUMBER: _ClassVar[int]
    DAYS_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    UNIQUE_PER_DAY_FIELD_NUMBER: _ClassVar[int]
    DIRECTORY_IDS_FIELD_NUMBER: _ClassVar[int]
    note_id: str
    actor_id: str
    role_id: str
    accessed_as: AccessedAs
    actions: _containers.RepeatedScalarFieldContainer[str]
    days: int
    limit: int
    offset: int
    unique_per_day: bool
    directory_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, note_id: _Optional[str] = ..., actor_id: _Optional[str] = ..., role_id: _Optional[str] = ..., accessed_as: _Optional[_Union[AccessedAs, str]] = ..., actions: _Optional[_Iterable[str]] = ..., days: _Optional[int] = ..., limit: _Optional[int] = ..., offset: _Optional[int] = ..., unique_per_day: _Optional[bool] = ..., directory_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class GetActivityHistoryRequest(_message.Message):
    __slots__ = ("user_id", "filter")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    FILTER_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    filter: ActivityFilter
    def __init__(self, user_id: _Optional[str] = ..., filter: _Optional[_Union[ActivityFilter, _Mapping]] = ...) -> None: ...

class GetMostUsedActivityRequest(_message.Message):
    __slots__ = ("user_id", "filter", "algorithm", "limit")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    FILTER_FIELD_NUMBER: _ClassVar[int]
    ALGORITHM_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    filter: ActivityFilter
    algorithm: MostUsedAlgorithm
    limit: int
    def __init__(self, user_id: _Optional[str] = ..., filter: _Optional[_Union[ActivityFilter, _Mapping]] = ..., algorithm: _Optional[_Union[MostUsedAlgorithm, str]] = ..., limit: _Optional[int] = ...) -> None: ...
