"""Msgspec models and decoder for Kimi Code CLI stream-json output."""

from __future__ import annotations

from typing import Any

import msgspec


class ToolCallFunction(msgspec.Struct, forbid_unknown_fields=False):
    """Function call details within a tool call."""

    name: str
    arguments: str  # JSON string of arguments


class ToolCall(msgspec.Struct, forbid_unknown_fields=False):
    """A tool call made by the assistant."""

    type: str  # "function"
    id: str
    function: ToolCallFunction


class UserMessage(
    msgspec.Struct, tag="user", tag_field="role", forbid_unknown_fields=False
):
    """User message in the conversation."""

    content: str


class AssistantMessage(
    msgspec.Struct, tag="assistant", tag_field="role", forbid_unknown_fields=False
):
    """Assistant message, optionally with tool calls."""

    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class ToolMessage(
    msgspec.Struct, tag="tool", tag_field="role", forbid_unknown_fields=False
):
    """Tool execution result message."""

    tool_call_id: str
    content: str | list[dict[str, Any]] | None = None


class SystemMessage(
    msgspec.Struct, tag="system", tag_field="role", forbid_unknown_fields=False
):
    """System message (may appear in stream)."""

    content: str


type StreamJsonMessage = UserMessage | AssistantMessage | ToolMessage | SystemMessage


STREAM_JSON_SCHEMA = msgspec.json.schema(StreamJsonMessage)

_DECODER = msgspec.json.Decoder(StreamJsonMessage)


def decode_stream_json_line(line: str | bytes) -> StreamJsonMessage:
    """Decode a single JSONL line from Kimi stream-json output."""
    return _DECODER.decode(line)
