from __future__ import annotations

import re
import textwrap
from collections import deque
from pathlib import Path
from textwrap import indent
from typing import Any

from markdown_it import MarkdownIt
from sulguk import transform_html

STATUS_RUNNING = "▸"
STATUS_DONE = "✓"
STATUS_FAIL = "✗"
HEADER_SEP = " · "
HARD_BREAK = "  \n"

MAX_PROGRESS_CMD_LEN = 300
MAX_QUERY_LEN = 60
MAX_PATH_LEN = 40

_md = MarkdownIt("commonmark", {"html": False})


def render_markdown(md: str) -> tuple[str, list[dict[str, Any]]]:
    html = _md.render(md or "")
    rendered = transform_html(html)

    text = re.sub(r"(?m)^(\s*)•", r"\1-", rendered.text)

    entities = [dict(e) for e in rendered.entities]
    return text, entities


def format_elapsed(elapsed_s: float) -> str:
    total = max(0, int(elapsed_s))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def format_header(elapsed_s: float, item: int | None, label: str) -> str:
    elapsed = format_elapsed(elapsed_s)
    parts = [label, elapsed]
    if item is not None:
        parts.append(f"step {item}")
    return HEADER_SEP.join(parts)


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


def _format_change_path(path: str) -> str:
    workdir = Path.cwd()
    path_obj = Path(path)
    if path_obj.is_absolute() and path_obj.is_relative_to(workdir):
        return str(path_obj.relative_to(workdir))
    return path


def format_event(
    event: dict[str, Any],
    last_item: int | None,
    *,
    command_width: int | None = None,
    escape_markdown: bool = False,
) -> tuple[int | None, list[str], str | None, str | None]:
    lines: list[str] = []

    match event["type"]:
        case "thread.started":
            return last_item, ["thread started"], None, None
        case "turn.started":
            return last_item, ["turn started"], None, None
        case "turn.completed":
            return last_item, ["turn completed"], None, None
        case "turn.failed":
            return last_item, [f"turn failed: {event['error']['message']}"], None, None
        case "error":
            return last_item, [f"stream error: {event['message']}"], None, None
        case "item.started" | "item.updated" | "item.completed" as etype:
            item = event["item"]
            item_type = item.get("type") or item.get("item_type")
            if item_type == "assistant_message":
                item_type = "agent_message"
            if item_type is None:
                return last_item, [], None, None
            item_num = extract_numeric_id(item.get("id"), last_item)
            last_item = item_num if item_num is not None else last_item
            prefix = f"{item_num}. "
            if escape_markdown and item_num is not None:
                # Avoid ordered-list parsing which renumbers items in MarkdownIt/CommonMark.
                prefix = f"{item_num}\\." + " "

            match (item_type, etype):
                case ("agent_message", "item.completed"):
                    lines.append("assistant:")
                    lines.extend(indent(item["text"], "  ").splitlines())
                    return last_item, lines, None, None
                case ("reasoning", "item.completed"):
                    text = item.get("text") or ""
                    first_line = text.splitlines()[0] if text else ""
                    line = prefix + first_line
                    return last_item, [line], line, prefix
                case ("command_execution", "item.started"):
                    command = item["command"]
                    if command_width is not None:
                        command = _shorten(command, command_width)
                    command = f"`{command}`"
                    line = prefix + f"{STATUS_RUNNING} {command}"
                    return last_item, [line], line, prefix
                case ("command_execution", "item.completed"):
                    command = item["command"]
                    if command_width is not None:
                        command = _shorten(command, command_width)
                    command = f"`{command}`"
                    exit_code = item["exit_code"]
                    if exit_code == 0:
                        status = STATUS_DONE
                        exit_part = ""
                    else:
                        status = STATUS_FAIL if exit_code is not None else STATUS_DONE
                        exit_part = (
                            f" (exit {exit_code})" if exit_code is not None else ""
                        )
                    line = prefix + f"{status} {command}{exit_part}"
                    return last_item, [line], line, prefix
                case ("mcp_tool_call", "item.started"):
                    name = (
                        ".".join(
                            part for part in (item["server"], item["tool"]) if part
                        )
                        or "tool"
                    )
                    line = prefix + f"{STATUS_RUNNING} tool: {name}"
                    return last_item, [line], line, prefix
                case ("mcp_tool_call", "item.completed"):
                    name = (
                        ".".join(
                            part for part in (item["server"], item["tool"]) if part
                        )
                        or "tool"
                    )
                    line = prefix + f"{STATUS_DONE} tool: {name}"
                    return last_item, [line], line, prefix
                case ("web_search", "item.completed"):
                    query = _shorten(item["query"], MAX_QUERY_LEN)
                    line = prefix + f"{STATUS_DONE} searched: {query}"
                    return last_item, [line], line, prefix
                case ("file_change", "item.completed"):
                    paths = [
                        change["path"]
                        for change in item["changes"]
                        if change.get("path")
                    ]
                    if not paths:
                        total = len(item["changes"])
                        desc = (
                            "updated files" if total == 0 else f"updated {total} files"
                        )
                    elif len(paths) <= 3:
                        desc = "updated " + ", ".join(
                            f"`{_format_change_path(p)}`" for p in paths
                        )
                    else:
                        desc = f"updated {len(paths)} files"
                    line = prefix + f"{STATUS_DONE} {desc}"
                    return last_item, [line], line, prefix
                case ("error", "item.completed"):
                    warning = _shorten(item["message"], 120)
                    line = prefix + f"{STATUS_DONE} warning: {warning}"
                    return last_item, [line], line, prefix
                case _:
                    return last_item, [], None, None
        case _:
            return last_item, [], None, None


def render_event_cli(
    event: dict[str, Any], last_item: int | None = None
) -> tuple[int | None, list[str]]:
    last_item, cli_lines, _, _ = format_event(
        event, last_item, command_width=None, escape_markdown=False
    )
    return last_item, cli_lines


class ExecProgressRenderer:
    def __init__(
        self,
        max_actions: int = 5,
        command_width: int | None = MAX_PROGRESS_CMD_LEN,
    ) -> None:
        self.max_actions = max_actions
        self.command_width = command_width
        self.recent_actions: deque[str] = deque(maxlen=max_actions)
        self.last_item: int | None = None

    def note_event(self, event: dict[str, Any]) -> bool:
        if event["type"] == "thread.started":
            return True

        self.last_item, _, progress_line, progress_prefix = format_event(
            event,
            self.last_item,
            command_width=self.command_width,
            escape_markdown=True,
        )
        if progress_line is None:
            return False

        # Replace the preceding "running" line for the same item on completion.
        if (
            event["type"] == "item.completed"
            and progress_prefix
            and self.recent_actions
        ):
            last = self.recent_actions[-1]
            if last.startswith(progress_prefix + f"{STATUS_RUNNING} "):
                self.recent_actions.pop()

        self.recent_actions.append(progress_line)
        return True

    def render_progress(self, elapsed_s: float) -> str:
        header = format_header(elapsed_s, self.last_item, label="working")
        return self._assemble(header, list(self.recent_actions))

    def render_final(self, elapsed_s: float, answer: str, status: str = "done") -> str:
        header = format_header(elapsed_s, self.last_item, label=status)
        answer = (answer or "").strip()
        return header + ("\n\n" + answer if answer else "")

    @staticmethod
    def _assemble(header: str, lines: list[str]) -> str:
        return header if not lines else header + "\n\n" + HARD_BREAK.join(lines)
