from typing import Any, Dict, cast
from src.api.other.undefined import UNDEFINED


def drop_undefined(data: Dict[str, Any] | Any) -> Dict[str, Any]:
    """Recursively drops fields with value UNDEFINED"""
    if isinstance(data, dict):
        data = cast(Dict[str, Any], data)
        return {
            key: drop_undefined(value)
            for key, value in data.items()
            if value is not UNDEFINED
        }
    return data 

def drop_except_keys(data: Dict[str, Any], keys_to_keep: set[str]) -> Dict[str, Any]:
    """Drops all fields except those specified in keys_to_keep.

    Non-dict leaf values are passed through unchanged; only dict
    values are recursed into.
    """
    if not isinstance(data, dict):
        return data
    return {
        key: drop_except_keys(value, keys_to_keep)
        for key, value in data.items()
        if key in keys_to_keep
    }