from __future__ import annotations

# Headless JSONL schema derived from tag rust-v0.77.0 (git 112f40e91c12af0f7146d7e03f20283516a8af0b).

from typing import Any, Literal

import msgspec

type CommandExecutionStatus = Literal[
    "in_progress",
    "completed",
    "failed",
    "declined",
]
type PatchApplyStatus = Literal[
    "in_progress",
    "completed",
    "failed",
]
type PatchChangeKind = Literal[
    "add",
    "delete",
    "update",
]
type McpToolCallStatus = Literal[
    "in_progress",
    "completed",
    "failed",
]
type CollabToolCallStatus = Literal[
    "in_progress",
    "completed",
    "failed",
]


class Usage(msgspec.Struct, kw_only=True):
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


class ThreadError(msgspec.Struct, kw_only=True):
    message: str


class ThreadStarted(msgspec.Struct, tag="thread.started", kw_only=True):
    thread_id: str


class TurnStarted(msgspec.Struct, tag="turn.started", kw_only=True):
    pass


class TurnCompleted(msgspec.Struct, tag="turn.completed", kw_only=True):
    usage: Usage


class TurnFailed(msgspec.Struct, tag="turn.failed", kw_only=True):
    error: ThreadError


class StreamError(msgspec.Struct, tag="error", kw_only=True):
    message: str


class AgentMessageItem(msgspec.Struct, tag="agent_message", kw_only=True):
    id: str
    text: str
    phase: str | None = None


class ReasoningItem(msgspec.Struct, tag="reasoning", kw_only=True):
    id: str
    text: str


class CommandExecutionItem(msgspec.Struct, tag="command_execution", kw_only=True):
    id: str
    command: str
    aggregated_output: str
    exit_code: int | None
    status: CommandExecutionStatus


class FileUpdateChange(msgspec.Struct, kw_only=True):
    path: str
    kind: PatchChangeKind


class FileChangeItem(msgspec.Struct, tag="file_change", kw_only=True):
    id: str
    changes: list[FileUpdateChange]
    status: PatchApplyStatus


class McpToolCallItemResult(msgspec.Struct, kw_only=True):
    content: list[dict[str, Any]]
    structured_content: Any


class McpToolCallItemError(msgspec.Struct, kw_only=True):
    message: str


class McpToolCallItem(msgspec.Struct, tag="mcp_tool_call", kw_only=True):
    id: str
    server: str
    tool: str
    arguments: Any
    result: McpToolCallItemResult | None
    error: McpToolCallItemError | None
    status: McpToolCallStatus


class CollabAgentState(msgspec.Struct, kw_only=True):
    status: str
    message: str | None = None


class CollabToolCallItem(msgspec.Struct, tag="collab_tool_call", kw_only=True):
    id: str
    tool: str | None = None
    sender_thread_id: str | None = None
    receiver_thread_ids: list[str] = msgspec.field(default_factory=list)
    prompt: str | None = None
    agents_states: dict[str, CollabAgentState] = msgspec.field(default_factory=dict)
    status: CollabToolCallStatus = "in_progress"


class WebSearchItem(msgspec.Struct, tag="web_search", kw_only=True):
    id: str
    query: str


class ErrorItem(msgspec.Struct, tag="error", kw_only=True):
    id: str
    message: str


class TodoItem(msgspec.Struct, kw_only=True):
    text: str
    completed: bool


class TodoListItem(msgspec.Struct, tag="todo_list", kw_only=True):
    id: str
    items: list[TodoItem]


class UnknownItem(msgspec.Struct, tag="unknown_item", kw_only=True):
    id: str
    item_type: str
    payload: dict[str, Any] = msgspec.field(default_factory=dict)


type ThreadItem = (
    AgentMessageItem
    | ReasoningItem
    | CommandExecutionItem
    | FileChangeItem
    | McpToolCallItem
    | CollabToolCallItem
    | WebSearchItem
    | TodoListItem
    | ErrorItem
    | UnknownItem
)


class ItemStarted(msgspec.Struct, tag="item.started", kw_only=True):
    item: ThreadItem


class ItemUpdated(msgspec.Struct, tag="item.updated", kw_only=True):
    item: ThreadItem


class ItemCompleted(msgspec.Struct, tag="item.completed", kw_only=True):
    item: ThreadItem


type ItemEvent = ItemStarted | ItemUpdated | ItemCompleted


type ThreadEvent = (
    ThreadStarted
    | TurnStarted
    | TurnCompleted
    | TurnFailed
    | ItemStarted
    | ItemUpdated
    | ItemCompleted
    | StreamError
)

_DECODER = msgspec.json.Decoder(ThreadEvent)
_RAW_OBJECT_DECODER = msgspec.json.Decoder(dict[str, Any])
_KNOWN_ITEM_TYPES = {
    "agent_message",
    "reasoning",
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "collab_tool_call",
    "web_search",
    "todo_list",
    "error",
}


def _decode_unknown_item_fallback(data: bytes | str) -> ItemEvent | None:
    payload = _RAW_OBJECT_DECODER.decode(data)
    event_type = payload.get("type")
    if event_type not in {"item.started", "item.updated", "item.completed"}:
        return None

    item = payload.get("item")
    if not isinstance(item, dict):
        return None

    item_type = item.get("type")
    if not isinstance(item_type, str) or item_type in _KNOWN_ITEM_TYPES:
        return None

    item_id = item.get("id")
    if not isinstance(item_id, str):
        return None

    unknown_item = UnknownItem(
        id=item_id,
        item_type=item_type,
        payload={
            str(key): value for key, value in item.items() if key not in {"id", "type"}
        },
    )
    if event_type == "item.started":
        return ItemStarted(item=unknown_item)
    if event_type == "item.updated":
        return ItemUpdated(item=unknown_item)
    return ItemCompleted(item=unknown_item)


def decode_event(data: bytes | str) -> ThreadEvent:
    try:
        return _DECODER.decode(data)
    except msgspec.DecodeError:
        fallback = _decode_unknown_item_fallback(data)
        if fallback is not None:
            return fallback
        raise
