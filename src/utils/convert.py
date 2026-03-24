from collections.abc import Sequence as AbcSequence
from dataclasses import fields, is_dataclass, replace, MISSING
from typing import Any, Dict, Tuple, TypeVar, get_args, get_origin, get_type_hints
from src.api.undefined import UNDEFINED, UndefinedType, UndefinedOr, UndefinedNoneOr


T = TypeVar("T")


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
    ```py
    @dataclass
    class User:
        name: str
        email: UndefinedOr[str] = UNDEFINED
    
    user = User(name="Alice")
    asdict(user)
    {'name': 'Alice'}  # email is excluded
    ```
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


def convert_entity_for_db(entity: T) -> T:
    """Convert a dataclass entity into DB-safe values.

    Rules
    -----
    - ``UndefinedNoneOr[str]`` with value ``UNDEFINED`` becomes ``None``.
    - ``UndefinedOr[Sequence[T]]`` with value ``UNDEFINED`` becomes ``[]``.
    - Other values are returned unchanged.

    Parameters
    ----------
    entity : Any
        Dataclass entity instance.

    Returns
    -------
    T
        New entity instance of the same type with converted field values.
    """
    if not is_dataclass(entity):
        raise TypeError("convert_entity_for_db() should be called on dataclass instances")

    type_hints = get_type_hints(type(entity))
    out: Dict[str, Any] = {}
    for field in fields(entity):
        value = getattr(entity, field.name)
        annotation = type_hints.get(field.name, field.type)

        if value is UNDEFINED:
            if _is_undefined_none_or_str(annotation):
                out[field.name] = None
                continue
            if _is_undefined_or_sequence(annotation):
                out[field.name] = []
                continue

        out[field.name] = value

    return replace(entity, **out)


def _is_undefined_none_or_str(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is None:
        return False

    args = get_args(annotation)
    has_undefined = any(arg is UndefinedType for arg in args)
    has_none = any(arg is type(None) for arg in args)
    has_str = any(arg is str for arg in args)
    return has_undefined and has_none and has_str


def _is_undefined_or_sequence(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is None:
        return False

    args = get_args(annotation)
    if not any(arg is UndefinedType for arg in args):
        return False

    return any(_is_sequence_annotation(arg) for arg in args if arg is not UndefinedType)


def _is_sequence_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is None:
        return annotation in (list, tuple, AbcSequence)

    return origin in (list, tuple, AbcSequence)