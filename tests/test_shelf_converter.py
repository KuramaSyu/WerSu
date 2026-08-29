"""Unit tests for :mod:`src.grpc_mod.converter.shelf_converter`.

The converters are thin structural translators -- the tests
pin the proto <-> entity mapping, including the UNDEFINED /
None / value semantics on optional fields and the
BootstrapStrategy proto <-> enum mapping.
"""

from __future__ import annotations

from src.api.other.undefined import UNDEFINED
from src.api.services.shelf_service import BootstrapStrategy
from src.db.entities.shelf import ShelfEntity
from src.grpc_mod.converter.shelf_converter import (
    grpc_bootstrap_strategy_from_proto,
    grpc_create_shelf_to_entity,
    grpc_update_shelf_to_entity,
)
from src.grpc_mod.proto.shelf_pb2 import (
    BootstrapStrategy as ProtoBS,
    CreateShelfRequest,
    UpdateShelfRequest,
)


def test_create_request_to_entity_forwards_required_and_optional_fields() -> None:
    """``slug`` is required; everything else maps from proto ``HasField``."""
    req = CreateShelfRequest(user_id="u-1", slug="my shelf")
    req.display_name = "My Shelf"
    req.description = "desc"
    req.image_url = "http://img"
    req.readme_note_id = "n-1"

    entity = grpc_create_shelf_to_entity(req)
    assert entity.id is UNDEFINED
    assert entity.slug == "my shelf"
    assert entity.display_name == "My Shelf"
    assert entity.description == "desc"
    assert entity.image_url == "http://img"
    assert entity.readme_note_id == "n-1"


def test_create_request_to_entity_leaves_unset_fields_undefined() -> None:
    """Optional fields the caller didn't set stay UNDEFINED."""
    req = CreateShelfRequest(user_id="u-1", slug="only-slug")
    entity = grpc_create_shelf_to_entity(req)
    assert entity.slug == "only-slug"
    assert entity.display_name is UNDEFINED
    assert entity.description is UNDEFINED
    assert entity.image_url is UNDEFINED
    assert entity.readme_note_id is UNDEFINED


def test_create_request_to_entity_treats_explicit_empty_as_none() -> None:
    """``HasField`` is True for an explicit empty string -> ``None``.

    Surfaces the caller's intent to clear the column.  Mirrors
    the existing rule converter behaviour.
    """
    req = CreateShelfRequest(user_id="u-1", slug="s")
    req.display_name = ""  # explicit empty
    entity = grpc_create_shelf_to_entity(req)
    assert entity.display_name is None


def test_update_request_to_entity_only_sets_fields_present() -> None:
    """``HasField`` drives whether the field carries through to the entity."""
    req = UpdateShelfRequest(user_id="u-1", id="s-1")
    req.slug = "new-slug"
    req.description = "new desc"
    entity = grpc_update_shelf_to_entity(req)
    assert entity.id == "s-1"
    assert entity.slug == "new-slug"
    assert entity.description == "new desc"
    # Untouched optional fields stay UNDEFINED.
    assert entity.display_name is UNDEFINED
    assert entity.image_url is UNDEFINED


def test_update_request_to_entity_with_only_id_emits_bare_entity() -> None:
    """An update with only the id carries every other field as UNDEFINED."""
    req = UpdateShelfRequest(user_id="u-1", id="s-1")
    entity = grpc_update_shelf_to_entity(req)
    assert entity.id == "s-1"
    assert entity.slug is UNDEFINED
    assert entity.display_name is UNDEFINED


def test_bootstrap_strategy_proto_to_domain_unknown_becomes_none() -> None:
    """Forward-compatible additions map to ``BootstrapStrategy.NONE``."""
    assert (
        grpc_bootstrap_strategy_from_proto(ProtoBS.BOOTSTRAP_STRATEGY_UNSPECIFIED)
        == BootstrapStrategy.NONE
    )
    assert (
        grpc_bootstrap_strategy_from_proto(ProtoBS.BOOTSTRAP_STRATEGY_NONE)
        == BootstrapStrategy.NONE
    )
    assert (
        grpc_bootstrap_strategy_from_proto(ProtoBS.BOOTSTRAP_STRATEGY_ZETTELKASTEN)
        == BootstrapStrategy.ZETTELKASTEN
    )
    # 99 is a value the server doesn't know -- must NOT raise.
    assert grpc_bootstrap_strategy_from_proto(99) == BootstrapStrategy.NONE