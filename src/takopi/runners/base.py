from __future__ import annotations

import inspect
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict, TypeAlias

import anyio
from anyio.abc import TaskGroup

EngineId: TypeAlias = Literal["codex", "mock"]

logger = logging.getLogger(__name__)

ActionKind: TypeAlias = Literal[
    "command",
    "tool",
    "file_change",
    "web_search",
    "note",
]

TakopiEventType: TypeAlias = Literal[
    "session.started",
    "action.started",
    "action.completed",
    "log",
    "error",
]


@dataclass(frozen=True, slots=True)
class ResumeToken:
    engine: EngineId
    value: str


@dataclass(frozen=True, slots=True)
class RunResult:
    resume: ResumeToken
    answer: str


class ResumePayload(TypedDict):
    engine: EngineId
    value: str


class Action(TypedDict, total=False):
    id: str
    kind: ActionKind
    title: str
    detail: Mapping[str, Any]
    ok: bool


class SessionStartedEvent(TypedDict):
    type: Literal["session.started"]
    engine: EngineId
    resume: ResumePayload


class ActionStartedEvent(TypedDict):
    type: Literal["action.started"]
    engine: EngineId
    action: Action


class ActionCompletedEvent(TypedDict):
    type: Literal["action.completed"]
    engine: EngineId
    action: Action


class LogEvent(TypedDict):
    type: Literal["log"]
    engine: EngineId
    level: str
    message: str


class ErrorEvent(TypedDict):
    type: Literal["error"]
    engine: EngineId
    message: str
    fatal: bool


TakopiEvent: TypeAlias = (
    SessionStartedEvent
    | ActionStartedEvent
    | ActionCompletedEvent
    | LogEvent
    | ErrorEvent
)

EventSink: TypeAlias = Callable[[TakopiEvent], Awaitable[None] | None]


class Runner(Protocol):
    engine: EngineId

    def is_resume_line(self, line: str) -> bool: ...

    def format_resume(self, token: ResumeToken) -> str: ...

    def extract_resume(self, text: str | None) -> ResumeToken | None: ...

    async def run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        on_event: EventSink | None = None,
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

    def emit(self, event: TakopiEvent) -> None:
        if self._closed:
            return
        try:
            self._queue.append(event)
            self._event.set()
        except Exception as e:  # pragma: no cover - defensive
            logger.info("[%s][on_event] enqueue error: %s", self._label, e)

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
        try:
            if self._drain_done is not None:
                await self._drain_done.wait()
            if self._tg is not None:
                await self._tg.__aexit__(None, None, None)
                self._tg = None
        except Exception as e:  # pragma: no cover - defensive
            logger.info("[%s][on_event] drain error: %s", self._label, e)

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
                except Exception as e:
                    logger.info("[%s][on_event] callback error: %s", self._label, e)
                    continue
                if res is None:
                    continue
                try:
                    if inspect.isawaitable(res):
                        await res
                    else:
                        logger.info(
                            "[%s][on_event] callback returned non-awaitable result",
                            self._label,
                        )
                except Exception as e:  # pragma: no cover - defensive
                    logger.info("[%s][on_event] callback error: %s", self._label, e)
            if self._closed and not self._queue:
                return
