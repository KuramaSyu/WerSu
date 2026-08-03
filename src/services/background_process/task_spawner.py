"""Production :class:`TaskSpawnerABC` / :class:`TaskHandleABC` over :mod:`asyncio`."""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine

from src.api.services.background_process.task import TaskHandleABC, TaskSpawnerABC


class TaskHandleAsyncio(TaskHandleABC):
    """Wraps an :class:`asyncio.Task`."""

    def __init__(self, task: asyncio.Task[Any]) -> None:
        self._task = task

    async def join(self) -> None:
        await self._task

    def cancel(self) -> None:
        if not self._task.done():
            self._task.cancel()

    def is_cancelled(self) -> bool:
        return self._task.cancelled()


class TaskSpawnerAsyncio(TaskSpawnerABC):
    """Wraps :func:`asyncio.create_task`."""

    def __init__(self, name_prefix: str = "background-process") -> None:
        self._name_prefix = name_prefix

    def spawn(self, coro: Coroutine[Any, Any, None]) -> TaskHandleABC:
        task = asyncio.create_task(coro, name=self._name_prefix)
        return TaskHandleAsyncio(task)