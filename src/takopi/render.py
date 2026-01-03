"""Pure renderers for Takopi events (no engine-native event handling)."""

from __future__ import annotations

import re
import textwrap
from collections import deque
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
TELEGRAM_MAX_MESSAGE_LEN = 4096

_MD_RENDERER = MarkdownIt("commonmark", {"html": False})
_BULLET_RE = re.compile(r"(?m)^(\s*)•")


@dataclass(frozen=True)
class MarkdownParts:
    header: str
    body: str | None = None
    footer: str | None = None


def assemble_markdown_parts(parts: MarkdownParts) -> str:
    return "\n\n".join(
        chunk for chunk in (parts.header, parts.body, parts.footer) if chunk
    )


def render_markdown(md: str) -> tuple[str, list[dict[str, Any]]]:
    html = _MD_RENDERER.render(md or "")
    rendered = transform_html(html)

    text = _BULLET_RE.sub(r"\1-", rendered.text)

    entities = [dict(e) for e in rendered.entities]
    return text, entities


def trim_body(body: str | None) -> str | None:
    if not body:
        return None
    if len(body) > 3500:
        body = body[: 3500 - 1] + "…"
    return body if body.strip() else None


def split_body(body: str, max_len: int) -> list[str]:
    """Split body text into chunks that fit within max_len.

    Splits at paragraph boundaries (double newlines) first, then single newlines,
    then spaces, and finally hard cuts as a last resort.
    """
    if not body or len(body) <= max_len:
        return [body] if body else []

    chunks: list[str] = []
    remaining = body

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Try to find a good split point
        split_idx = _find_split_point(remaining, max_len)
        chunk = remaining[:split_idx].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_idx:].lstrip()

    return chunks


def _find_split_point(text: str, max_len: int) -> int:
    """Find the best split point within max_len characters.

    Priority: paragraph break > single newline > space > hard cut
    """
    search_region = text[:max_len]

    # Try paragraph break (double newline)
    para_idx = search_region.rfind("\n\n")
    if para_idx > max_len // 4:  # Only use if reasonably far into the text
        return para_idx + 2  # Include the newlines in the cut

    # Try single newline
    newline_idx = search_region.rfind("\n")
    if newline_idx > max_len // 4:
        return newline_idx + 1

    # Try space
    space_idx = search_region.rfind(" ")
    if space_idx > max_len // 4:
        return space_idx + 1

    # Hard cut as last resort
    return max_len


def prepare_telegram(parts: MarkdownParts) -> tuple[str, list[dict[str, Any]]]:
    trimmed = MarkdownParts(
        header=parts.header or "",
        body=trim_body(parts.body),
        footer=parts.footer,
    )
    return render_markdown(assemble_markdown_parts(trimmed))


def prepare_telegram_split(
    parts: MarkdownParts,
    max_message_len: int = TELEGRAM_MAX_MESSAGE_LEN,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Prepare telegram message(s), splitting if body exceeds max length.

    Returns a list of (text, entities) tuples. Each message includes the footer
    (for resume token continuity). Only the first message has the header.
    Continuation messages are prefixed with "…continued".
    """
    header = parts.header or ""
    body = parts.body or ""
    footer = parts.footer

    # Render header and footer to get their actual lengths after markdown processing
    header_rendered, _ = render_markdown(header) if header else ("", [])
    footer_rendered, _ = render_markdown(footer) if footer else ("", [])

    # Calculate overhead: header + footer + separators (2 newlines between sections)
    # For first message: header + 2 newlines + body + 2 newlines + footer
    # For continuation messages: "…continued" + 2 newlines + body + 2 newlines + footer
    continuation_marker = "…continued"
    first_overhead = len(header_rendered) + len(footer_rendered) + 4  # 2x "\n\n"
    cont_overhead = len(continuation_marker) + len(footer_rendered) + 4

    # Safety margin for markdown entity expansion
    safety_margin = 100

    first_body_max = max_message_len - first_overhead - safety_margin
    cont_body_max = max_message_len - cont_overhead - safety_margin

    # Ensure reasonable minimums
    first_body_max = max(first_body_max, 500)
    cont_body_max = max(cont_body_max, 500)

    # If body fits in single message, use original behavior
    if len(body) <= first_body_max:
        return [prepare_telegram(parts)]

    # Split the body
    body_chunks: list[str] = []

    # First chunk uses first_body_max
    if body:
        first_chunk_parts = split_body(body, first_body_max)
        if first_chunk_parts:
            body_chunks.append(first_chunk_parts[0])
            remaining = body[len(first_chunk_parts[0]):].lstrip()

            # Remaining chunks use cont_body_max
            if remaining:
                body_chunks.extend(split_body(remaining, cont_body_max))

    if not body_chunks:
        return [prepare_telegram(parts)]

    # Build message parts
    messages: list[tuple[str, list[dict[str, Any]]]] = []

    for i, chunk in enumerate(body_chunks):
        if i == 0:
            # First message: header + body + footer
            msg_parts = MarkdownParts(header=header, body=chunk, footer=footer)
        else:
            # Continuation messages: marker + body + footer
            msg_parts = MarkdownParts(
                header=continuation_marker, body=chunk, footer=footer
            )
        messages.append(render_markdown(assemble_markdown_parts(msg_parts)))

    return messages


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


def format_header(
    elapsed_s: float, item: int | None, *, label: str, engine: str
) -> str:
    elapsed = format_elapsed(elapsed_s)
    parts = [label, engine]
    parts.append(elapsed)
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
    if kind == "subagent":
        title = shorten(title, command_width)
        return f"subagent: {title}"
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
class RecentLine:
    action_id: str
    text: str
    completed: bool = False


class ExecProgressRenderer:
    def __init__(
        self,
        engine: str,
        max_actions: int = 5,
        command_width: int | None = MAX_PROGRESS_CMD_LEN,
        resume_formatter: Callable[[ResumeToken], str] | None = None,
    ) -> None:
        self.max_actions = max(0, int(max_actions))
        self.command_width = command_width
        self.lines: deque[RecentLine] = deque(maxlen=self.max_actions)
        self.action_count = 0
        self.seen_action_ids: set[str] = set()
        self.resume_token: ResumeToken | None = None
        self._resume_formatter = resume_formatter
        self.engine = engine

    def note_event(self, event: TakopiEvent) -> bool:
        match event:
            case StartedEvent(resume=resume):
                self.resume_token = resume
                return True
            case ActionEvent(action=action, phase=phase, ok=ok):
                if action.kind == "turn":
                    return False
                action_id = str(action.id or "")
                if not action_id:
                    return False
                completed = phase == "completed"
                has_open = self.has_open_line(action_id)
                is_update = phase == "updated" or (phase == "started" and has_open)
                phase_for_line = "updated" if is_update and not completed else phase
                line = format_action_line(
                    action, phase_for_line, ok, command_width=self.command_width
                )

                if action_id not in self.seen_action_ids:
                    self.seen_action_ids.add(action_id)
                    self.action_count += 1

                self.upsert_line(action_id, line=line, completed=completed)
                return True
            case _:
                return False

    def has_open_line(self, action_id: str) -> bool:
        return any(
            line.action_id == action_id and not line.completed for line in self.lines
        )

    def upsert_line(self, action_id: str, *, line: str, completed: bool) -> None:
        for i in range(len(self.lines) - 1, -1, -1):
            existing = self.lines[i]
            if existing.action_id == action_id and not existing.completed:
                self.lines[i] = RecentLine(
                    action_id=action_id,
                    text=line,
                    completed=existing.completed or completed,
                )
                return
        self.lines.append(
            RecentLine(action_id=action_id, text=line, completed=completed)
        )

    def render_progress_parts(
        self, elapsed_s: float, label: str = "working"
    ) -> MarkdownParts:
        step = self.action_count or None
        header = format_header(
            elapsed_s,
            step,
            label=label,
            engine=self.engine,
        )
        body = self.assemble_body([line.text for line in self.lines])
        return MarkdownParts(header=header, body=body, footer=self.render_footer())

    def render_final_parts(
        self, elapsed_s: float, answer: str, status: str = "done"
    ) -> MarkdownParts:
        step = self.action_count or None
        header = format_header(
            elapsed_s,
            step,
            label=status,
            engine=self.engine,
        )
        answer = (answer or "").strip()
        body = answer if answer else None
        return MarkdownParts(header=header, body=body, footer=self.render_footer())

    def render_footer(self) -> str | None:
        if not self.resume_token or self._resume_formatter is None:
            return None
        return self._resume_formatter(self.resume_token)

    @property
    def recent_actions(self) -> list[str]:
        return [line.text for line in self.lines]

    @staticmethod
    def assemble_body(lines: list[str]) -> str | None:
        if not lines:
            return None
        return HARD_BREAK.join(lines)
