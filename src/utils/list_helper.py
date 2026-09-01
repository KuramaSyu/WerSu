from typing import Any, Sequence, Generic, TypeVar, List, overload

T = TypeVar("T")

@overload
def non_empty(lst: Sequence[T]) -> Sequence[T]: ...

@overload
def non_empty(lst: List[T]) -> List[T]: ...

def non_empty(lst: Sequence[T]) -> Sequence[T]:
    """Non empty items of the list"""
    return [item for item in lst if item]
