"""Proto-to-entity converters for the rule service.

Mirrors the layout of :mod:`src.grpc_mod.converter.from_proto`:
inbound gRPC messages are translated into the domain entities
the rule service layer expects, and outbound
:class:`~src.db.entities.rule.RuleEntity` instances are
converted to gRPC :class:`Rule` messages via the visitor
(``visit_rule`` is added to
:class:`~src.grpc_mod.converter.grpc_visitor.ConvertToGrpcVisitor`).

Two helpers live here:

* :func:`grpc_create_rule_to_entity` -- turn a
  :class:`CreateRuleRequest` into a :class:`RuleEntity`.
* :func:`grpc_update_rule_to_entity` -- turn an
  :class:`UpdateRuleRequest` into a :class:`RuleEntity`,
  preserving the existing row's fields wherever the request
  leaves the optional ``optional *`` field unset.

The shape conversion is intentionally thin: condition / action
JSONB payloads are passed through as ``dict`` (the gRPC
``google.protobuf.Struct`` -> ``dict`` conversion is handled by
``google.protobuf.json_format``).  Validation lives in the
service layer; the converter only does structural translation.
"""

from __future__ import annotations

from typing import Any, Optional

from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

from src.api.other.undefined import UNDEFINED, UndefinedOr
from src.db.entities.rule import AttachedEntityType, RuleEntity
from src.grpc_mod.proto.rule_pb2 import (
    CreateRuleRequest,
    UpdateRuleRequest,
)


def _struct_to_dict(struct: Optional[Struct]) -> dict:
    """Convert a gRPC ``Struct`` to a plain ``dict``.

    An unset / null struct becomes an empty dict so the
    service layer always sees a mapping it can iterate.
    """
    if struct is None:
        return {}
    return MessageToDict(struct, preserving_proto_field_name=True)


def _attached_entity_type(raw: str) -> UndefinedOr[AttachedEntityType]:
    """Coerce the proto string into the typed literal.

    Empty string -> ``UNDEFINED`` (caller did not supply;
    rejected at the service layer since global rules are no
    longer supported).  ``"directory"`` / ``"note"`` / ``"shelf"``
    pass through.  Any other value is treated as ``UNDEFINED`` to
    avoid a "valid" proto message that breaks the service
    validation.
    """
    if not raw:
        return UNDEFINED
    if raw in ("directory", "note", "shelf"):
        return raw  # type: ignore[return-value]
    return UNDEFINED


def grpc_create_rule_to_entity(request: CreateRuleRequest) -> RuleEntity:
    """Build a :class:`RuleEntity` from a :class:`CreateRuleRequest`.

    Args:
        request: inbound gRPC message.  ``user_id`` is **not**
            consumed here -- the service layer pulls it off the
            request to build a ``UserContext``.

    Returns:
        :class:`RuleEntity`: a partial entity suitable for
        :meth:`src.api.services.rule_service.RuleServiceABC.create_rule`.
    """
    return RuleEntity(
        id=UNDEFINED,
        event_type=request.event_type,
        attached_entity_type=_attached_entity_type(request.attached_entity_type),
        attached_entity_id=(
            request.attached_entity_id or UNDEFINED
        ),
        condition=_struct_to_dict(request.condition),
        action_type=request.action_type,
        action_context=_struct_to_dict(request.action_context),
        enabled=(
            request.enabled if request.HasField("enabled") else True
        ),
        creator_id=(
            request.creator_id or UNDEFINED
        ),
    )


def grpc_update_rule_to_entity(request: UpdateRuleRequest) -> RuleEntity:
    """Build a :class:`RuleEntity` from an :class:`UpdateRuleRequest`.

    Only fields explicitly set on the request are set on the
    returned entity; everything else is left as ``UNDEFINED``
    so the service / repo ``update`` path knows to skip them.

    Args:
        request: inbound gRPC message.

    Returns:
        :class:`RuleEntity`: a partial entity suitable for
        :meth:`src.api.services.rule_service.RuleServiceABC.update_rule`.
    """
    out = RuleEntity(id=request.id)

    if request.HasField("event_type"):
        out.event_type = request.event_type
    if request.HasField("attached_entity_type"):
        out.attached_entity_type = _attached_entity_type(
            request.attached_entity_type,
        )
    if request.HasField("attached_entity_id"):
        out.attached_entity_id = request.attached_entity_id or UNDEFINED
    # ``condition`` and ``action_context`` are Structs; they do
    # not support HasField cleanly so we just include them
    # whenever they were provided on the wire.  An explicit
    # empty struct is treated as "leave alone" because clearing
    # the condition / action would always make the rule invalid.
    if request.HasField("condition"):
        out.condition = _struct_to_dict(request.condition)
    if request.HasField("action_type"):
        out.action_type = request.action_type
    if request.HasField("action_context"):
        out.action_context = _struct_to_dict(request.action_context)
    if request.HasField("enabled"):
        out.enabled = request.enabled

    return out


__all__ = [
    "grpc_create_rule_to_entity",
    "grpc_update_rule_to_entity",
]
