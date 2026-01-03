"""
Msgspec-based decoder for newline-delimited JSON ("JSONL") emitted by:

  claude -p --output-format stream-json --verbose

This schema mirrors the public Claude Agent SDK message types. Unknown fields are
ignored, and unknown lines are returned as UnknownSDKLine so callers can inspect
and update the schema as needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import msgspec


# ----------------------------
# Common aliases / primitives
# ----------------------------

UUID = str


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


class ThinkingBlock(_Tagged, tag="thinking"):
    thinking: str
    signature: Optional[str] = None


class ToolUseBlock(_Tagged, tag="tool_use"):
    id: str
    name: str
    input: Dict[str, Any]


class ToolResultBlock(_Tagged, tag="tool_result"):
    tool_use_id: str
    content: str | List[Dict[str, Any]] | None = None
    is_error: Optional[bool] = None


ContentBlock = Union[TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock]


# ----------------------------
# Claude Agent SDK stream-json line types (mirroring SDK types)
# ----------------------------


class SDKUserMessage(msgspec.Struct, forbid_unknown_fields=False):
    content: str | List[ContentBlock]
    uuid: Optional[UUID] = None
    parent_tool_use_id: Optional[str] = None
    session_id: Optional[str] = None


class SDKAssistantMessage(msgspec.Struct, forbid_unknown_fields=False):
    content: List[ContentBlock]
    model: Optional[str] = None
    parent_tool_use_id: Optional[str] = None
    error: Optional[str] = None
    session_id: Optional[str] = None


class SDKSystemMessage(msgspec.Struct, forbid_unknown_fields=False):
    subtype: str
    data: Dict[str, Any]
    session_id: Optional[str] = None


class SDKResultMessage(msgspec.Struct, forbid_unknown_fields=False):
    subtype: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    session_id: str
    total_cost_usd: Optional[float] = None
    usage: Optional[Dict[str, Any]] = None
    result: Optional[str] = None
    structured_output: Any = None


SDKMessage = Union[
    SDKSystemMessage,
    SDKAssistantMessage,
    SDKUserMessage,
    SDKResultMessage,
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


def _parse_content_block(block: Dict[str, Any]) -> ContentBlock | None:
    match block.get("type"):
        case "text":
            text = block.get("text")
            if isinstance(text, str):
                return TextBlock(text=text)
        case "thinking":
            thinking = block.get("thinking")
            if isinstance(thinking, str):
                signature = block.get("signature")
                return ThinkingBlock(
                    thinking=thinking,
                    signature=signature if isinstance(signature, str) else None,
                )
        case "tool_use":
            tool_id = block.get("id")
            name = block.get("name")
            tool_input = block.get("input")
            if (
                isinstance(tool_id, str)
                and isinstance(name, str)
                and isinstance(tool_input, dict)
            ):
                return ToolUseBlock(id=tool_id, name=name, input=tool_input)
        case "tool_result":
            tool_use_id = block.get("tool_use_id")
            if isinstance(tool_use_id, str):
                return ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=block.get("content"),
                    is_error=block.get("is_error"),
                )
    return None


def _parse_content(value: Any) -> str | List[ContentBlock] | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        blocks: list[ContentBlock] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            block = _parse_content_block(item)
            if block is not None:
                blocks.append(block)
        return blocks
    return None


def decode_stream_json_line(line: Union[str, bytes]) -> DecodedLine:
    """
    Decode a single JSONL line from Claude Code's stream-json output.

    - If line parses to a recognized SDK message, returns a typed msgspec.Struct.
    - If line isn't JSON, returns NonJsonLine.
    - If JSON but unrecognized shape/type, returns UnknownSDKLine(raw=obj).
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
        return NonJsonLine(text=raw_bytes.decode("utf-8", errors="replace"))

    if not isinstance(obj, dict):
        return UnknownSDKLine(raw=obj)

    t = obj.get("type")

    try:
        if t == "system":
            subtype = obj.get("subtype")
            return SDKSystemMessage(
                subtype=str(subtype or ""),
                data=obj,
                session_id=obj.get("session_id"),
            )

        if t == "assistant":
            message = obj.get("message")
            if not isinstance(message, dict):
                return UnknownSDKLine(raw=obj)
            content = _parse_content(message.get("content"))
            if not isinstance(content, list):
                content = []
            model = message.get("model")
            error = message.get("error")
            return SDKAssistantMessage(
                content=content,
                model=model if isinstance(model, str) else None,
                parent_tool_use_id=obj.get("parent_tool_use_id"),
                error=error if isinstance(error, str) else None,
                session_id=obj.get("session_id"),
            )

        if t == "user":
            message = obj.get("message")
            if not isinstance(message, dict):
                return UnknownSDKLine(raw=obj)
            content = _parse_content(message.get("content"))
            if content is None:
                content = ""
            return SDKUserMessage(
                content=content,
                uuid=obj.get("uuid"),
                parent_tool_use_id=obj.get("parent_tool_use_id"),
                session_id=obj.get("session_id"),
            )

        if t == "result":
            return msgspec.convert(obj, type=SDKResultMessage)

        return UnknownSDKLine(raw=obj)

    except (msgspec.ValidationError, TypeError):
        return UnknownSDKLine(raw=obj)
