"""Takopi domain model types (events, actions, resume tokens)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

type EngineId = str

type ActionKind = Literal[
    "command",
    "tool",
    "file_change",
    "web_search",
    "subagent",
    "note",
    "turn",
    "warning",
    "telemetry",
]

type TakopiEventType = Literal[
    "started",
    "action",
    "completed",
]

type ActionPhase = Literal["started", "updated", "completed"]
type ActionLevel = Literal["debug", "info", "warning", "error"]


@dataclass(frozen=True, slots=True)
class ResumeToken:
    engine: EngineId
    value: str


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    kind: ActionKind
    title: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StartedEvent:
    type: Literal["started"] = field(default="started", init=False)
    engine: EngineId
    resume: ResumeToken
    title: str | None = None
    meta: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ActionEvent:
    type: Literal["action"] = field(default="action", init=False)
    engine: EngineId
    action: Action
    phase: ActionPhase
    ok: bool | None = None
    message: str | None = None
    level: ActionLevel | None = None


@dataclass(frozen=True, slots=True)
class CompletedEvent:
    type: Literal["completed"] = field(default="completed", init=False)
    engine: EngineId
    ok: bool
    answer: str
    resume: ResumeToken | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None


type TakopiEvent = StartedEvent | ActionEvent | CompletedEvent
