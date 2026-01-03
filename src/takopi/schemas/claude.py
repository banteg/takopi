"""Msgspec models and decoder for Claude Code stream-json output."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

import msgspec


class StreamTextBlock(
    msgspec.Struct, tag="text", tag_field="type", forbid_unknown_fields=False
):
    """Text content block."""

    text: str


class StreamThinkingBlock(
    msgspec.Struct, tag="thinking", tag_field="type", forbid_unknown_fields=False
):
    """Thinking content block."""

    thinking: str
    signature: str


class StreamToolUseBlock(
    msgspec.Struct, tag="tool_use", tag_field="type", forbid_unknown_fields=False
):
    """Tool use content block."""

    id: str
    name: str
    input: dict[str, Any]


class StreamToolResultBlock(
    msgspec.Struct, tag="tool_result", tag_field="type", forbid_unknown_fields=False
):
    """Tool result content block."""

    tool_use_id: str
    content: str | list[dict[str, Any]] | None = None
    is_error: bool | None = None


StreamContentBlock: TypeAlias = (
    StreamTextBlock | StreamThinkingBlock | StreamToolUseBlock | StreamToolResultBlock
)


class StreamUserMessageBody(msgspec.Struct, forbid_unknown_fields=False):
    """User message body."""

    role: Literal["user"]
    content: str | list[StreamContentBlock]


class StreamAssistantMessageBody(msgspec.Struct, forbid_unknown_fields=False):
    """Assistant message body."""

    role: Literal["assistant"]
    content: list[StreamContentBlock]
    model: str
    error: str | None = None


class StreamUserMessage(
    msgspec.Struct, tag="user", tag_field="type", forbid_unknown_fields=False
):
    """User message."""

    message: StreamUserMessageBody
    uuid: str | None = None
    parent_tool_use_id: str | None = None
    session_id: str | None = None


class StreamAssistantMessage(
    msgspec.Struct, tag="assistant", tag_field="type", forbid_unknown_fields=False
):
    """Assistant message."""

    message: StreamAssistantMessageBody
    parent_tool_use_id: str | None = None
    uuid: str | None = None
    session_id: str | None = None


class StreamSystemMessage(
    msgspec.Struct, tag="system", tag_field="type", forbid_unknown_fields=False
):
    """System message."""

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
    """Result message."""

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
    """Stream event message for partial updates."""

    uuid: str
    session_id: str
    event: dict[str, Any]
    parent_tool_use_id: str | None = None


class ControlInterruptRequest(
    msgspec.Struct, tag="interrupt", tag_field="subtype", forbid_unknown_fields=False
):
    """Control request to interrupt generation."""


class ControlCanUseToolRequest(
    msgspec.Struct, tag="can_use_tool", tag_field="subtype", forbid_unknown_fields=False
):
    """Control request for tool permission."""

    tool_name: str
    input: dict[str, Any]
    permission_suggestions: list[Any] | None = None
    blocked_path: str | None = None


class ControlInitializeRequest(
    msgspec.Struct, tag="initialize", tag_field="subtype", forbid_unknown_fields=False
):
    """Control request to initialize streaming control protocol."""

    hooks: dict[str, Any] | None = None


class ControlSetPermissionModeRequest(
    msgspec.Struct,
    tag="set_permission_mode",
    tag_field="subtype",
    forbid_unknown_fields=False,
):
    """Control request to update permission mode."""

    mode: str


class ControlHookCallbackRequest(
    msgspec.Struct,
    tag="hook_callback",
    tag_field="subtype",
    forbid_unknown_fields=False,
):
    """Control request to execute a hook callback."""

    callback_id: str
    input: Any
    tool_use_id: str | None = None


class ControlMcpMessageRequest(
    msgspec.Struct, tag="mcp_message", tag_field="subtype", forbid_unknown_fields=False
):
    """Control request to forward an MCP message."""

    server_name: str
    message: Any


class ControlRewindFilesRequest(
    msgspec.Struct, tag="rewind_files", tag_field="subtype", forbid_unknown_fields=False
):
    """Control request to rewind files to a checkpoint."""

    user_message_id: str


ControlRequest: TypeAlias = (
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
    """Envelope for control requests emitted by the CLI."""

    request_id: str
    request: ControlRequest


class ControlSuccessResponse(
    msgspec.Struct, tag="success", tag_field="subtype", forbid_unknown_fields=False
):
    """Control response for successful requests."""

    request_id: str
    response: dict[str, Any] | None = None


class ControlErrorResponse(
    msgspec.Struct, tag="error", tag_field="subtype", forbid_unknown_fields=False
):
    """Control response for failed requests."""

    request_id: str
    error: str


ControlResponse: TypeAlias = ControlSuccessResponse | ControlErrorResponse


class StreamControlResponse(
    msgspec.Struct,
    tag="control_response",
    tag_field="type",
    forbid_unknown_fields=False,
):
    """Envelope for control responses emitted by the CLI."""

    response: ControlResponse


class StreamControlCancelRequest(
    msgspec.Struct,
    tag="control_cancel_request",
    tag_field="type",
    forbid_unknown_fields=False,
):
    """Envelope for control cancellation requests (shape may evolve)."""

    request_id: str | None = None


StreamJsonMessage: TypeAlias = (
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
