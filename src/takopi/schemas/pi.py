"""Msgspec models and decoder for pi --mode json output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

import msgspec


class _Event(msgspec.Struct, tag_field="type", forbid_unknown_fields=False):
    pass


class AgentStart(_Event, tag="agent_start"):
    pass


class AgentEnd(_Event, tag="agent_end"):
    messages: list[dict[str, Any]]


class MessageEnd(_Event, tag="message_end"):
    message: dict[str, Any]


class ToolExecutionStart(_Event, tag="tool_execution_start"):
    toolCallId: str
    toolName: str | None = None
    args: dict[str, Any] = msgspec.field(default_factory=dict)


class ToolExecutionEnd(_Event, tag="tool_execution_end"):
    toolCallId: str
    toolName: str | None = None
    result: Any = None
    isError: bool = False


PiEvent: TypeAlias = (
    AgentStart | AgentEnd | MessageEnd | ToolExecutionStart | ToolExecutionEnd
)


@dataclass(frozen=True)
class NonJsonLine:
    text: str


@dataclass(frozen=True)
class UnknownLine:
    raw: Any


DecodedLine: TypeAlias = PiEvent | NonJsonLine | UnknownLine


def decode_event(line: str | bytes) -> DecodedLine:
    if isinstance(line, str):
        raw_bytes = line.encode("utf-8", errors="replace")
    else:
        raw_bytes = line

    raw_bytes = raw_bytes.strip()
    if not raw_bytes:
        return NonJsonLine(text="")

    try:
        obj = msgspec.json.decode(raw_bytes)
    except Exception:
        return NonJsonLine(text=raw_bytes.decode("utf-8", errors="replace"))

    if not isinstance(obj, dict):
        return UnknownLine(raw=obj)

    try:
        return msgspec.convert(obj, type=PiEvent)
    except (msgspec.ValidationError, TypeError):
        return UnknownLine(raw=obj)
