"""Msgspec models and decoder for pi --mode json output."""

from __future__ import annotations

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

_DECODER = msgspec.json.Decoder(PiEvent)


def decode_event(line: str | bytes) -> PiEvent:
    return _DECODER.decode(line)
