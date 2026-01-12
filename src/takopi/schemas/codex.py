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


type ThreadItem = (
    AgentMessageItem
    | ReasoningItem
    | CommandExecutionItem
    | FileChangeItem
    | McpToolCallItem
    | WebSearchItem
    | TodoListItem
    | ErrorItem
)


class ItemStarted(msgspec.Struct, tag="item.started", kw_only=True):
    item: ThreadItem


class ItemUpdated(msgspec.Struct, tag="item.updated", kw_only=True):
    item: ThreadItem


class ItemCompleted(msgspec.Struct, tag="item.completed", kw_only=True):
    item: ThreadItem


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


def decode_event(data: bytes | str) -> ThreadEvent:
    return _DECODER.decode(data)
