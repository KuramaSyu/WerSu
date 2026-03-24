from dataclasses import dataclass
from typing import Sequence

import pytest

from src.api.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.utils.convert import convert_entity_for_db


@dataclass
class _EntityWithOptionalString:
    title: UndefinedNoneOr[str] = UNDEFINED


@dataclass
class _EntityWithSequence:
    tags: UndefinedOr[Sequence[str]] = UNDEFINED


@dataclass
class _MixedEntity:
    title: UndefinedNoneOr[str] = UNDEFINED
    tags: UndefinedOr[Sequence[str]] = UNDEFINED
    count: int = 0


def test_convert_entity_for_db_returns_same_dataclass_type() -> None:
    entity = _MixedEntity(title="hello", tags=["a"], count=2)

    converted = convert_entity_for_db(entity)

    assert isinstance(converted, _MixedEntity)
    assert type(converted) is type(entity)


def test_convert_entity_for_db_converts_undefined_optional_string_to_none() -> None:
    entity = _EntityWithOptionalString(title=UNDEFINED)

    converted = convert_entity_for_db(entity)

    assert converted.title is None


def test_convert_entity_for_db_converts_undefined_sequence_to_empty_list() -> None:
    entity = _EntityWithSequence(tags=UNDEFINED)

    converted = convert_entity_for_db(entity)

    assert converted.tags == []


def test_convert_entity_for_db_keeps_defined_values_unchanged() -> None:
    entity = _MixedEntity(title="x", tags=("a", "b"), count=7)

    converted = convert_entity_for_db(entity)

    assert converted.title == "x"
    assert converted.tags == ("a", "b")
    assert converted.count == 7


def test_convert_entity_for_db_does_not_mutate_original_entity() -> None:
    entity = _MixedEntity(title=UNDEFINED, tags=UNDEFINED, count=1)

    converted = convert_entity_for_db(entity)

    assert entity.title is UNDEFINED
    assert entity.tags is UNDEFINED
    assert converted.title is None
    assert converted.tags == []


def test_convert_entity_for_db_raises_for_non_dataclass() -> None:
    with pytest.raises(TypeError):
        _ = convert_entity_for_db({"title": UNDEFINED})
