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
    """The type of the [`hikari.undefined.UNDEFINED`][] singleton sentinel value."""

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
        # Returning False tells pickle to not call [`__setstate__`][] on unpickling.
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
"""A sentinel singleton that denotes a missing or omitted value."""


def _forbidden_new(cls: UndefinedType) -> typing.NoReturn:  # noqa: ARG001 - Unused arguments
    msg = "Cannot initialize multiple instances of singleton UNDEFINED"
    raise TypeError(msg)  # pragma: nocover


UndefinedType.__new__ = _forbidden_new  # type: ignore[method-assign]
del _forbidden_new

T_co = typing.TypeVar("T_co", covariant=True)
UndefinedOr = typing.Union[T_co, UndefinedType]
"""Type hint to mark a type as being semantically optional.

!!! warning "**THIS IS NOT THE SAME AS [`typing.Optional`][] BY DEFINITION!**"
    If you see a type with this marker, it may be [`src.api.undefined.UNDEFINED`][] or
    the value it wraps. For example, `UndefinedOr[float]` would mean the value could
    be a [`float`][], or the literal [`src.api.undefined.UNDEFINED`][] value.

    On the other hand, `typing.Optional[float]` would mean the value could be
    a [`float`][], or the literal [`None`][] value.

    The reason for using this is in some places, there is a semantic difference
    between specifying something as being [`None`][], i.e. "no value", and
    having a default to specify that the value has just not been mentioned. 
    The main reason used, is then receiving data from a client, which is 
    converted to an entity for database. While processing such an entity, `UNDEFINED`
    means, that this field is currently missing where as `None` means, that the field is 
    explicitly set to, in terms of postgres, `NULL`. Hence it's the only way to 
    differentiate if a field has been set, but explicitly set to `None`, or if the 
    field ist just missing.

    Consider `UndefinedOr[T]` semantically equivalent to `undefined` versus
    `null` in JavaScript, or `Optional<T>` versus `null` in Java and C#.


!!! note
    If in doubt, remember:

    - [`src.api.undefined.UNDEFINED`][] means there is no value present, or that it has
        been left to the default value, whatever that would be.
    - [`None`][] means the value is present and explicitly empty/null/void,
        where this has a deterministic documented behaviour and no differentiation
        is made between a [`None`][] value, and one that has been omitted.
"""

UndefinedNoneOr = typing.Union[UndefinedOr[T_co], None]
"""Type hint for a value that may be [src.api.undefined.UNDEFINED], or [`None`][].

`UndefinedNoneOr[T]` is simply an alias for
`UndefinedOr[typing.Optional[T]]`, which would expand to
`typing.Union[UndefinedType, T, None]`.
"""


def all_undefined(*items: object) -> bool:
    """Get if all of the provided items are [`src.api.undefined.UNDEFINED`][]."""
    return all(item is UNDEFINED for item in items)


def any_undefined(*items: object) -> bool:
    """Get if any of the provided items are [`src.api.undefined.UNDEFINED`][]."""
    return any(item is UNDEFINED for item in items)


def count_undefined(*items: object) -> int:
    """Count the number of items that are provided that are [`src.api.undefined.UNDEFINED`][]."""
    return sum(item is UNDEFINED for item in items)

def is_undefined(item: UndefinedOr[T_co] | UndefinedNoneOr[T_co]) -> bool:
    """Check if the provided item is [`src.api.undefined.UNDEFINED`][]."""
    return item is UNDEFINED

def unwrap_undefined(item: UndefinedOr[T_co]) -> T_co:
    """Return T_co if the provided item is not [`src.api.undefined.UNDEFINED`][]."""
    if item is UNDEFINED:
        raise ValueError("Cannot unwrap UNDEFINED value")
    return item