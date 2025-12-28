from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from textwrap import indent
from typing import Any, Optional

ELLIPSIS = "…"
STATUS_RUNNING = "▸"
STATUS_DONE = "✓"
HEADER_SEP = " · "

MAX_CMD_LEN = 40
MAX_REASON_LEN = 80
MAX_QUERY_LEN = 60
MAX_PATH_LEN = 40
MAX_PROGRESS_CHARS = 300


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def _truncate(text: str, max_len: int) -> str:
    text = _one_line(text)
    if len(text) <= max_len:
        return text
    if max_len <= len(ELLIPSIS):
        return text[:max_len]
    return text[: max_len - len(ELLIPSIS)] + ELLIPSIS


def _inline_code(text: str) -> str:
    return f"`{text}`"


def _format_elapsed(elapsed_s: float) -> str:
    total = max(0, int(elapsed_s))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _format_header(elapsed_s: float, turn: Optional[int], done: bool) -> str:
    label = "Done" if done else "codex"
    elapsed = _format_elapsed(elapsed_s)
    if turn is not None:
        return f"{label}{HEADER_SEP}{elapsed}{HEADER_SEP}turn {turn}"
    return f"{label}{HEADER_SEP}{elapsed}"


def _format_reasoning(text: str) -> str:
    if not text:
        return ""
    return f"_{_truncate(text, MAX_REASON_LEN)}_"


def _format_command(command: str) -> str:
    command = _truncate(command, MAX_CMD_LEN)
    if not command:
        command = "(empty)"
    return _inline_code(command)


def _format_query(query: str) -> str:
    return _truncate(query, MAX_QUERY_LEN)


def _format_paths(paths: list[str]) -> str:
    rendered = []
    for path in paths:
        rendered.append(_inline_code(_truncate(path, MAX_PATH_LEN)))
    return ", ".join(rendered)


def _format_file_change(changes: list[dict[str, Any]]) -> str:
    paths = [change.get("path") for change in changes if change.get("path")]
    if not paths:
        total = len(changes)
        return "updated files" if total == 0 else f"updated {total} files"
    if len(paths) <= 3:
        return f"updated {_format_paths(paths)}"
    return f"updated {len(paths)} files"


def _format_tool_call(server: str, tool: str) -> str:
    name = ".".join(part for part in (server, tool) if part)
    return name or "tool"


def _truncate_output(text: str, max_lines: int = 20, max_chars: int = 4000) -> str:
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[-max_chars:]
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = ["..."] + lines[-max_lines:]
    return "\n".join(lines)


def _maybe_parse_json(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


@dataclass
class ExecRenderState:
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    recent_actions: deque[str] = field(default_factory=lambda: deque(maxlen=5))
    current_action: Optional[str] = None
    current_action_id: Optional[str] = None
    pending_reasoning: Optional[str] = None
    current_reasoning: Optional[str] = None
    last_turn: Optional[int] = None


def _record_item(state: ExecRenderState, item: dict[str, Any]) -> None:
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id:
        state.items[item_id] = item
        match = re.search(r"item_(\d+)", item_id)
        if match:
            state.last_turn = int(match.group(1))


def _set_current_action(state: ExecRenderState, item_id: Optional[str], line: str) -> bool:
    changed = False
    if state.current_action != line or state.current_action_id != item_id:
        state.current_action = line
        state.current_action_id = item_id
        if state.pending_reasoning:
            state.current_reasoning = state.pending_reasoning
            state.pending_reasoning = None
        changed = True
    return changed


def _complete_action(state: ExecRenderState, item_id: Optional[str], line: str) -> bool:
    changed = False
    if line:
        state.recent_actions.append(line)
        changed = True
    if item_id and state.current_action_id == item_id:
        state.current_action = None
        state.current_action_id = None
        state.current_reasoning = None
        changed = True
    return changed


def render_event_cli(
    event: dict[str, Any],
    state: ExecRenderState,
    *,
    show_reasoning: bool = False,
) -> list[str]:
    etype = event.get("type")
    lines: list[str] = []

    if etype == "thread.started":
        return ["thread started"]

    if etype == "turn.started":
        return ["turn started"]

    if etype == "turn.completed":
        return ["turn completed"]

    if etype == "turn.failed":
        error = event.get("error", {}).get("message", "")
        return [f"turn failed: {error}"]

    if etype == "error":
        return [f"stream error: {event.get('message', '')}"]

    if etype in {"item.started", "item.updated", "item.completed"}:
        item = event.get("item", {}) or {}
        _record_item(state, item)

        itype = item.get("type")

        if itype == "agent_message" and etype == "item.completed":
            text = item.get("text", "")
            parsed = _maybe_parse_json(text)
            if parsed is not None:
                lines.append("assistant (json):")
                lines.extend(indent(json.dumps(parsed, indent=2), "  ").splitlines())
            else:
                lines.append("assistant:")
                lines.extend(indent(text, "  ").splitlines() if text else ["  (empty)"])

        elif itype == "reasoning" and show_reasoning:
            reasoning = _format_reasoning(item.get("text", ""))
            if reasoning:
                lines.append(reasoning)

        elif itype == "command_execution":
            command = _format_command(item.get("command", ""))
            if etype == "item.started":
                lines.append(f"{STATUS_RUNNING} running: {command}")
            elif etype == "item.completed":
                exit_code = item.get("exit_code")
                exit_part = f" (exit {exit_code})" if exit_code is not None else ""
                lines.append(f"{STATUS_DONE} ran: {command}{exit_part}")
                output = _truncate_output(item.get("aggregated_output", ""))
                if output:
                    lines.extend(indent(output, "  ").splitlines())

        elif itype == "file_change" and etype == "item.completed":
            line = _format_file_change(item.get("changes", []))
            lines.append(f"{STATUS_DONE} {line}")

        elif itype == "mcp_tool_call":
            name = _format_tool_call(item.get("server", ""), item.get("tool", ""))
            if etype == "item.started":
                lines.append(f"{STATUS_RUNNING} tool: {name}")
            elif etype == "item.completed":
                lines.append(f"{STATUS_DONE} tool: {name}")

        elif itype == "web_search" and etype == "item.completed":
            query = _format_query(item.get("query", ""))
            lines.append(f"{STATUS_DONE} searched: {query}")

        elif itype == "error" and etype == "item.completed":
            warning = _truncate(item.get("message", ""), 120)
            lines.append(f"{STATUS_DONE} warning: {warning}")

    return lines


class ExecProgressRenderer:
    def __init__(self, max_actions: int = 5, max_chars: int = MAX_PROGRESS_CHARS) -> None:
        self.state = ExecRenderState(recent_actions=deque(maxlen=max_actions))
        self.max_chars = max_chars

    def note_event(self, event: dict[str, Any]) -> bool:
        etype = event.get("type")
        changed = False

        if etype in {"thread.started", "turn.started"}:
            return True

        if etype in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item", {}) or {}
            _record_item(self.state, item)
            itype = item.get("type")
            item_id = item.get("id") if isinstance(item.get("id"), str) else None

            if itype == "reasoning":
                reasoning = _format_reasoning(item.get("text", ""))
                if reasoning:
                    if self.state.current_action and not self.state.current_reasoning:
                        self.state.current_reasoning = reasoning
                        changed = True
                    else:
                        self.state.pending_reasoning = reasoning
                return changed

            if itype == "command_execution":
                command = _format_command(item.get("command", ""))
                if etype == "item.started":
                    line = f"{STATUS_RUNNING} running: {command}"
                    changed = _set_current_action(self.state, item_id, line) or changed
                elif etype == "item.completed":
                    exit_code = item.get("exit_code")
                    exit_part = f" (exit {exit_code})" if exit_code is not None else ""
                    line = f"{STATUS_DONE} ran: {command}{exit_part}"
                    changed = _complete_action(self.state, item_id, line) or changed
                return changed

            if itype == "mcp_tool_call":
                name = _format_tool_call(item.get("server", ""), item.get("tool", ""))
                if etype == "item.started":
                    line = f"{STATUS_RUNNING} tool: {name}"
                    changed = _set_current_action(self.state, item_id, line) or changed
                elif etype == "item.completed":
                    line = f"{STATUS_DONE} tool: {name}"
                    changed = _complete_action(self.state, item_id, line) or changed
                return changed

            if itype == "web_search" and etype == "item.completed":
                query = _format_query(item.get("query", ""))
                line = f"{STATUS_DONE} searched: {query}"
                return _complete_action(self.state, item_id, line) or changed

            if itype == "file_change" and etype == "item.completed":
                line = f"{STATUS_DONE} {_format_file_change(item.get('changes', []))}"
                return _complete_action(self.state, item_id, line) or changed

            if itype == "error" and etype == "item.completed":
                warning = _truncate(item.get("message", ""), 120)
                line = f"{STATUS_DONE} warning: {warning}"
                return _complete_action(self.state, item_id, line) or changed

        return changed

    def render_progress(self, elapsed_s: float) -> str:
        header = _format_header(elapsed_s, self.state.last_turn, done=False)
        actions = list(self.state.recent_actions)
        current_reasoning = self.state.current_reasoning
        current_action = self.state.current_action

        lines: list[str] = []
        if actions:
            lines.extend(actions)
        if current_reasoning and current_action:
            lines.append(current_reasoning)
        if current_action:
            lines.append(current_action)

        message = self._assemble(header, lines)
        if len(message) <= self.max_chars:
            return message

        trimmed_actions = list(actions)
        while len(message) > self.max_chars and trimmed_actions:
            trimmed_actions.pop(0)
            lines = []
            if trimmed_actions:
                lines.extend(trimmed_actions)
            if current_reasoning and current_action:
                lines.append(current_reasoning)
            if current_action:
                lines.append(current_action)
            message = self._assemble(header, lines)

        return message

    def render_final(self, elapsed_s: float, answer: str) -> str:
        header = _format_header(elapsed_s, self.state.last_turn, done=True)
        lines: list[str] = []
        if self.state.recent_actions:
            lines.extend(self.state.recent_actions)
        body = self._assemble(header, lines)
        answer = (answer or "").strip()
        if answer:
            body = body + "\n\n" + answer
        return body

    @staticmethod
    def _assemble(header: str, lines: list[str]) -> str:
        if not lines:
            return header
        return header + "\n\n" + "\n".join(lines)
