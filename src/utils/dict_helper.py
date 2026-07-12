from typing import Any, Dict
from src.api.undefined import UNDEFINED


def drop_undefined(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively drops fields with value UNDEFINED"""
    return {
        key: drop_undefined(value)
        for key, value in data.items()
        if value is not UNDEFINED
    }

def drop_except_keys(data: Dict[str, Any], keys_to_keep: set[str]) -> Dict[str, Any]:
    """Drops all fields except those specified in keys_to_keep."""
    return {
        key: drop_except_keys(value, keys_to_keep)
        for key, value in data.items()
        if key in keys_to_keep
    }