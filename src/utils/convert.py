from dataclasses import fields, is_dataclass, MISSING
from typing import Any, Dict, Tuple
from api.undefined import UNDEFINED, UndefinedOr, UndefinedNoneOr


def asdict(obj: Any, *, dict_factory: type = dict) -> Dict[str, Any]:
    """Convert a dataclass instance to a dictionary, excluding UNDEFINED values.
    
    This is similar to dataclasses.asdict() but with one key difference:
    fields with UNDEFINED values are omitted from the resulting dictionary.
    
    Args:
        obj: A dataclass instance to convert.
        dict_factory: A callable to create the dictionary. Defaults to dict.
    
    Returns:
        A dictionary representation of the dataclass, excluding UNDEFINED fields.
    
    Example:
        >>> @dataclass
        ... class User:
        ...     name: str
        ...     email: UndefinedOr[str] = UNDEFINED
        >>> user = User(name="Alice")
        >>> asdict(user)
        {'name': 'Alice'}  # email is excluded
    """
    if not is_dataclass(obj):
        raise TypeError("asdict() should be called on dataclass instances")
    
    return _asdict_inner(obj, dict_factory)


def _asdict_inner(obj: Any, dict_factory: type) -> Any:
    """Recursively convert dataclass to dict, handling nested dataclasses."""
    if is_dataclass(obj):
        result = []
        for field in fields(obj):
            value = getattr(obj, field.name)
            # Skip UNDEFINED values
            if value is UNDEFINED:
                continue
            result.append((field.name, _asdict_inner(value, dict_factory)))
        return dict_factory(result)
    elif isinstance(obj, tuple) and hasattr(obj, '_fields'):
        # Handle namedtuples
        return type(obj)(*[_asdict_inner(v, dict_factory) for v in obj])
    elif isinstance(obj, (list, tuple)):
        # Handle lists and tuples
        return type(obj)(_asdict_inner(v, dict_factory) for v in obj)
    elif isinstance(obj, dict):
        # Handle dictionaries, filtering out UNDEFINED values
        return dict_factory(
            (k, _asdict_inner(v, dict_factory))
            for k, v in obj.items()
            if v is not UNDEFINED
        )
    else:
        return obj