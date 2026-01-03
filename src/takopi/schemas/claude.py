"""
Msgspec-based decoder for newline-delimited JSON ("JSONL") emitted by:

  claude -p --output-format stream-json --verbose

This is based on the published Claude Agent SDK message types and the Anthropic
Messages streaming event schema. Unknown fields are ignored. Unknown top-level
lines are returned as UnknownSDKLine so the caller can inspect/update the schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union

import msgspec


# ----------------------------
# Common aliases / primitives
# ----------------------------

UUID = str

PermissionMode = Literal["default", "acceptEdits", "bypassPermissions", "plan"]


# ----------------------------
# Low-level / shared structs
# ----------------------------


class McpServerStatus(msgspec.Struct, forbid_unknown_fields=False):
    name: str
    status: str


class CacheCreationUsage(msgspec.Struct, forbid_unknown_fields=False):
    # Seen in the wild as something like:
    #   {"ephemeral_5m_input_tokens": 430, "ephemeral_1h_input_tokens": 0}
    ephemeral_5m_input_tokens: Optional[int] = None
    ephemeral_1h_input_tokens: Optional[int] = None


class ServerToolUseUsage(msgspec.Struct, forbid_unknown_fields=False):
    # Streaming docs mention server tool use; one known counter is web_search_requests.
    web_search_requests: Optional[int] = None


class Usage(msgspec.Struct, forbid_unknown_fields=False):
    """
    A forgiving usage structure that works for:
    - message.usage in assistant messages (often includes input+output tokens)
    - usage in message_delta streaming events (often includes only output_tokens)
    - result.usage in final result messages (non-null usage)
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    cache_creation: Optional[CacheCreationUsage] = None
    server_tool_use: Optional[ServerToolUseUsage] = None
    service_tier: Optional[str] = None


class SDKPermissionDenial(msgspec.Struct, forbid_unknown_fields=False):
    tool_name: str
    tool_use_id: str
    tool_input: Dict[str, Any]


class ModelUsage(msgspec.Struct, forbid_unknown_fields=False):
    # ModelUsage isn't fully spelled out in the public docs; treat it as "Usage-like".
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    cache_creation: Optional[CacheCreationUsage] = None
    server_tool_use: Optional[ServerToolUseUsage] = None
    service_tier: Optional[str] = None


# ----------------------------
# Tagged union base
# ----------------------------


class _Tagged(msgspec.Struct, tag_field="type", forbid_unknown_fields=False):
    @property
    def type(self) -> str:
        info = msgspec.inspect.type_info(self.__class__)
        tag = getattr(info, "tag", None)
        return tag or ""


# ----------------------------
# Anthropic "Message content" blocks
# ----------------------------


class TextBlock(_Tagged, tag="text"):
    text: str
    # Some models return citations on text blocks.
    citations: Optional[List[Dict[str, Any]]] = None


class ToolUseBlock(_Tagged, tag="tool_use"):
    id: str
    name: str
    input: Dict[str, Any]


class ToolResultBlock(_Tagged, tag="tool_result"):
    tool_use_id: str
    content: Any
    is_error: Optional[bool] = None


class ThinkingBlock(_Tagged, tag="thinking"):
    # Extended thinking blocks appear in streaming; may also appear in final content.
    thinking: str
    signature: Optional[str] = None


class RedactedThinkingBlock(_Tagged, tag="redacted_thinking"):
    # Field names vary across SDKs; keep it permissive.
    redacted_thinking: Optional[str] = None
    data: Optional[str] = None


# Content blocks can evolve; update the schema if new block types appear.
ContentBlock = Union[
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
    RedactedThinkingBlock,
]


# ----------------------------
# Anthropic "message" objects (assistant/user)
# ----------------------------


class APIAssistantMessage(msgspec.Struct, forbid_unknown_fields=False):
    id: str
    type: Literal["message"]
    role: Literal["assistant"]
    content: List[ContentBlock]

    model: Optional[str] = None
    stop_reason: Optional[str] = None
    stop_sequence: Optional[str] = None
    usage: Optional[Usage] = None

    # Present in some outputs (e.g. context editing / management features).
    context_management: Any = None

    # Some SDKs surface an error field on assistant messages.
    error: Any = None


class APIUserMessage(msgspec.Struct, forbid_unknown_fields=False):
    # In the Anthropic API, user messages are typically {role, content}. Claude Code output
    # generally includes "role":"user" but we default it in case it's omitted.
    role: Literal["user"] = "user"
    content: Union[str, List[ContentBlock]] = ""


# ----------------------------
# Claude Agent SDK stream-json line types ("SDKMessage")
# ----------------------------


class SDKSystemInit(msgspec.Struct, forbid_unknown_fields=False):
    type: Literal["system"]
    subtype: Literal["init"]
    uuid: UUID
    session_id: str

    apiKeySource: str
    cwd: str
    tools: List[str]
    mcp_servers: List[McpServerStatus]
    model: str
    permissionMode: PermissionMode
    slash_commands: List[str]
    output_style: str

    # Observed in real outputs; not guaranteed by the docs:
    claude_code_version: Optional[str] = None
    agents: Optional[List[str]] = None
    skills: Optional[List[str]] = None
    plugins: Optional[List[Dict[str, Any]]] = None


class SDKAssistantMessage(msgspec.Struct, forbid_unknown_fields=False):
    type: Literal["assistant"]
    uuid: UUID
    session_id: str
    message: APIAssistantMessage
    parent_tool_use_id: Optional[str] = None

    # Some wrappers/versions add structured tool output alongside tool_result blocks.
    tool_use_result: Optional[Dict[str, Any]] = None


class SDKUserMessage(msgspec.Struct, forbid_unknown_fields=False):
    type: Literal["user"]
    session_id: str
    message: APIUserMessage
    parent_tool_use_id: Optional[str] = None
    uuid: Optional[UUID] = None

    # Some wrappers/versions add structured tool output alongside tool_result blocks.
    tool_use_result: Optional[Dict[str, Any]] = None


class _ResultTagged(msgspec.Struct, tag_field="subtype", forbid_unknown_fields=False):
    pass


class SDKResultSuccess(_ResultTagged, tag="success"):
    type: Literal["result"]
    uuid: UUID
    session_id: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    result: str
    total_cost_usd: float
    usage: Usage
    modelUsage: Dict[str, ModelUsage]
    permission_denials: List[SDKPermissionDenial]
    structured_output: Any = None


class SDKResultErrorBase(_ResultTagged):
    type: Literal["result"]
    uuid: UUID
    session_id: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    total_cost_usd: float
    usage: Usage
    modelUsage: Dict[str, ModelUsage]
    permission_denials: List[SDKPermissionDenial]
    errors: List[str]


class SDKResultErrorMaxTurns(SDKResultErrorBase, tag="error_max_turns"):
    pass


class SDKResultErrorDuringExecution(SDKResultErrorBase, tag="error_during_execution"):
    pass


class SDKResultErrorMaxBudgetUsd(SDKResultErrorBase, tag="error_max_budget_usd"):
    pass


class SDKResultErrorMaxStructuredOutputRetries(
    SDKResultErrorBase, tag="error_max_structured_output_retries"
):
    pass


SDKResultError = Union[
    SDKResultErrorMaxTurns,
    SDKResultErrorDuringExecution,
    SDKResultErrorMaxBudgetUsd,
    SDKResultErrorMaxStructuredOutputRetries,
]

SDKResult = Union[SDKResultSuccess, SDKResultError]


SDKMessage = Union[
    SDKSystemInit,
    SDKAssistantMessage,
    SDKUserMessage,
    SDKResultSuccess,
    SDKResultError,
]


# ----------------------------
# Fallback wrapper for unknown/unparseable lines
# ----------------------------


@dataclass(frozen=True)
class NonJsonLine:
    text: str


@dataclass(frozen=True)
class UnknownSDKLine:
    raw: Any


DecodedLine = Union[SDKMessage, NonJsonLine, UnknownSDKLine]


# ----------------------------
# Public decoding helpers
# ----------------------------


def decode_stream_json_line(line: Union[str, bytes]) -> DecodedLine:
    """
    Decode a single JSONL line from Claude Code's stream-json output.

    - If line parses to a recognized SDK message, returns a typed msgspec.Struct.
    - If line isn't JSON, returns NonJsonLine.
    - If JSON but unrecognized shape/type, returns UnknownSDKLine(raw=obj).

    This function parses JSON once, then uses msgspec.convert for typed validation.
    """
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
        # In practice, stream-json should be JSON on every line, but --verbose or runtime
        # warnings could leak non-JSON to stdout on some systems.
        return NonJsonLine(text=raw_bytes.decode("utf-8", errors="replace"))

    if not isinstance(obj, dict):
        return UnknownSDKLine(raw=obj)

    t = obj.get("type")
    st = obj.get("subtype")

    try:
        if t == "system":
            if st == "init":
                return msgspec.convert(obj, type=SDKSystemInit)
            return UnknownSDKLine(raw=obj)

        if t == "assistant":
            return msgspec.convert(obj, type=SDKAssistantMessage)

        if t == "user":
            return msgspec.convert(obj, type=SDKUserMessage)

        if t == "result":
            return msgspec.convert(obj, type=SDKResult)

        return UnknownSDKLine(raw=obj)

    except (msgspec.ValidationError, TypeError):
        # Schema mismatch — preserve the raw dict to help you update types.
        return UnknownSDKLine(raw=obj)
