"""Translate database driver errors into API-friendly errors.

The public entry point is the :func:`translate_unique_violation`
decorator factory.  Wrap a repo write method so that an asyncpg
``UniqueViolationError`` on one of the ``fields`` (by default
``email`` / ``username``) becomes a :class:`FriendlyLoginError`
instead of a raw driver error:

```py
class UserPostgresRepo(...):
    @translate_unique_violation(fields=("email", "username"))
    async def insert(self, user: UserEntity) -> UserEntity:
        ...

    @translate_unique_violation(
        "{field} is already in use", fields=("email", "username")
    )
    async def update(self, user: UserEntity) -> UserEntity:
        ...
```

Unknown constraints are re-raised unchanged, so wrapping a method
that also writes unrelated tables is safe.
"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable, Iterable, Optional, TypeVar, Union, cast

from asyncpg.exceptions import UniqueViolationError

from src.api.errors import FriendlyLoginError

# Either a ``str.format`` template with ``{field}`` / ``{value}``
# placeholders or a callable receiving ``(field, value)``.
MessageTemplate = Union[str, Callable[[str, str], str]]
F = TypeVar("F", bound=Callable[..., Any])


def _render_message(
    message: Optional[MessageTemplate], field: str, value: str
) -> Optional[str]:
    """Return the custom message for ``(field, value)``, or ``None``.

    A ``str`` is treated as a format template with ``{field}`` and
    ``{value}`` placeholders.  Note that this cannot be an
    already-evaluated f-string: an f-string is built when the
    decorator is applied, long before the failing value is known.
    A callable receives ``(field, value)`` at raise time.
    """
    if message is None:
        return None
    if callable(message):
        return message(field, value)
    return message.format(field=field, value=value)


def _translate(
    err: UniqueViolationError,
    entity: Any,
    fields: tuple[str, ...],
    message: Optional[MessageTemplate],
) -> FriendlyLoginError:
    """Map ``err`` using ``entity``'s matching attribute.

    The offending column is inferred from the constraint name; the
    value is read off ``entity``.  Unknown constraints re-raise the
    original ``err``.
    """
    constraint = (getattr(err, "constraint_name", None) or "").lower()
    for field in fields:
        if field in constraint:
            value = getattr(entity, field, None) or ""
            return FriendlyLoginError(
                field, value, _render_message(message, field, value)
            )
    raise err


def translate_unique_violation(
    message: Optional[MessageTemplate] = None,
    *,
    fields: Iterable[str],
    entity: Union[int, str] = 1,
) -> Callable[[F], F]:
    """Decorator factory mapping unique violations to login errors.

    Args:
        message: optional message override.  A ``str`` is a format
            template with ``{field}`` / ``{value}`` placeholders; a
            callable receives ``(field, value)``.  ``None`` (the
            default) keeps :class:`FriendlyLoginError`'s built-in
            ``"<field> is already taken: <value>"`` message.
        fields: unique columns checked against the constraint name.
            Required so every decorated method states the
            constraints it is expected to violate.
        entity: where to read the duplicate value from.  An ``int``
            indexes the call's positional arguments (default ``1``,
            i.e. the first argument after ``self``); a ``str`` selects
            a keyword argument by name.

    Returns:
        A decorator that re-raises a translated error with the
        original exception chained via ``raise ... from err``.
    """
    checked_fields = tuple(fields)

    def decorator(func: F) -> F:
        def _entity_from(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            if isinstance(entity, int):
                return args[entity]
            if entity in kwargs:
                return kwargs[entity]
            bound = inspect.signature(func).bind_partial(*args, **kwargs)
            return bound.arguments[entity]

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except UniqueViolationError as err:
                    raise _translate(
                        err, _entity_from(args, kwargs), checked_fields, message
                    ) from err

            return cast(F, async_wrapper)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except UniqueViolationError as err:
                raise _translate(
                    err, _entity_from(args, kwargs), checked_fields, message
                ) from err

        return cast(F, sync_wrapper)

    return decorator


__all__ = ["translate_unique_violation", "MessageTemplate"]
