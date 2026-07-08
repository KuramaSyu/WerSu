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


def _log_service_call_factory(logger_name: str = "src.services", measure_time: bool = True):
    """Decorator factory for logging service method entry/exit and timing.

    Logs at INFO level for entry/exit summary, DEBUG level for detailed args/timing.
    Handles both async coroutine methods and async generator methods (streaming).
    The decorator will prefer a `self.log` logger on the instance if present;
    otherwise it will use `logging.getLogger(logger_name)`.
    """

    def decorator(func):
        is_generator = inspect.isasyncgenfunction(func)

        if is_generator:
            @functools.wraps(func)
            async def generator_wrapper(*args, **kwargs):
                self = args[0] if args else None
                logger = getattr(self, "log", None) or logging.getLogger(logger_name)
                class_name = self.__class__.__name__ if self else ""

                logger.info("Calling %s.%s", class_name, func.__name__)
                try:
                    logger.debug("  args=%s kwargs=%s", args[1:] if self else args, kwargs)
                except Exception:
                    pass

                start = time.perf_counter() if measure_time else None
                try:
                    async for item in func(*args, **kwargs):
                        yield item
                except Exception:
                    try:
                        logger.exception("Exception in %s.%s", class_name, func.__name__)
                    except Exception:
                        pass
                    raise
                finally:
                    if measure_time and start is not None:
                        elapsed = time.perf_counter() - start
                        logger.info("Completed %s.%s in %.3fs", class_name, func.__name__, elapsed)

            return generator_wrapper
        else:
            @functools.wraps(func)
            async def coroutine_wrapper(*args, **kwargs):
                self = args[0] if args else None
                logger = getattr(self, "log", None) or logging.getLogger(logger_name)
                class_name = self.__class__.__name__ if self else ""

                logger.info("Calling %s.%s", class_name, func.__name__)
                try:
                    logger.debug("  args=%s kwargs=%s", args[1:] if self else args, kwargs)
                except Exception:
                    pass

                start = time.perf_counter() if measure_time else None
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception:
                    try:
                        logger.exception("Exception in %s.%s", class_name, func.__name__)
                    except Exception:
                        pass
                    raise
                finally:
                    if measure_time and start is not None:
                        elapsed = time.perf_counter() - start
                        logger.info("Completed %s.%s in %.3fs", class_name, func.__name__, elapsed)

            return coroutine_wrapper

    return decorator


def log_service_call(logger_name: str = "src.services", measure_time: bool = True):
    """Convenience factory used as `@log_service_call()` or `@log_service_call("my.logger")`.

    Returns the actual decorator produced by `_log_service_call_factory`.
    """
    return _log_service_call_factory(logger_name=logger_name, measure_time=measure_time)