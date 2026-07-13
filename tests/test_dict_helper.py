from dataclasses import dataclass
from typing import Any, Dict

import pytest

from tests.stubs.user_context import _UserContext as UserContext
from src.api import UNDEFINED
from src.api.other.undefined import UndefinedNoneOr
from src.utils.dict_helper import drop_undefined, drop_except_keys
from src.utils.convert import asdict


def construct_test_dict() -> Dict[str, Any]:
    return {
        "a": 5,
        "b": "",
        "c": None,
        "d": UNDEFINED,
    }


@dataclass
class User:
    name: str
    age: UndefinedNoneOr[int]


# -------------------------
# drop_undefined tests
# -------------------------

def test_undefined_is_dropped():
    test_dict = construct_test_dict()
    result = drop_undefined(test_dict)

    with pytest.raises(KeyError) as exc:
        _ = result["d"]

    assert str(exc.value) == "'d'"


def test_none_persists():
    test_dict = construct_test_dict()
    result = drop_undefined(test_dict)

    assert result["c"] is None


def test_empty_string_persists():
    test_dict = construct_test_dict()
    result = drop_undefined(test_dict)

    assert result["b"] == ""


def test_normal_value_persists():
    test_dict = construct_test_dict()
    result = drop_undefined(test_dict)

    assert result["a"] == 5


# -------------------------
# drop_except_keys tests
# -------------------------

def test_only_specified_keys_persist():
    test_dict = construct_test_dict()
    result = drop_except_keys(test_dict, {"a", "c"})

    assert result == {"a": 5, "c": None}


# -------------------------
# asdict (dataclass) tests
# -------------------------

def test_undefined_field_is_dropped():
    test_user = User("paul", UNDEFINED)

    with pytest.raises(KeyError):
        _ = asdict(test_user)["age"]


def test_normal_fields_persist():
    test_user = User("paul", 22)
    d = asdict(test_user)

    assert d["age"] == 22
    assert d["name"] == "paul"


def test_none_field_persists():
    test_user = User("paul", None)
    d = asdict(test_user)

    assert d["age"] is None
