from __future__ import annotations


class _expect_value_error:
    """Tiny context manager that asserts a :exc:`ValueError` with a substring.

    Equivalent to ``pytest.raises(ValueError, match=...)`` but lets
    the assertions read top-to-bottom without an extra import in the
    helper section above.
    """

    def __init__(self, needle: str) -> None:
        self._needle = needle

    def __enter__(self) -> "_expect_value_error":
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        assert exc_type is ValueError, f"expected ValueError, got {exc_type!r}: {exc!r}"
        assert self._needle in str(exc), (
            f"expected {self._needle!r} in {str(exc)!r}"
        )
        return True


__all__ = [
    '_expect_value_error',
]
