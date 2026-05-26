"""Cursor CLI stream-json schema.

Defines msgspec structs for decoding Cursor Agent CLI ``--output-format stream-json``
events. Derived from observed output of Cursor CLI v2026.01.28.
"""

from __future__ import annotations

from typing import Any, Literal

import msgspec


# -- Nested types --


class TextContent(msgspec.Struct, kw_only=True):
    type: Literal["text"]
    text: str


class AssistantMessage(msgspec.Struct, kw_only=True):
    role: Literal["assistant"]
    content: list[TextContent]


# -- Top-level events (discriminated by "type" field) --


class SystemInit(msgspec.Struct, tag="system", kw_only=True):
    subtype: str  # "init"
    session_id: str
    model: str | None = None
    cwd: str | None = None
    apiKeySource: str | None = None
    permissionMode: str | None = None


class UserMessage(msgspec.Struct, tag="user", kw_only=True):
    message: Any = None
    session_id: str | None = None


class ToolCall(msgspec.Struct, tag="tool_call", kw_only=True):
    subtype: str  # "started" | "completed"
    call_id: str | None = None
    tool_call: dict[str, Any] | None = None
    model_call_id: str | None = None
    session_id: str | None = None
    timestamp_ms: int | None = None


class AssistantResponse(msgspec.Struct, tag="assistant", kw_only=True):
    message: AssistantMessage | None = None
    session_id: str | None = None


class Thinking(msgspec.Struct, tag="thinking", kw_only=True):
    subtype: str  # "delta" | "completed"
    text: str | None = None
    session_id: str | None = None
    timestamp_ms: int | None = None


class Result(msgspec.Struct, tag="result", kw_only=True):
    subtype: str  # "success" | "error"
    result: str | None = None
    duration_ms: int | None = None
    duration_api_ms: int | None = None
    is_error: bool | None = None
    session_id: str | None = None
    request_id: str | None = None


type CursorEvent = (
    SystemInit | UserMessage | ToolCall | AssistantResponse | Thinking | Result
)

_DECODER = msgspec.json.Decoder(CursorEvent)


def decode_event(data: bytes | str) -> CursorEvent:
    return _DECODER.decode(data)
