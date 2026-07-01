"""TTL cache decorator for async functions.

Caches the resolved return value of an `async def` function for `ttl`
seconds, lazily expiring entries on access. When `maxsize` is set, the
cache also evicts the least-recently-used entry on overflow.

Typical usage:

* ``@async_ttl`` — bare form, defaults: `ttl=60s`, unbounded cache.
* ``@async_ttl(ttl=30, maxsize=256)`` — bounded form with explicit TTL.
* Inject a custom ``timer`` in tests to drive expiry deterministically
  without sleeping.

Example:

```python
from src.utils.async_ttl import async_ttl

@async_ttl(ttl=30, maxsize=1024)
async def fetch_user(user_id: str) -> dict:
    ...

class Repo:
    @async_ttl(ttl=60)
    async def get_note(self, note_id: str) -> NoteEntity:
        ...
```

Parameter reference for :func:`async_ttl`:

* `func`: the async callable being decorated. When `None`, the decorator
  is returned (parameterised form); when given, the function is wrapped
  immediately (bare form).
* `ttl`: lifetime of each cached entry in seconds (default `60.0`).
  Timestamped on insert, treated as stale on the next access after the
  deadline.
* `maxsize`: optional cap on cache size (default unbounded). When set,
  overflow evicts the least-recently-used entry.
* `timer`: callable returning a monotonic seconds value. Defaults to
  :func:`time.monotonic`; override in tests for deterministic expiry.

Concurrency: mutations happen between awaits on a single event loop, so
no extra locking is needed. Concurrent calls for the same uncached key
may run the wrapped coroutine more than once; only the last completed
result is kept.
"""
from collections import OrderedDict
from functools import wraps
from time import monotonic
from typing import Any, Awaitable, Callable, NamedTuple, TypeVar, overload

# Inserted between positional and keyword segments so cache keys never
# collide when args and kwargs would otherwise flatten to the same tuple.
_KW_SENTINEL = object()

R = TypeVar("R")


class AsyncTtlCacheInfo(NamedTuple):
    """Stats for an :func:`async_ttl` cache.

    Mirrors the shape of :func:`functools.lru_cache`'s cache_info result.
    """
    hits: int
    misses: int
    expires: int
    maxsize: int | None
    currsize: int
    ttl: float


def _make_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, ...]:
    """Build a hashable cache key from positional and keyword arguments.

    Requires every argument to be hashable. The sentinel mirrors the
    behaviour of :func:`functools.lru_cache` so `f(1, x=2)` and
    `f((1, x=2))` produce different keys.
    """
    if not kwargs:
        return args
    return args + (_KW_SENTINEL,) + tuple(sorted(kwargs.items()))


@overload
def async_ttl(
    func: Callable[..., Awaitable[R]],
    *,
    ttl: float = 60.0,
    maxsize: int | None = None,
    timer: Callable[[], float] = ...,
) -> Callable[..., Awaitable[R]]: ...
@overload
def async_ttl(
    func: None = None,
    *,
    ttl: float = 60.0,
    maxsize: int | None = None,
    timer: Callable[[], float] = ...,
) -> Callable[[Callable[..., Awaitable[R]]], Callable[..., Awaitable[R]]]: ...
def async_ttl(
    func: Callable[..., Awaitable[R]] | None = None,
    *,
    ttl: float = 60.0,
    maxsize: int | None = None,
    timer: Callable[[], float] = monotonic,
) -> Any:
    """TTL cache decorator for `async def` functions.

    Use directly (`@async_ttl`) or with options (`@async_ttl(ttl=30)`).
    The wrapped coroutine is awaited before its result is stored, so the
    cached value is the resolved return value, not a coroutine object.

    Args:
        func: The async function to wrap. When `None`, a decorator is
            returned (use the `@async_ttl(...)` form). When given, the
            function is wrapped immediately (use the bare `@async_ttl` form).
        ttl: Lifetime of each cached entry in seconds. Defaults to `60.0`.
            Each entry is timestamped on insert and treated as stale on
            the next access after its deadline.
        maxsize: Optional cap on cache size. When set, the cache evicts
            the least-recently-used entry on overflow. Leave as `None`
            for unbounded growth (entries only leave on TTL expiry).
        timer: Callable returning a monotonic float seconds value. Defaults
            to :func:`time.monotonic`. Override in tests to avoid real
            sleeps.

    Returns:
        The wrapped async callable. It exposes `cache_info()` and
        `cache_clear()` mirroring :func:`functools.lru_cache`.

    Note:
        Concurrent coroutines on the same event loop are safe: every
        cache mutation happens between awaits, so no extra locking is
        needed. Concurrent calls for the same uncached key may run the
        wrapped coroutine more than once; only the last completed result
        is kept.
    """
    def decorator(fn: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[R]]:
        cache: OrderedDict[tuple[Any, ...], tuple[R, float]] = OrderedDict()
        hits = 0
        misses = 0
        expires = 0

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> R:
            nonlocal hits, misses, expires
            key = _make_key(args, kwargs)
            now = timer()

            entry = cache.get(key)
            if entry is not None:
                value, expire_at = entry
                if expire_at > now:
                    cache.move_to_end(key)
                    hits += 1
                    return value
                del cache[key]
                expires += 1

            misses += 1
            result = await fn(*args, **kwargs)
            cache[key] = (result, now + ttl)
            cache.move_to_end(key)
            if maxsize is not None:
                while len(cache) > maxsize:
                    cache.popitem(last=False)
            return result

        def cache_info() -> AsyncTtlCacheInfo:
            return AsyncTtlCacheInfo(hits, misses, expires, maxsize, len(cache), ttl)

        def cache_clear() -> None:
            nonlocal hits, misses, expires
            cache.clear()
            hits = 0
            misses = 0
            expires = 0

        wrapper.cache_info = cache_info  # type: ignore[attr-defined]
        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        return wrapper

    if func is None:
        return decorator
    return decorator(func)