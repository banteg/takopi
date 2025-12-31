from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict, TypeAlias

EngineId: TypeAlias = Literal["codex", "mock"]

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

    async def run(
        self,
        prompt: str,
        resume: str | None,
        on_event: EventSink | None = None,
    ) -> RunResult: ...
