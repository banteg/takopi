from __future__ import annotations

import re
import textwrap
from collections import deque
from textwrap import indent
from typing import Any

STATUS_RUNNING = "▸"
STATUS_DONE = "✓"
HEADER_SEP = " · "
HARD_BREAK = "  \n"

MAX_CMD_LEN = 40
MAX_QUERY_LEN = 60
MAX_PATH_LEN = 40
MAX_PROGRESS_CHARS = 300


def format_elapsed(elapsed_s: float) -> str:
    total = max(0, int(elapsed_s))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def format_header(elapsed_s: float, turn: int | None, label: str) -> str:
    elapsed = format_elapsed(elapsed_s)
    if turn is not None:
        return f"{label}{HEADER_SEP}{elapsed}{HEADER_SEP}turn {turn}"
    return f"{label}{HEADER_SEP}{elapsed}"


def is_command_log_line(line: str) -> bool:
    return f"{STATUS_RUNNING} running:" in line or f"{STATUS_DONE} ran:" in line


def extract_numeric_id(item_id: object, fallback: int | None = None) -> int | None:
    if isinstance(item_id, int):
        return item_id
    if isinstance(item_id, str):
        match = re.search(r"(?:item_)?(\d+)", item_id)
        if match:
            return int(match.group(1))
    return fallback


def _shorten(text: str, width: int) -> str:
    return textwrap.shorten(text, width=width, placeholder="…")


def _shorten_path(path: str, width: int) -> str:
    # Encourage word-boundary truncation for paths (since they may have no spaces).
    return _shorten(path.replace("/", " /"), width).replace(" /", "/")


def format_event(
    event: dict[str, Any],
    last_turn: int | None,
) -> tuple[int | None, list[str], str | None, str | None]:
    """
    Returns (new_last_turn, cli_lines, progress_line, progress_prefix).
    progress_prefix is only set when progress_line is set, and is used for
    replacing a preceding "running" line on completion.
    """
    lines: list[str] = []

    match event["type"]:
        case "thread.started":
            return last_turn, ["thread started"], None, None
        case "turn.started":
            return last_turn, ["turn started"], None, None
        case "turn.completed":
            return last_turn, ["turn completed"], None, None
        case "turn.failed":
            return last_turn, [f"turn failed: {event['error']['message']}"], None, None
        case "error":
            return last_turn, [f"stream error: {event['message']}"], None, None
        case "item.started" | "item.updated" | "item.completed" as etype:
            item = event["item"]
            item_num = extract_numeric_id(item["id"], last_turn)
            last_turn = item_num if item_num is not None else last_turn
            prefix = f"[{item_num if item_num is not None else '?'}] "

            match (item["type"], etype):
                case ("agent_message", "item.completed"):
                    lines.append("assistant:")
                    lines.extend(indent(item["text"], "  ").splitlines())
                    return last_turn, lines, None, None
                case ("reasoning", "item.completed"):
                    line = prefix + item["text"]
                    return last_turn, [line], line, prefix
                case ("command_execution", "item.started"):
                    command = f"`{_shorten(item['command'], MAX_CMD_LEN)}`"
                    line = prefix + f"{STATUS_RUNNING} running: {command}"
                    return last_turn, [line], line, prefix
                case ("command_execution", "item.completed"):
                    command = f"`{_shorten(item['command'], MAX_CMD_LEN)}`"
                    exit_code = item["exit_code"]
                    exit_part = f" (exit {exit_code})" if exit_code is not None else ""
                    line = prefix + f"{STATUS_DONE} ran: {command}{exit_part}"
                    return last_turn, [line], line, prefix
                case ("mcp_tool_call", "item.started"):
                    name = ".".join(part for part in (item["server"], item["tool"]) if part) or "tool"
                    line = prefix + f"{STATUS_RUNNING} tool: {name}"
                    return last_turn, [line], line, prefix
                case ("mcp_tool_call", "item.completed"):
                    name = ".".join(part for part in (item["server"], item["tool"]) if part) or "tool"
                    line = prefix + f"{STATUS_DONE} tool: {name}"
                    return last_turn, [line], line, prefix
                case ("web_search", "item.completed"):
                    query = _shorten(item["query"], MAX_QUERY_LEN)
                    line = prefix + f"{STATUS_DONE} searched: {query}"
                    return last_turn, [line], line, prefix
                case ("file_change", "item.completed"):
                    paths = [change["path"] for change in item["changes"] if change.get("path")]
                    if not paths:
                        total = len(item["changes"])
                        desc = "updated files" if total == 0 else f"updated {total} files"
                    elif len(paths) <= 3:
                        desc = "updated " + ", ".join(f"`{_shorten_path(p, MAX_PATH_LEN)}`" for p in paths)
                    else:
                        desc = f"updated {len(paths)} files"
                    line = prefix + f"{STATUS_DONE} {desc}"
                    return last_turn, [line], line, prefix
                case ("error", "item.completed"):
                    warning = _shorten(item["message"], 120)
                    line = prefix + f"{STATUS_DONE} warning: {warning}"
                    return last_turn, [line], line, prefix
                case _:
                    return last_turn, [], None, None
        case _:
            return last_turn, [], None, None


def render_event_cli(event: dict[str, Any], last_turn: int | None = None) -> tuple[int | None, list[str]]:
    last_turn, cli_lines, _, _ = format_event(event, last_turn)
    return last_turn, cli_lines


class ExecProgressRenderer:
    def __init__(self, max_actions: int = 5, max_chars: int = MAX_PROGRESS_CHARS) -> None:
        self.max_actions = max_actions
        self.max_chars = max_chars
        self.recent_actions: deque[str] = deque(maxlen=max_actions)
        self.last_turn: int | None = None

    def note_event(self, event: dict[str, Any]) -> bool:
        if event["type"] in {"thread.started", "turn.started"}:
            return True

        self.last_turn, _, progress_line, progress_prefix = format_event(event, self.last_turn)
        if progress_line is None:
            return False

        # Replace the preceding "running" line for the same item on completion.
        if event["type"] == "item.completed" and progress_prefix and self.recent_actions:
            last = self.recent_actions[-1]
            if last.startswith(progress_prefix + f"{STATUS_RUNNING} "):
                self.recent_actions.pop()

        self.recent_actions.append(progress_line)
        return True

    def render_progress(self, elapsed_s: float) -> str:
        header = format_header(elapsed_s, self.last_turn, label="working")
        message = self._assemble(header, list(self.recent_actions))
        return message if len(message) <= self.max_chars else header

    def render_final(self, elapsed_s: float, answer: str, status: str = "done") -> str:
        header = format_header(elapsed_s, self.last_turn, label=status)
        lines = list(self.recent_actions)
        if status == "done":
            lines = [line for line in lines if not is_command_log_line(line)]
        body = self._assemble(header, lines)
        answer = (answer or "").strip()
        return body + ("\n\n" + answer if answer else "")

    @staticmethod
    def _assemble(header: str, lines: list[str]) -> str:
        return header if not lines else header + "\n\n" + HARD_BREAK.join(lines)
