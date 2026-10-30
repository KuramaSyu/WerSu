"""Tests for the tag proto converters.

Covers the inbound optional-field translation
(:func:`src.grpc_mod.converter.tag_converter.grpc_update_tag_fields`)
and the outbound entity -> proto projection
(:meth:`src.grpc_mod.converter.grpc_visitor.ConvertToGrpcVisitor.visit_tag`).
"""

from __future__ import annotations

from src.api.other.undefined import UNDEFINED
from src.db.entities.note.tag import TagEntity
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.converter.tag_converter import grpc_update_tag_fields
from src.grpc_mod.proto.tag_pb2 import UpdateTagRequest


# ---- grpc_update_tag_fields ----------------------------------------------


def test_update_fields_empty_when_nothing_set() -> None:
    assert grpc_update_tag_fields(
        UpdateTagRequest(user_id="u-1", id="t-1")
    ) == {}


def test_update_fields_includes_slug_when_set() -> None:
    request = UpdateTagRequest(user_id="u-1", id="t-1")
    request.slug = "python"
    assert grpc_update_tag_fields(request) == {"slug": "python"}


def test_update_fields_includes_display_name_when_set() -> None:
    request = UpdateTagRequest(user_id="u-1", id="t-1")
    request.display_name = "Python"
    assert grpc_update_tag_fields(request) == {"display_name": "Python"}


def test_update_fields_includes_both_when_set() -> None:
    request = UpdateTagRequest(user_id="u-1", id="t-1")
    request.slug = "python"
    request.display_name = "Python"
    assert grpc_update_tag_fields(request) == {
        "slug": "python",
        "display_name": "Python",
    }


def test_update_fields_preserves_explicit_empty_string() -> None:
    """An explicitly-cleared field is present (so validation rejects it)."""
    request = UpdateTagRequest(user_id="u-1", id="t-1")
    request.slug = ""
    assert grpc_update_tag_fields(request) == {"slug": ""}


# ---- visit_tag ------------------------------------------------------------


def test_visit_tag_projects_all_fields() -> None:
    visitor = ConvertToGrpcVisitor()
    proto = visitor.visit_tag(
        TagEntity(id="t-1", slug="python", display_name="Python")
    )
    assert proto.id == "t-1"
    assert proto.slug == "python"
    assert proto.display_name == "Python"


def test_visit_tag_treats_undefined_and_none_as_empty() -> None:
    visitor = ConvertToGrpcVisitor()
    proto = visitor.visit_tag(
        TagEntity(id=UNDEFINED, slug=None, display_name=UNDEFINED)
    )
    assert proto.id == ""
    assert proto.slug == ""
    assert proto.display_name == ""
