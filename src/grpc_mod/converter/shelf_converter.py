"""Proto-to-entity converters for the shelf service.

Mirrors :mod:`src.grpc_mod.converter.rule_converter`: inbound gRPC
messages are translated into the domain entities the shelf service
expects, and outbound :class:`~src.db.entities.shelf.ShelfEntity`
instances are converted back via the visitor
(``visit_shelf`` lives on
:class:`src.grpc_mod.converter.grpc_visitor.ConvertToGrpcVisitor`).

Two helpers live here:

* :func:`grpc_create_shelf_to_entity` -- turn a
  :class:`CreateShelfRequest` into a :class:`ShelfEntity`.
* :func:`grpc_update_shelf_to_entity` -- turn an
  :class:`UpdateShelfRequest` into a partial :class:`ShelfEntity`,
  preserving the existing row's fields wherever the request
  leaves the optional ``optional *`` field unset.
* :func:`grpc_bootstrap_strategy_from_proto` -- translate the
  :class:`BootstrapStrategy` proto enum into the domain
  :class:`~src.api.services.shelf_service.BootstrapStrategy` value.

The shape conversion is intentionally thin: validation lives in
the service layer; the converter only does structural translation.
"""

from __future__ import annotations

from typing import Any

from src.api.other.undefined import UNDEFINED, UndefinedOr
from src.api.services.shelf_service import BootstrapStrategy
from src.db.entities.shelf import ShelfEntity
from src.grpc_mod.proto.shelf_pb2 import (
    BootstrapStrategy as ProtoBS,
    CreateShelfRequest,
    UpdateShelfRequest,
)


def grpc_create_shelf_to_entity(request: CreateShelfRequest) -> ShelfEntity:
    """Build a :class:`ShelfEntity` from a :class:`CreateShelfRequest`.

    Args:
        request: inbound gRPC message.  ``user_id`` is **not**
            consumed here -- the service layer pulls it off the
            request to build a ``UserContext``.

    Returns:
        :class:`ShelfEntity`: a partial entity suitable for
        :meth:`src.api.services.shelf_service.ShelfServiceABC.create_shelf`.
        ``id`` is left as :obj:`~src.api.undefined.UNDEFINED`; the
        repo mints a fresh UUID.
    """
    return ShelfEntity(
        id=UNDEFINED,
        slug=request.slug,
        display_name=_optional_string(request, "display_name"),
        description=_optional_string(request, "description"),
        image_url=_optional_string(request, "image_url"),
        readme_note_id=_optional_string(request, "readme_note_id"),
    )


def grpc_update_shelf_to_entity(request: UpdateShelfRequest) -> ShelfEntity:
    """Build a :class:`ShelfEntity` from an :class:`UpdateShelfRequest`.

    Only fields explicitly set on the request are set on the
    returned entity; everything else is left as :obj:`~src.api.undefined.UNDEFINED`
    so the service / repo ``update`` path knows to skip them.

    Args:
        request: inbound gRPC message.

    Returns:
        :class:`ShelfEntity`: a partial entity suitable for
        :meth:`src.api.services.shelf_service.ShelfServiceABC.update_shelf`.
        ``entity.id`` is always set (required for update).
    """
    out = ShelfEntity(id=request.id)

    if request.HasField("slug"):
        out.slug = request.slug
    if request.HasField("display_name"):
        out.display_name = request.display_name
    if request.HasField("description"):
        out.description = request.description
    if request.HasField("image_url"):
        out.image_url = request.image_url
    if request.HasField("readme_note_id"):
        out.readme_note_id = request.readme_note_id or UNDEFINED

    return out


def grpc_bootstrap_strategy_from_proto(raw: int) -> BootstrapStrategy:
    """Translate the proto enum int into a :class:`BootstrapStrategy`.

    ``BOOTSTRAP_STRATEGY_UNSPECIFIED`` and ``BOOTSTRAP_STRATEGY_NONE``
    both map to :data:`BootstrapStrategy.NONE` (the proto's
    ``UNSPECIFIED`` is the zero value -- explicit ``NONE`` is the
    caller's way of saying "no strategy").  Any other value
    (including forward-compatible additions the server doesn't
    recognise) maps to :data:`BootstrapStrategy.NONE` so the
    caller never lands on an unknown branch.
    """
    if raw == ProtoBS.BOOTSTRAP_STRATEGY_ZETTELKASTEN:
        return BootstrapStrategy.ZETTELKASTEN
    return BootstrapStrategy.NONE


def _optional_string(
    request: Any,
    field_name: str,
) -> UndefinedOr[str | None]:
    """Read an ``optional string`` proto field as either value or None.

    ``HasField`` returns ``True`` when the caller supplied the
    field (including an explicit empty string); returns ``False``
    when the caller left it unset.  We surface UNDEFINED in the
    latter case so the repo's UNDEFINED / None / value semantics
    apply.
    """
    if not request.HasField(field_name):
        return UNDEFINED
    value = getattr(request, field_name)
    if not value:
        return None
    return str(value)


__all__ = [
    "grpc_bootstrap_strategy_from_proto",
    "grpc_create_shelf_to_entity",
    "grpc_update_shelf_to_entity",
]