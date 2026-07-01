"""Tests for `src.utils.async_ttl`.

The cache exposes an injectable `timer`, so expiry is driven by a fake
clock rather than real sleeps.
"""
from typing import Callable

import pytest

from src.utils.async_ttl import AsyncTtlCacheInfo, async_ttl


class FakeClock:
    """Manually advanced clock for deterministic TTL tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_caches_result_and_counts_hits_and_misses() -> None:
    clock = FakeClock()

    @async_ttl(ttl=60, timer=clock)
    async def square(x: int) -> int:
        square.calls += 1
        return x * x
    square.calls = 0

    assert await square(3) == 9
    assert await square(3) == 9
    assert await square(3) == 9
    assert square.calls == 1

    info: AsyncTtlCacheInfo = square.cache_info()
    assert info.hits == 2
    assert info.misses == 1
    assert info.expires == 0
    assert info.currsize == 1
    assert info.ttl == 60
    assert info.maxsize is None


async def test_entry_expires_after_ttl() -> None:
    clock = FakeClock()

    @async_ttl(ttl=10, timer=clock)
    async def fn(x: int) -> int:
        fn.calls += 1
        return x
    fn.calls = 0

    assert await fn(1) == 1
    clock.advance(5)
    assert await fn(1) == 1  # still fresh
    clock.advance(6)         # total 11 -> past TTL
    assert await fn(1) == 1
    assert fn.calls == 2

    info = fn.cache_info()
    assert info.expires == 1
    assert info.misses == 2
    assert info.hits == 1
    assert info.currsize == 1


async def test_maxsize_evicts_least_recently_used() -> None:
    clock = FakeClock()

    @async_ttl(ttl=60, maxsize=2, timer=clock)
    async def fn(x: int) -> int:
        fn.calls += 1
        return x
    fn.calls = 0

    await fn(1)
    await fn(2)
    assert fn.cache_info().currsize == 2

    # Touching 1 makes 2 the LRU; the next insert should evict 2.
    assert await fn(1) == 1
    await fn(3)
    assert fn.cache_info().currsize == 2

    # 2 should now miss, 3 should hit.
    fn.calls = 0
    assert await fn(2) == 2
    assert await fn(3) == 3
    assert fn.calls == 1


async def test_unbounded_cache_when_maxsize_is_none() -> None:
    clock = FakeClock()

    @async_ttl(ttl=60, timer=clock)
    async def fn(x: int) -> int:
        return x

    for i in range(50):
        assert await fn(i) == i

    assert fn.cache_info().currsize == 50
    assert fn.cache_info().maxsize is None


async def test_positional_and_keyword_keys_are_distinct() -> None:
    clock = FakeClock()

    @async_ttl(timer=clock)
    async def fn(*args: int, **kwargs: int) -> tuple:
        fn.calls += 1
        return (args, tuple(sorted(kwargs.items())))
    fn.calls = 0

    # `fn(a=1, b=2)` and `fn((1, 2))` must not collide.
    assert await fn(a=1, b=2) == ((), (("a", 1), ("b", 2)))
    assert await fn(a=1, b=2) == ((), (("a", 1), ("b", 2)))
    assert await fn((1, 2)) == (((1, 2),), ())
    assert fn.calls == 2


async def test_kwargs_order_does_not_change_key() -> None:
    clock = FakeClock()

    @async_ttl(timer=clock)
    async def fn(**kwargs: int) -> int:
        fn.calls += 1
        return sum(kwargs.values())
    fn.calls = 0

    assert await fn(a=1, b=2) == 3
    assert await fn(b=2, a=1) == 3
    assert fn.calls == 1


async def test_bare_decorator_form() -> None:
    clock = FakeClock()

    @async_ttl
    async def fn(x: int) -> int:
        fn.calls += 1
        return x + 1
    fn.calls = 0

    assert await fn(5) == 6
    assert await fn(5) == 6
    assert fn.calls == 1
    # Default TTL is 60s and cache is unbounded.
    assert fn.cache_info().ttl == 60.0
    assert fn.cache_info().maxsize is None


async def test_method_form_uses_self_as_key_segment() -> None:
    clock = FakeClock()

    class Repo:
        def __init__(self) -> None:
            self.calls = 0

        @async_ttl(ttl=60, timer=clock)
        async def get(self, k: str) -> str:
            self.calls += 1
            return k.upper()

    a = Repo()
    b = Repo()

    # Caches are per-instance because self participates in the key.
    assert await a.get("x") == "X"
    assert await a.get("x") == "X"
    assert await b.get("x") == "X"
    assert a.calls == 1
    assert b.calls == 1


async def test_cache_clear_resets_counters_and_entries() -> None:
    clock = FakeClock()

    @async_ttl(ttl=60, timer=clock)
    async def fn(x: int) -> int:
        fn.calls += 1
        return x
    fn.calls = 0

    await fn(1)
    await fn(1)
    fn.calls = 0
    fn.cache_clear()

    info = fn.cache_info()
    assert info.currsize == 0
    assert info.hits == 0
    assert info.misses == 0
    assert info.expires == 0

    # Next call is a miss again.
    await fn(1)
    assert fn.calls == 1
    assert fn.cache_info().misses == 1


async def test_exceptions_are_not_cached() -> None:
    clock = FakeClock()

    call = 0

    @async_ttl(ttl=60, timer=clock)
    async def fn(x: int) -> int:
        nonlocal call
        call += 1
        if call == 1:
            raise RuntimeError("boom")
        return x

    with pytest.raises(RuntimeError, match="boom"):
        await fn(1)

    # Recovery call must execute again, not replay the exception.
    assert await fn(1) == 1
    assert call == 2
    assert fn.cache_info().currsize == 1


async def test_preserves_function_metadata() -> None:
    @async_ttl
    async def my_function(x: int) -> int:
        """Squares the input."""
        return x * x

    assert my_function.__name__ == "my_function"
    assert "Squares the input." in (my_function.__doc__ or "")