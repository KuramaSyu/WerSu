from dataclasses import dataclass

from typing import Any, Dict
import unittest
from unittest import TestCase
from api import UNDEFINED
from api.undefined import UndefinedNoneOr, UndefinedOr
from utils.dict_helper import drop_undefined, drop_except_keys
from utils.convert import asdict

def construct_test_dict() -> Dict[str, Any]:
    return {
        "a": 5, "b": "", "c": None, "d": UNDEFINED
    }

@dataclass
class TestUser:
    name: str
    age: UndefinedNoneOr[int]

class DropUndefinedUseCase(TestCase):
    
    def test_if_undefined_is_dropped(self):
        test_dict = construct_test_dict()
        with self.assertRaises(KeyError) as cm:
            # d should be dropped
            drop_undefined(test_dict)["d"]
        exc = cm.exception
        self.assertEqual(str(exc), "'d'")

    def test_if_none_persists(self):
        test_dict = construct_test_dict()
        self.assertEqual(drop_undefined(test_dict)["c"], None)

    def test_if_empty_string_persists(self):
        test_dict = construct_test_dict()
        self.assertEqual(drop_undefined(test_dict)["b"], "")

    def test_normal_value_persists(self):
        test_dict = construct_test_dict()
        self.assertEqual(drop_undefined(test_dict)["a"], 5)


class DropExceptKeysUseCase(TestCase):

    def test_if_only_defined_keys_persist(self):
        test_dict = construct_test_dict()
        new = drop_except_keys(test_dict, {"a", "c"})
        self.assertEqual(new, {"a": 5, "c": None})

class AsDictDataclassUseCase(TestCase):
    def test_if_undefined_age_is_dropped(self):
        test_user = TestUser("paul", UNDEFINED)
        with self.assertRaises(KeyError) as cm:
            asdict(test_user)["age"]

    def test_if_normal_fields_persist(self):
        test_user = TestUser("paul", 22)
        d = asdict(test_user)
        self.assertEqual(d["age"], 22)
        self.assertEqual(d["name"], "paul")

    def test_if_none_fields_persist(self):
        test_user = TestUser("paul", None)
        d = asdict(test_user)
        self.assertEqual(d["age"], None)
