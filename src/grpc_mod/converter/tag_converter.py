"""Proto helpers for the tag service.

Outbound :class:`~src.db.entities.note.tag.TagEntity` conversion lives
on :class:`src.grpc_mod.converter.grpc_visitor.ConvertToGrpcVisitor`
(``visit_tag``).  This module only holds the inbound translation for
the partial-update request, where the difference between an omitted
``optional`` field and an explicitly-set one matters.

The shape conversion is intentionally thin: validation lives in the
service layer; the converter only does structural translation.
"""

from __future__ import annotations

from typing import Dict, Optional

from src.grpc_mod.proto.tag_pb2 import UpdateTagRequest


def grpc_update_tag_fields(
    request: UpdateTagRequest,
) -> Dict[str, Optional[str]]:
    """Return only the fields the caller explicitly set.

    Proto3 ``optional string`` fields report presence via
    ``HasField``: ``True`` means the caller supplied a value
    (including an explicit empty string), ``False`` means the field
    was omitted.  Only present fields are returned so the service /
    repo ``update`` path knows to leave the rest untouched.

    Args:
        request: inbound :class:`UpdateTagRequest`.  ``id`` and
            ``user_id`` are not consumed here.

    Returns:
        Dict[str, Optional[str]]: mapping of ``"slug"`` /
        ``"display_name"`` to their new value, containing only the
        keys whose field was set.
    """
    fields: Dict[str, Optional[str]] = {}
    if request.HasField("slug"):
        fields["slug"] = request.slug
    if request.HasField("display_name"):
        fields["display_name"] = request.display_name
    return fields


__all__ = ["grpc_update_tag_fields"]
