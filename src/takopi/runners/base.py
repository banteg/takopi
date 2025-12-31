from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict, TypeAlias

EngineId: TypeAlias = Literal["codex", "claude", "mock"]

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
    ok: bool


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
        resume: str | None,
        on_event: EventSink | None = None,
    ) -> RunResult: ...


class EventQueue:
    def __init__(self, on_event: EventSink, *, label: str = "runner") -> None:
        self._on_event = on_event
        self._label = label
        self._queue: asyncio.Queue[TakopiEvent | None] = asyncio.Queue()
        self._closed = False
        self._task = asyncio.create_task(self._drain())

    def emit(self, event: TakopiEvent) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except Exception as e:  # pragma: no cover - defensive
            logger.info("[%s][on_event] enqueue error: %s", self._label, e)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(None)
        try:
            await self._task
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            logger.info("[%s][on_event] drain error: %s", self._label, e)

    async def _drain(self) -> None:
        while True:
            event = await self._queue.get()
            if event is None:
                return
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
            except asyncio.CancelledError:
                return
            except Exception as e:  # pragma: no cover - defensive
                logger.info("[%s][on_event] callback error: %s", self._label, e)
