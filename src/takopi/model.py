"""Takopi domain model types (events, actions, resume tokens)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, NewType, TypeAlias, TypedDict

EngineId = NewType("EngineId", str)

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
    "run.completed",
]


@dataclass(frozen=True, slots=True)
class ResumeToken:
    engine: EngineId
    value: str


class Action(TypedDict):
    id: str
    kind: ActionKind
    title: str
    detail: dict[str, Any]


class SessionStartedEvent(TypedDict):
    type: Literal["session.started"]
    engine: EngineId
    resume: ResumeToken
    title: str


class ActionStartedEvent(TypedDict):
    type: Literal["action.started"]
    engine: EngineId
    action: Action


class ActionCompletedEvent(TypedDict):
    type: Literal["action.completed"]
    engine: EngineId
    action: Action
    ok: bool


class RunCompletedEvent(TypedDict):
    type: Literal["run.completed"]
    engine: EngineId
    resume: ResumeToken
    answer: str


TakopiEvent: TypeAlias = (
    SessionStartedEvent
    | ActionStartedEvent
    | ActionCompletedEvent
    | RunCompletedEvent
)
