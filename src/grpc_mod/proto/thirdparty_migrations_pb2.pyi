from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class BookstackBookImportChunk(_message.Message):
    __slots__ = ("user_id", "content")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    content: bytes
    def __init__(self, user_id: _Optional[str] = ..., content: _Optional[bytes] = ...) -> None: ...

class BookstackImportedChapter(_message.Message):
    __slots__ = ("directory_id", "chapter_name", "pages_imported")
    DIRECTORY_ID_FIELD_NUMBER: _ClassVar[int]
    CHAPTER_NAME_FIELD_NUMBER: _ClassVar[int]
    PAGES_IMPORTED_FIELD_NUMBER: _ClassVar[int]
    directory_id: str
    chapter_name: str
    pages_imported: int
    def __init__(self, directory_id: _Optional[str] = ..., chapter_name: _Optional[str] = ..., pages_imported: _Optional[int] = ...) -> None: ...

class BookstackBookImportResponse(_message.Message):
    __slots__ = ("book_directory_id", "chapters", "pages_imported", "attachments_uploaded")
    BOOK_DIRECTORY_ID_FIELD_NUMBER: _ClassVar[int]
    CHAPTERS_FIELD_NUMBER: _ClassVar[int]
    PAGES_IMPORTED_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENTS_UPLOADED_FIELD_NUMBER: _ClassVar[int]
    book_directory_id: str
    chapters: _containers.RepeatedCompositeFieldContainer[BookstackImportedChapter]
    pages_imported: int
    attachments_uploaded: int
    def __init__(self, book_directory_id: _Optional[str] = ..., chapters: _Optional[_Iterable[_Union[BookstackImportedChapter, _Mapping]]] = ..., pages_imported: _Optional[int] = ..., attachments_uploaded: _Optional[int] = ...) -> None: ...
