"""Logging decorator factory used by every gRPC service module.

Each :class:`grpc.aio.ServicerContext` method on the gRPC service classes
wraps its body with ``@log_service_call()`` to get entry/exit logging,
optional timing, and exception capture that survives both regular
coroutines and async generator (streaming) handlers.
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, ParamSpec, TypeVar, overload


P = ParamSpec("P")
R = TypeVar("R")
T = TypeVar("T")


class _LogServiceCall:
    """Decorator returned by `log_service_call` with the configured logger + timing.

    The `__call__` overloads preserve the wrapped function's `ParamSpec`
    and return type so call sites keep their original signature for
    both coroutine methods (`Awaitable[R]`) and async generator
    methods (`AsyncIterator[T]`).
    """

    __slots__ = ("_logger_name", "_measure_time")

    def __init__(self, logger_name: str, measure_time: bool) -> None:
        self._logger_name = logger_name
        self._measure_time = measure_time

    @overload
    def __call__(
        self, func: Callable[P, Awaitable[R]]
    ) -> Callable[P, Awaitable[R]]: ...
    @overload
    def __call__(
        self, func: Callable[P, AsyncIterator[T]]
    ) -> Callable[P, AsyncIterator[T]]: ...
    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap `func` with entry/exit logging, optional timing, and exception capture.

        Logs at INFO level for entry/exit summary, DEBUG level for detailed args/timing.
        Handles both async coroutine methods and async generator methods (streaming).
        The wrapper will prefer a `self.log` logger on the instance if present;
        otherwise it will use `logging.getLogger(logger_name)`.
        """
        logger_name = self._logger_name
        measure_time = self._measure_time
        is_generator = inspect.isasyncgenfunction(func)

        if is_generator:
            @functools.wraps(func)
            async def generator_wrapper(
                *args: Any, **kwargs: Any
            ) -> AsyncIterator[Any]:
                self_obj: Any = args[0] if args else None
                logger: logging.Logger = (
                    getattr(self_obj, "log", None)
                    or getattr(self_obj, "_log", None)
                    or logging.getLogger(logger_name)
                )

                logger.debug("Calling %s", func.__name__)
                try:
                    logger.debug(
                        "  args=%s kwargs=%s",
                        args[1:] if self_obj else args,
                        kwargs,
                    )
                except Exception:
                    pass

                start: float | None = time.perf_counter() if measure_time else None
                try:
                    async for item in func(*args, **kwargs):
                        yield item
                except Exception:
                    try:
                        logger.exception("Exception in %s", func.__name__)
                    except Exception:
                        pass
                    raise
                else:
                    if measure_time and start is not None:
                        elapsed: float = time.perf_counter() - start
                        logger.info(f"[{elapsed*1000:.0f}ms] {func.__name__}")

            return generator_wrapper

        @functools.wraps(func)
        async def coroutine_wrapper(*args: Any, **kwargs: Any) -> Any:
            self_obj: Any = args[0] if args else None
            logger: logging.Logger = (
                getattr(self_obj, "log", None)
                or getattr(self_obj, "_log", None)
                or logging.getLogger(logger_name)
            )

            logger.debug("Calling %s", func.__name__)
            try:
                logger.debug(
                    "  args=%s kwargs=%s",
                    args[1:] if self_obj else args,
                    kwargs,
                )
            except Exception:
                pass

            start: float | None = time.perf_counter() if measure_time else None
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception:
                try:
                    logger.exception("Exception in %s", func.__name__)
                except Exception:
                    pass
                raise
            finally:
                if measure_time and start is not None:
                    elapsed: float = time.perf_counter() - start
                    logger.info(f"[{elapsed*1000:.0f}ms] {func.__name__}")

        return coroutine_wrapper


def log_service_call(
    logger_name: str = "src.services",
    measure_time: bool = True,
) -> _LogServiceCall:
    """Convenience factory used as `@log_service_call()` or `@log_service_call("my.logger")`.

    Returns a decorator that wraps an async coroutine or async generator
    service method with entry/exit logging, optional timing, and
    exception capture. See :class:`_LogServiceCall` for the typing
    contract.
    """
    return _LogServiceCall(logger_name=logger_name, measure_time=measure_time)