"""Helpers that translate gRPC ``oneof``/``optional`` wrappers into :data:`UndefinedOr` / :data:`UndefinedNoneOr`."""

from __future__ import annotations

from typing import Any, List

from google.protobuf.message import Message

from src.api.other.undefined import (
    UNDEFINED,
    UndefinedNoneOr,
    UndefinedOr,
    is_undefined,
)


def grpc_unwrap_oneof(
    request: Message,
    oneof_field_name: str,
    oneof_inner_field_name: str = "ids",
) -> UndefinedOr[List[Any]]:
    """Return the inner ``oneof`` arm as a list, or :obj:`UNDEFINED`.

    The unset branch (no arm selected) maps to :data:`UNDEFINED`
    so repo update loops can skip the column.  There is no
    "explicit-null list" case in proto3, so the only two return
    shapes are :data:`UNDEFINED` or a concrete list.
    """
    which = request.WhichOneof(oneof_field_name)
    if which is None:
        return UNDEFINED
    return list(getattr(request, which).__getattribute__(oneof_inner_field_name))


def grpc_unwrap_optional(
    request: Message,
    oneof_field_name: str,
) -> UndefinedNoneOr[str]:
    """Return the value of a proto3 ``optional`` field, or :data:`UNDEFINED`.

    Unset fields surface as :data:`UNDEFINED` so callers can
    distinguish "not provided" from a concrete empty string.
    The field is read via :func:`getattr` (not subscript) to
    stay compatible with both protoc and betterproto messages.
    """
    if not request.HasField(oneof_field_name):
        return UNDEFINED
    value: Any = getattr(request, oneof_field_name)
    return value


def grpc_unwrap_tri_state(
    request: Message,
    oneof_field_name: str,
    set_arm: str,
    clear_arm: str,
    clear_value: Any = None,
) -> UndefinedNoneOr[Any]:
    """Collapse a ``_set`` / ``_clear`` ``oneof`` into tri-state entity value.

    * no arm selected  -> :data:`UNDEFINED` (skip the column).
    * ``set_arm``      -> the inner string value (may be ``""``).
    * ``clear_arm``    -> ``clear_value`` (default ``None``, i.e. write NULL).
      Pass e.g. ``""`` when the REST contract encodes 'unset' as an
      empty string rather than NULL.

    The returned :data:`UndefinedNoneOr` slots straight into an
    entity field typed ``UndefinedNoneOr[str]`` so the repo's
    partial-update loop can distinguish all three states.
    """
    which = request.WhichOneof(oneof_field_name)
    if which is None or is_undefined(which):
        return UNDEFINED
    if which == set_arm:
        value: Any = getattr(request, set_arm)
        return value
    if which == clear_arm:
        return clear_value
    return UNDEFINED