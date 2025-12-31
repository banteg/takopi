from __future__ import annotations

import re
import textwrap
from collections import deque
from typing import Any

from markdown_it import MarkdownIt
from sulguk import transform_html

from .runners.base import ResumeToken, TakopiEvent

STATUS_RUNNING = "▸"
STATUS_DONE = "✓"
STATUS_FAIL = "✗"
HEADER_SEP = " · "
HARD_BREAK = "  \n"

MAX_PROGRESS_CMD_LEN = 300
MAX_QUERY_LEN = 60

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


def _shorten(text: str, width: int | None) -> str:
    if width is None:
        return text
    return textwrap.shorten(text, width=width, placeholder="…")


def _action_status_symbol(action: dict[str, Any], *, completed: bool) -> str:
    if not completed:
        return STATUS_RUNNING
    ok = action.get("ok")
    if ok is not None:
        return STATUS_DONE if ok else STATUS_FAIL
    detail = action.get("detail") or {}
    exit_code = detail.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return STATUS_FAIL
    return STATUS_DONE


def _action_exit_suffix(action: dict[str, Any]) -> str:
    detail = action.get("detail") or {}
    exit_code = detail.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return f" (exit {exit_code})"
    return ""


def _format_action_title(action: dict[str, Any], *, command_width: int | None) -> str:
    title = str(action.get("title") or "")
    kind = action.get("kind")
    if kind == "command":
        title = _shorten(title, command_width)
        return f"`{title}`"
    if kind == "tool":
        title = _shorten(title, command_width)
        return f"tool: {title}"
    if kind == "web_search":
        title = _shorten(title, MAX_QUERY_LEN)
        return f"searched: {title}"
    if kind == "file_change":
        title = _shorten(title, command_width)
        return f"updated {title}"
    if kind == "note":
        title = _shorten(title, MAX_QUERY_LEN)
        return title
    return _shorten(title, command_width)


def render_event_cli(
    event: TakopiEvent, last_item: int | None = None
) -> tuple[int | None, list[str]]:
    lines: list[str] = []
    etype = event["type"]
    match etype:
        case "session.started":
            lines.append(event.get("engine", "engine"))
        case "action.started":
            action = event["action"]
            title = _format_action_title(action, command_width=MAX_PROGRESS_CMD_LEN)
            lines.append(f"{STATUS_RUNNING} {title}")
        case "action.completed":
            action = event["action"]
            status = _action_status_symbol(action, completed=True)
            title = _format_action_title(action, command_width=MAX_PROGRESS_CMD_LEN)
            suffix = _action_exit_suffix(action)
            lines.append(f"{status} {title}{suffix}")
        case "log":
            level = event.get("level", "info")
            lines.append(f"log[{level}]: {event.get('message', '')}")
        case "error":
            lines.append(f"error: {event.get('message', '')}")
        case _:
            return last_item, []
    return last_item, lines


class ExecProgressRenderer:
    def __init__(
        self,
        max_actions: int = 5,
        command_width: int | None = MAX_PROGRESS_CMD_LEN,
    ) -> None:
        self.max_actions = max_actions
        self.command_width = command_width
        self.recent_actions: deque[str] = deque(maxlen=max_actions)
        self.action_count = 0
        self._started_counts: dict[str, int] = {}
        self.resume_token: ResumeToken | None = None

    def note_event(self, event: TakopiEvent) -> bool:
        if event["type"] == "session.started":
            resume = event["resume"]
            self.resume_token = ResumeToken(
                engine=resume["engine"], value=resume["value"]
            )
            return True

        if event["type"] not in {"action.started", "action.completed"}:
            return False

        action = event["action"]
        action_id = str(action.get("id") or "")
        if not action_id:
            return False

        completed = event["type"] == "action.completed"
        if not completed:
            self._started_counts[action_id] = self._started_counts.get(action_id, 0) + 1
            self.action_count += 1
        else:
            count = self._started_counts.get(action_id, 0)
            if count <= 0:
                self.action_count += 1
            elif count == 1:
                self._started_counts.pop(action_id, None)
            else:
                self._started_counts[action_id] = count - 1

        status = _action_status_symbol(action, completed=completed)
        title = _format_action_title(action, command_width=self.command_width)
        suffix = _action_exit_suffix(action) if completed else ""
        line = f"{status} {title}{suffix}"

        self._append_action(line)
        return True

    def _append_action(self, line: str) -> None:
        self.recent_actions.append(line)

    def render_progress(self, elapsed_s: float, label: str = "working") -> str:
        step = self.action_count or None
        header = format_header(elapsed_s, step, label=label)
        message = self._assemble(header, list(self.recent_actions))
        return self._append_resume(message)

    def render_final(self, elapsed_s: float, answer: str, status: str = "done") -> str:
        step = self.action_count or None
        header = format_header(elapsed_s, step, label=status)
        answer = (answer or "").strip()
        message = header + ("\n\n" + answer if answer else "")
        return self._append_resume(message)

    def _append_resume(self, message: str) -> str:
        if not self.resume_token:
            return message
        token = f"{self.resume_token.engine}:{self.resume_token.value}"
        # Escape backticks so they remain literal in rendered text and reply parsing.
        return message + f"\n\nresume: \\`{token}\\`"

    @staticmethod
    def _assemble(header: str, lines: list[str]) -> str:
        return header if not lines else header + "\n\n" + HARD_BREAK.join(lines)
