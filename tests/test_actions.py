"""Tests for the action dataclass union + (de)serialise round-trip."""

from __future__ import annotations

import pytest

from src.api.events.actions import (
    AddTag,
    AddToDirectory,
    deserialise_action,
    serialise_action,
)


# ---- serialise ------------------------------------------------------------


def test_serialise_add_to_directory():
    action_type, ctx = serialise_action(AddToDirectory(directory_id="d1"))
    assert action_type == "add_to_directory"
    assert ctx == {"type": "add_to_directory", "directory_id": "d1"}


def test_serialise_add_tag():
    action_type, ctx = serialise_action(AddTag(tag_id="t1"))
    assert action_type == "add_tag"
    assert ctx == {"type": "add_tag", "tag_id": "t1"}


def test_serialise_unknown_variant_raises():
    class Bogus:
        type = "bogus"

    with pytest.raises(TypeError):
        serialise_action(Bogus())  # type: ignore[arg-type]


# ---- deserialise ----------------------------------------------------------


def test_deserialise_add_to_directory():
    action = deserialise_action(
        "add_to_directory", {"directory_id": "d1"},
    )
    assert action == AddToDirectory(directory_id="d1")


def test_deserialise_add_tag():
    action = deserialise_action("add_tag", {"tag_id": "t1"})
    assert action == AddTag(tag_id="t1")


def test_deserialise_unknown_type_raises():
    with pytest.raises(ValueError, match="unknown action type"):
        deserialise_action("bogus", {})


def test_deserialise_add_to_directory_missing_id_raises():
    with pytest.raises(ValueError, match="directory_id"):
        deserialise_action("add_to_directory", {})


def test_deserialise_add_to_directory_empty_id_raises():
    with pytest.raises(ValueError, match="directory_id"):
        deserialise_action("add_to_directory", {"directory_id": ""})


def test_deserialise_add_tag_missing_id_raises():
    with pytest.raises(ValueError, match="tag_id"):
        deserialise_action("add_tag", {})


def test_deserialise_add_tag_empty_id_raises():
    with pytest.raises(ValueError, match="tag_id"):
        deserialise_action("add_tag", {"tag_id": ""})


# ---- round-trip -----------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        AddToDirectory(directory_id="d1"),
        AddToDirectory(directory_id="long-id-with-dashes-123"),
        AddTag(tag_id="t1"),
    ],
)
def test_round_trip(action):
    action_type, ctx = serialise_action(action)
    assert deserialise_action(action_type, ctx) == action
