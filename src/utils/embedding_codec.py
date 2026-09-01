"""Encode/decode pgvector arrays for storage in a ``text`` column."""

from __future__ import annotations

from typing import List, Protocol, Sequence


class _Tolistable(Protocol):
    """Anything with a 1-D ``tolist()`` (torch / numpy / nested sequence)."""

    def tolist(self) -> Sequence[float]: ...


def tensor_to_str_vec(tensor: _Tolistable) -> str:
    """Serialize a 1-D tensor / numeric iterable as ``[x,y,z]``."""
    return f"[{','.join(str(x) for x in tensor.tolist())}]"


def str_vec_to_list(vec_str: str) -> List[float]:
    """Parse a ``[x,y,z]`` string back into a list of floats.

    An empty / whitespace-only string decodes to ``[]`` so the
    Postgres ``NULL``-or-empty case never raises.
    """
    vec_str = vec_str.strip().lstrip("[").rstrip("]")
    if not vec_str:
        return []
    return [float(x) for x in vec_str.split(",")]


def sequence_to_str_vec(values: Sequence[float]) -> str:
    """Encode an in-memory ``Sequence[float]`` as ``[x,y,z]``."""
    return f"[{','.join(str(x) for x in values)}]"


__all__ = ["tensor_to_str_vec", "str_vec_to_list", "sequence_to_str_vec"]
