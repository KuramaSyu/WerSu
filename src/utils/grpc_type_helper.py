"""Helpers that translate gRPC ``oneof``/``optional`` wrappers into :data:`UndefinedOr`."""

from __future__ import annotations

from typing import Any

from google.protobuf.message import Message

from src.api import UNDEFINED, UndefinedOr


def grpc_unwrap_oneof(
    request: Message,
    oneof_field_name: str,
    oneof_inner_field_name: str = "ids",
) -> UndefinedOr[list[str]]:
    """Return the inner ``oneof`` arm as a list, or :obj:`UNDEFINED`."""
    which = request.WhichOneof(oneof_field_name)
    if which is None:
        return UNDEFINED
    return list(getattr(request, which).__getattribute__(oneof_inner_field_name))


def grpc_unwrap_optional(
    request: Message,
    oneof_field_name: str,
) -> UndefinedOr[str]:
    """Return the value of a proto3 ``optional`` field, or :obj:`UNDEFINED`."""
    if not request.HasField(oneof_field_name):
        return UNDEFINED
    value: Any = getattr(request, oneof_field_name)
    return value