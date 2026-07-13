# Copyright (c) 2020 Nekokatt
# Copyright (c) 2021-2025 davfsa
# // copied herefrom hikari
# Copyright (c) 2026-present KuramaSyu
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Singleton used throughout the library to denote values that are not present."""

from __future__ import annotations

__all__: typing.Sequence[str] = (
    "UNDEFINED",
    "UndefinedNoneOr",
    "UndefinedOr",
    "UndefinedType",
    "all_undefined",
    "any_undefined",
    "count_undefined",
    "is_undefined",
    "unwrap_undefined",
)

import typing
import typing_extensions


if typing.TYPE_CHECKING:
    from typing_extensions import Self


class UndefinedType:
    """Type of the :obj:`~src.api.undefined.UNDEFINED` singleton sentinel value."""

    __slots__: typing.Sequence[str] = ()

    def __bool__(self) -> typing.Literal[False]:
        return False

    def __copy__(self) -> Self:
        # This is meant to be a singleton
        return self

    def __deepcopy__(self, memo: typing.MutableMapping[int, typing.Any]) -> Self:
        memo[id(self)] = self

        # This is meant to be a singleton
        return self

    def __getstate__(self) -> bool:
        # Returning False tells pickle to not call __setstate__ on unpickling.
        return False

    def __repr__(self) -> str:
        return "UNDEFINED"

    def __reduce__(self) -> str:
        # Returning a string makes pickle fetch from the module namespace.
        return "UNDEFINED"

    @typing_extensions.override
    def __str__(self) -> str:
        return "UNDEFINED"


UNDEFINED = UndefinedType()
"""Sentinel singleton that denotes a missing or omitted value."""


def _forbidden_new(cls: UndefinedType) -> typing.NoReturn:  # noqa: ARG001 - Unused arguments
    msg = "Cannot initialize multiple instances of singleton UNDEFINED"
    raise TypeError(msg)  # pragma: nocover


UndefinedType.__new__ = _forbidden_new  # type: ignore[method-assign]
del _forbidden_new

T_co = typing.TypeVar("T_co", covariant=True)
UndefinedOr = typing.Union[T_co, UndefinedType]
"""Type hint to mark a type as semantically optional.

!!! warning "NOT THE SAME AS :class:`typing.Optional` BY DEFINITION!"
    A value typed :data:`UndefinedOr` may be :obj:`~src.api.undefined.UNDEFINED`
    or the wrapped type.  For example, ``UndefinedOr[float]`` means the value
    could be a :class:`float` or the literal :obj:`~src.api.undefined.UNDEFINED`
    sentinel.  ``Optional[float]`` on the other hand means :class:`float` or
    :obj:`None`.

    The distinction matters when receiving data from a client and converting
    it to a database entity.  :obj:`~src.api.undefined.UNDEFINED` means the
    field is missing (use the default); :obj:`None` means the field was
    explicitly sent as ``NULL``.  Without this split, ``None`` cannot be
    persisted as a real value.

    Think of :data:`UndefinedOr` as the JS ``undefined`` vs ``null``
    distinction, or Java/C# ``Optional<T>`` vs ``null``.

!!! note
    * :obj:`~src.api.undefined.UNDEFINED` -> no value present, use the default.
    * :obj:`None` -> value present and explicitly empty / null / void, with
      a deterministic, documented behaviour that does not depend on whether
      the field was omitted.
"""

UndefinedNoneOr = typing.Union[UndefinedOr[T_co], None]
"""Type hint for a value that may be :obj:`~src.api.undefined.UNDEFINED`, :obj:`None`, or ``T``.

Shortcut for ``UndefinedOr[typing.Optional[T]]``, i.e.
``typing.Union[UndefinedType, T, None]``.
"""


def all_undefined(*items: object) -> bool:
    """Return whether every provided item is :obj:`~src.api.undefined.UNDEFINED`."""
    return all(item is UNDEFINED for item in items)


def any_undefined(*items: object) -> bool:
    """Return whether any provided item is :obj:`~src.api.undefined.UNDEFINED`."""
    return any(item is UNDEFINED for item in items)


def count_undefined(*items: object) -> int:
    """Count how many of the provided items are :obj:`~src.api.undefined.UNDEFINED`."""
    return sum(item is UNDEFINED for item in items)

def is_undefined(item: UndefinedOr[T_co] | UndefinedNoneOr[T_co]) -> bool:
    """Return whether ``item`` is :obj:`~src.api.undefined.UNDEFINED`."""
    return item is UNDEFINED

def unwrap_undefined(item: UndefinedOr[T_co]) -> T_co:
    """Return ``item`` if it is not :obj:`~src.api.undefined.UNDEFINED`.

    Raises:
        ValueError: if ``item`` is :obj:`~src.api.undefined.UNDEFINED`.
    """
    if item is UNDEFINED:
        raise ValueError("Cannot unwrap UNDEFINED value")
    return item  # type: ignore[return-value]

T_default = typing.TypeVar("T_default")
def unwrap_undefined_or(item: UndefinedNoneOr[T_co], default: T_default = None) -> T_co | T_default:
    """Return ``item`` if it is not :obj:`~src.api.undefined.UNDEFINED`, otherwise ``default``.

    Args:
        item: value to unwrap; may be :obj:`~src.api.undefined.UNDEFINED`, :obj:`None`, or ``T``.
        default: fallback returned when ``item`` is :obj:`~src.api.undefined.UNDEFINED`.
            ``None`` by default.  Note that ``None`` here is treated as the
            fallback value, not as a sentinel for "value not provided".
    """
    if item is UNDEFINED:
        return default
    return item  # type: ignore[return-value]