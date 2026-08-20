"""Tests for the condition dataclass union + (de)serialise round-trip."""

from __future__ import annotations

import pytest

from src.api.events.conditions import (
    AlwaysTrue,
    NoteContentContains,
    NoteTitleContains,
    deserialise_condition,
    serialise_condition,
)


# ---- serialise ------------------------------------------------------------


def test_serialise_always_true():
    assert serialise_condition(AlwaysTrue()) == {
        "type": "always_true",
    }


def test_serialise_note_content_contains():
    cond = NoteContentContains(substring="linux")
    out = serialise_condition(cond)
    assert out == {"type": "note_content_contains", "substring": "linux"}


def test_serialise_note_title_contains():
    cond = NoteTitleContains(substring="hello")
    assert serialise_condition(cond) == {
        "type": "note_title_contains",
        "substring": "hello",
    }


def test_serialise_unknown_variant_raises():
    class Bogus:
        type = "bogus"

    with pytest.raises(TypeError):
        serialise_condition(Bogus())  # type: ignore[arg-type]


# ---- deserialise ----------------------------------------------------------


def test_deserialise_always_true():
    assert deserialise_condition({"type": "always_true"}) == AlwaysTrue()


def test_deserialise_note_content_contains():
    cond = deserialise_condition(
        {"type": "note_content_contains", "substring": "linux"}
    )
    assert cond == NoteContentContains(substring="linux")


def test_deserialise_note_title_contains():
    cond = deserialise_condition(
        {"type": "note_title_contains", "substring": "hi"}
    )
    assert cond == NoteTitleContains(substring="hi")


def test_deserialise_missing_type_raises():
    with pytest.raises(ValueError, match="missing the 'type'"):
        deserialise_condition({})


def test_deserialise_unknown_type_raises():
    with pytest.raises(ValueError, match="unknown condition type"):
        deserialise_condition({"type": "bogus"})


def test_deserialise_content_missing_substring_raises():
    with pytest.raises(ValueError, match="substring"):
        deserialise_condition({"type": "note_content_contains"})


def test_deserialise_title_missing_substring_raises():
    with pytest.raises(ValueError, match="substring"):
        deserialise_condition({"type": "note_title_contains"})


def test_deserialise_empty_substring_raises():
    with pytest.raises(ValueError, match="substring"):
        deserialise_condition({"type": "note_content_contains", "substring": ""})


# ---- round-trip -----------------------------------------------------------


@pytest.mark.parametrize(
    "cond",
    [
        AlwaysTrue(),
        NoteContentContains(substring="linux"),
        NoteContentContains(substring="a long substring with spaces"),
        NoteTitleContains(substring="title fragment"),
    ],
)
def test_round_trip(cond):
    assert deserialise_condition(serialise_condition(cond)) == cond
