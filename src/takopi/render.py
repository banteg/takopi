"""Pure renderers for Takopi events (no engine-native event handling)."""

from __future__ import annotations

import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from sulguk import transform_html

from .model import Action, ActionEvent, ResumeToken, StartedEvent, TakopiEvent
from .utils.paths import relativize_path

STATUS = {"running": "▸", "update": "↻", "done": "✓", "fail": "✗"}
HEADER_SEP = " · "
HARD_BREAK = "  \n"

MAX_PROGRESS_CMD_LEN = 300
MAX_FILE_CHANGES_INLINE = 3


@dataclass(frozen=True)
class MarkdownParts:
    header: str
    body: str | None = None
    footer: str | None = None


def assemble_markdown_parts(parts: MarkdownParts) -> str:
    chunks = [parts.header]
    if parts.body:
        chunks.append(parts.body)
    if parts.footer:
        chunks.append(parts.footer)
    return "\n\n".join(chunks)


def render_markdown(md: str) -> tuple[str, list[dict[str, Any]]]:
    md_renderer = MarkdownIt("commonmark", {"html": False})
    html = md_renderer.render(md or "")
    rendered = transform_html(html)

    text = re.sub(r"(?m)^(\s*)•", r"\1-", rendered.text)

    entities = [dict(e) for e in rendered.entities]
    return text, entities


def trim_body(body: str) -> str:
    if len(body) <= 3500:
        return body
    return body[: 3500 - 1] + "…"


def trim_markdown_parts(parts: MarkdownParts) -> MarkdownParts:
    header = parts.header or ""
    body = parts.body or ""
    footer = parts.footer or ""

    trimmed_body = trim_body(body) if body else ""
    if not trimmed_body.strip():
        trimmed_body = ""

    return MarkdownParts(
        header=header,
        body=trimmed_body or None,
        footer=footer or None,
    )


def prepare_telegram(parts: MarkdownParts) -> tuple[str, list[dict[str, Any]]]:
    trimmed = trim_markdown_parts(parts)
    md = assemble_markdown_parts(trimmed)
    return render_markdown(md)


def format_changed_file_path(path: str, *, base_dir: Path | None = None) -> str:
    return f"`{relativize_path(path, base_dir=base_dir)}`"


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


def shorten(text: str, width: int | None) -> str:
    if width is None:
        return text
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return textwrap.shorten(text, width=width, placeholder="…")


def action_status(action: Action, *, completed: bool, ok: bool | None = None) -> str:
    if not completed:
        return STATUS["running"]
    if ok is not None:
        return STATUS["done"] if ok else STATUS["fail"]
    detail = action.detail or {}
    exit_code = detail.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return STATUS["fail"]
    return STATUS["done"]


def action_suffix(action: Action) -> str:
    detail = action.detail or {}
    exit_code = detail.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return f" (exit {exit_code})"
    return ""


def format_file_change_title(action: Action, *, command_width: int | None) -> str:
    title = str(action.title or "")
    detail = action.detail or {}

    changes = detail.get("changes")
    if isinstance(changes, list) and changes:
        rendered: list[str] = []
        for raw in changes:
            if not isinstance(raw, dict):
                continue
            path = raw.get("path")
            if not isinstance(path, str) or not path:
                continue
            kind = raw.get("kind")
            verb = kind if isinstance(kind, str) and kind else "update"
            rendered.append(f"{verb} {format_changed_file_path(path)}")

        if rendered:
            if len(rendered) > MAX_FILE_CHANGES_INLINE:
                remaining = len(rendered) - MAX_FILE_CHANGES_INLINE
                rendered = rendered[:MAX_FILE_CHANGES_INLINE] + [f"…({remaining} more)"]
            inline = shorten(", ".join(rendered), command_width)
            return f"files: {inline}"

    return f"files: {shorten(title, command_width)}"


def format_action_title(action: Action, *, command_width: int | None) -> str:
    title = str(action.title or "")
    kind = action.kind
    if kind == "command":
        title = shorten(title, command_width)
        return f"`{title}`"
    if kind == "tool":
        title = shorten(title, command_width)
        return f"tool: {title}"
    if kind == "web_search":
        title = shorten(title, command_width)
        return f"searched: {title}"
    if kind == "file_change":
        return format_file_change_title(action, command_width=command_width)
    if kind in {"note", "warning"}:
        return shorten(title, command_width)
    return shorten(title, command_width)


def format_action_line(
    action: Action,
    phase: str,
    ok: bool | None,
    *,
    command_width: int | None,
) -> str:
    if phase != "completed":
        status = STATUS["update"] if phase == "updated" else STATUS["running"]
        return f"{status} {format_action_title(action, command_width=command_width)}"
    status = action_status(action, completed=True, ok=ok)
    suffix = action_suffix(action)
    return (
        f"{status} {format_action_title(action, command_width=command_width)}{suffix}"
    )


def is_command_log_line(line: str) -> bool:
    return line.startswith(f"{STATUS['running']} `") or line.startswith(
        f"{STATUS['done']} `"
    )


def render_event_cli(event: TakopiEvent) -> list[str]:
    match event:
        case StartedEvent(engine=engine):
            return [str(engine)]
        case ActionEvent() as action_event:
            action = action_event.action
            if action.kind == "turn":
                return []
            return [
                format_action_line(
                    action_event.action,
                    action_event.phase,
                    action_event.ok,
                    command_width=MAX_PROGRESS_CMD_LEN,
                )
            ]
        case _:
            return []


@dataclass
class TrackedAction:
    action_id: str
    line: str
    completed: bool = False


class ExecProgressRenderer:
    def __init__(
        self,
        max_actions: int = 5,
        command_width: int | None = MAX_PROGRESS_CMD_LEN,
        resume_formatter: Callable[[ResumeToken], str] | None = None,
        show_title: bool = False,
    ) -> None:
        self.max_actions = max_actions
        self.command_width = command_width
        self.actions: list[TrackedAction] = []
        self.action_count = 0
        self.started_counts: dict[str, int] = {}
        self.resume_token: ResumeToken | None = None
        self.session_title: str | None = None
        self._resume_formatter = resume_formatter
        self.show_title = show_title

    def note_event(self, event: TakopiEvent) -> bool:
        match event:
            case StartedEvent(resume=resume, title=title):
                self.resume_token = resume
                self.session_title = title
                return True
            case ActionEvent(action=action, phase=phase, ok=ok):
                if action.kind == "turn":
                    return False
                action_id = str(action.id or "")
                if not action_id:
                    return False
                return self.record_action(action_id, action, phase, ok)
            case _:
                return False

    def record_action(
        self, action_id: str, action: Action, phase: str, ok: bool | None
    ) -> bool:
        completed = phase == "completed"
        started_count = self.started_counts.get(action_id, 0)
        if completed:
            is_update = False
        else:
            is_update = phase == "updated" or started_count > 0
            if started_count == 0:
                self.action_count += 1
                self.started_counts[action_id] = 1
            elif phase == "started":
                self.started_counts[action_id] = started_count + 1
            else:
                self.started_counts[action_id] = started_count

        if completed:
            count = self.started_counts.get(action_id, 0)
            if count <= 0:
                self.action_count += 1
            elif count == 1:
                self.started_counts.pop(action_id, None)
            else:
                self.started_counts[action_id] = count - 1

        phase_for_line = "updated" if is_update and not completed else phase
        line = format_action_line(
            action, phase_for_line, ok, command_width=self.command_width
        )

        for tracked in reversed(self.actions):
            if tracked.action_id == action_id and not tracked.completed:
                tracked.line = line
                if completed:
                    tracked.completed = True
                return True

        self.actions.append(
            TrackedAction(action_id=action_id, line=line, completed=completed)
        )
        if len(self.actions) > self.max_actions:
            self.actions.pop(0)
        return True

    def render_progress_parts(
        self, elapsed_s: float, label: str = "working"
    ) -> MarkdownParts:
        step = self.action_count or None
        header = format_header(elapsed_s, step, label=self.label_with_title(label))
        body = self.assemble_body([tracked.line for tracked in self.actions])
        return MarkdownParts(header=header, body=body, footer=self.render_footer())

    def render_final_parts(
        self, elapsed_s: float, answer: str, status: str = "done"
    ) -> MarkdownParts:
        step = self.action_count or None
        header = format_header(elapsed_s, step, label=self.label_with_title(status))
        lines = [tracked.line for tracked in self.actions]
        if status == "done":
            lines = [line for line in lines if not is_command_log_line(line)]
        body = self.assemble_body(lines)
        answer = (answer or "").strip()
        if answer:
            body = answer if not body else body + "\n\n" + answer
        return MarkdownParts(header=header, body=body, footer=self.render_footer())

    def label_with_title(self, label: str) -> str:
        if self.show_title and self.session_title:
            return f"{label} ({self.session_title})"
        return label

    def render_footer(self) -> str | None:
        if not self.resume_token or self._resume_formatter is None:
            return None
        return self._resume_formatter(self.resume_token)

    @property
    def recent_actions(self) -> list[str]:
        return [tracked.line for tracked in self.actions]

    @staticmethod
    def assemble_body(lines: list[str]) -> str | None:
        if not lines:
            return None
        return HARD_BREAK.join(lines)
