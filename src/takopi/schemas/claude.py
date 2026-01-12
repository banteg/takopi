"""Msgspec models and decoder for Claude Code stream-json output."""

from __future__ import annotations

from typing import Any, Literal

import msgspec


class StreamTextBlock(
    msgspec.Struct, tag="text", tag_field="type", forbid_unknown_fields=False
):
    text: str


class StreamThinkingBlock(
    msgspec.Struct, tag="thinking", tag_field="type", forbid_unknown_fields=False
):
    thinking: str
    signature: str


class StreamToolUseBlock(
    msgspec.Struct, tag="tool_use", tag_field="type", forbid_unknown_fields=False
):
    id: str
    name: str
    input: dict[str, Any]


class StreamToolResultBlock(
    msgspec.Struct, tag="tool_result", tag_field="type", forbid_unknown_fields=False
):
    tool_use_id: str
    content: str | list[dict[str, Any]] | None = None
    is_error: bool | None = None


type StreamContentBlock = (
    StreamTextBlock | StreamThinkingBlock | StreamToolUseBlock | StreamToolResultBlock
)


class StreamUserMessageBody(msgspec.Struct, forbid_unknown_fields=False):
    role: Literal["user"]
    content: str | list[StreamContentBlock]


class StreamAssistantMessageBody(msgspec.Struct, forbid_unknown_fields=False):
    role: Literal["assistant"]
    content: list[StreamContentBlock]
    model: str
    error: str | None = None


class StreamUserMessage(
    msgspec.Struct, tag="user", tag_field="type", forbid_unknown_fields=False
):
    message: StreamUserMessageBody
    uuid: str | None = None
    parent_tool_use_id: str | None = None
    session_id: str | None = None


class StreamAssistantMessage(
    msgspec.Struct, tag="assistant", tag_field="type", forbid_unknown_fields=False
):
    message: StreamAssistantMessageBody
    parent_tool_use_id: str | None = None
    uuid: str | None = None
    session_id: str | None = None


class StreamSystemMessage(
    msgspec.Struct, tag="system", tag_field="type", forbid_unknown_fields=False
):
    subtype: str
    session_id: str | None = None
    uuid: str | None = None
    cwd: str | None = None
    tools: list[str] | None = None
    mcp_servers: list[Any] | None = None
    model: str | None = None
    permissionMode: str | None = None
    output_style: str | None = None
    apiKeySource: str | None = None


class StreamResultMessage(
    msgspec.Struct, tag="result", tag_field="type", forbid_unknown_fields=False
):
    subtype: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    session_id: str
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    result: str | None = None
    structured_output: Any = None


class StreamEventMessage(
    msgspec.Struct, tag="stream_event", tag_field="type", forbid_unknown_fields=False
):
    uuid: str
    session_id: str
    event: dict[str, Any]
    parent_tool_use_id: str | None = None


class ControlInterruptRequest(
    msgspec.Struct, tag="interrupt", tag_field="subtype", forbid_unknown_fields=False
):
    pass


class ControlCanUseToolRequest(
    msgspec.Struct, tag="can_use_tool", tag_field="subtype", forbid_unknown_fields=False
):
    tool_name: str
    input: dict[str, Any]
    permission_suggestions: list[Any] | None = None
    blocked_path: str | None = None


class ControlInitializeRequest(
    msgspec.Struct, tag="initialize", tag_field="subtype", forbid_unknown_fields=False
):
    hooks: dict[str, Any] | None = None


class ControlSetPermissionModeRequest(
    msgspec.Struct,
    tag="set_permission_mode",
    tag_field="subtype",
    forbid_unknown_fields=False,
):
    mode: str


class ControlHookCallbackRequest(
    msgspec.Struct,
    tag="hook_callback",
    tag_field="subtype",
    forbid_unknown_fields=False,
):
    callback_id: str
    input: Any
    tool_use_id: str | None = None


class ControlMcpMessageRequest(
    msgspec.Struct, tag="mcp_message", tag_field="subtype", forbid_unknown_fields=False
):
    server_name: str
    message: Any


class ControlRewindFilesRequest(
    msgspec.Struct, tag="rewind_files", tag_field="subtype", forbid_unknown_fields=False
):
    user_message_id: str


type ControlRequest = (
    ControlInterruptRequest
    | ControlCanUseToolRequest
    | ControlInitializeRequest
    | ControlSetPermissionModeRequest
    | ControlHookCallbackRequest
    | ControlMcpMessageRequest
    | ControlRewindFilesRequest
)


class StreamControlRequest(
    msgspec.Struct, tag="control_request", tag_field="type", forbid_unknown_fields=False
):
    request_id: str
    request: ControlRequest


class ControlSuccessResponse(
    msgspec.Struct, tag="success", tag_field="subtype", forbid_unknown_fields=False
):
    request_id: str
    response: dict[str, Any] | None = None


class ControlErrorResponse(
    msgspec.Struct, tag="error", tag_field="subtype", forbid_unknown_fields=False
):
    request_id: str
    error: str


type ControlResponse = ControlSuccessResponse | ControlErrorResponse


class StreamControlResponse(
    msgspec.Struct,
    tag="control_response",
    tag_field="type",
    forbid_unknown_fields=False,
):
    response: ControlResponse


class StreamControlCancelRequest(
    msgspec.Struct,
    tag="control_cancel_request",
    tag_field="type",
    forbid_unknown_fields=False,
):
    request_id: str | None = None


type StreamJsonMessage = (
    StreamUserMessage
    | StreamAssistantMessage
    | StreamSystemMessage
    | StreamResultMessage
    | StreamEventMessage
    | StreamControlRequest
    | StreamControlResponse
    | StreamControlCancelRequest
)


STREAM_JSON_SCHEMA = msgspec.json.schema(StreamJsonMessage)

_DECODER = msgspec.json.Decoder(StreamJsonMessage)


def decode_stream_json_line(line: str | bytes) -> StreamJsonMessage:
    return _DECODER.decode(line)
