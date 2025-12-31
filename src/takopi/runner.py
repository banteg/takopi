"""Runner protocol and shared utilities for emitting Takopi events."""

from __future__ import annotations

import inspect
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeAlias

import anyio
from anyio.abc import TaskGroup

from .model import ResumeToken, RunResult, TakopiEvent

logger = logging.getLogger(__name__)

EventSink: TypeAlias = Callable[[TakopiEvent], Awaitable[None] | None]


def _noop_sink(_event: TakopiEvent) -> None:
    return None


NO_OP_SINK: EventSink = _noop_sink


class Runner(Protocol):
    engine: str

    def is_resume_line(self, line: str) -> bool: ...

    def format_resume(self, token: ResumeToken) -> str: ...

    def extract_resume(self, text: str | None) -> ResumeToken | None: ...

    async def run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        on_event: EventSink,
    ) -> RunResult: ...


class EventQueue:
    def __init__(self, on_event: EventSink, *, label: str = "runner") -> None:
        self._on_event = on_event
        self._label = label
        self._queue: deque[TakopiEvent] = deque()
        self._event = anyio.Event()
        self._closed = False
        self._tg: TaskGroup | None = None
        self._drain_done: anyio.Event | None = None
        self._error: BaseException | None = None
        self._error_event = anyio.Event()

    def emit(self, event: TakopiEvent) -> None:
        if self._closed:
            return
        if self._error is not None:
            raise self._error
        self._queue.append(event)
        self._event.set()

    async def start(self) -> None:
        if self._tg is not None:
            return
        self._tg = anyio.create_task_group()
        await self._tg.__aenter__()
        self._drain_done = anyio.Event()
        self._tg.start_soon(self._drain_wrapper)

    async def close(self) -> None:
        if self._closed:
            if self._tg is not None:
                await self._tg.__aexit__(None, None, None)
                self._tg = None
            return
        self._closed = True
        self._event.set()
        if self._drain_done is not None:
            await self._drain_done.wait()
        if self._tg is not None:
            await self._tg.__aexit__(None, None, None)
            self._tg = None
        if self._error is not None:
            raise self._error

    async def _drain_wrapper(self) -> None:
        try:
            await self._drain()
        finally:
            if self._drain_done is not None:
                self._drain_done.set()

    async def _drain(self) -> None:
        while True:
            await self._event.wait()
            self._event = anyio.Event()
            while self._queue:
                event = self._queue.popleft()
                try:
                    res = self._on_event(event)
                    if res is None:
                        continue
                    if inspect.isawaitable(res):
                        await res
                    else:
                        logger.info(
                            "[%s][on_event] callback returned non-awaitable result",
                            self._label,
                        )
                except BaseException as exc:
                    self._error = exc
                    self._error_event.set()
                    raise
            if self._closed and not self._queue:
                return

    async def wait_error(self, done: anyio.Event | None = None) -> None:
        if done is None:
            await self._error_event.wait()
        else:
            async with anyio.create_task_group() as tg:

                async def wait_done() -> None:
                    await done.wait()
                    tg.cancel_scope.cancel()

                async def wait_error() -> None:
                    await self._error_event.wait()
                    tg.cancel_scope.cancel()

                tg.start_soon(wait_done)
                tg.start_soon(wait_error)

        if self._error is not None:
            raise self._error
