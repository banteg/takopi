"""Pure renderers for Takopi events (no engine-native event handling)."""

from __future__ import annotations

import textwrap
from collections import deque
from typing import Callable

from .model import Action, ResumeToken, TakopiEvent

STATUS_RUNNING = "▸"
STATUS_UPDATE = "↻"
STATUS_DONE = "✓"
STATUS_FAIL = "✗"
HEADER_SEP = " · "
HARD_BREAK = "  \n"

MAX_PROGRESS_CMD_LEN = 300
MAX_QUERY_LEN = 60
MAX_FILE_CHANGES_INLINE = 3


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


def _action_status_symbol(
    action: Action, *, completed: bool, ok: bool | None = None
) -> str:
    if not completed:
        return STATUS_RUNNING
    if ok is not None:
        return STATUS_DONE if ok else STATUS_FAIL
    detail = action.detail or {}
    exit_code = detail.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return STATUS_FAIL
    return STATUS_DONE


def _action_exit_suffix(action: Action) -> str:
    detail = action.detail or {}
    exit_code = detail.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return f" (exit {exit_code})"
    return ""


def _format_action_title(action: Action, *, command_width: int | None) -> str:
    title = str(action.title or "")
    kind = action.kind
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
        detail = action.detail or {}
        changes = detail.get("changes")
        if isinstance(changes, list) and changes:
            rendered: list[str] = []
            for raw_change in changes:
                if not isinstance(raw_change, dict):
                    continue
                path = raw_change.get("path")
                if not path:
                    continue
                kind = raw_change.get("kind")
                prefix = {"add": "+", "delete": "-", "update": "~"}.get(kind, "~")
                rendered.append(f"{prefix}{path}")
            if rendered:
                if len(rendered) > MAX_FILE_CHANGES_INLINE:
                    remaining = len(rendered) - MAX_FILE_CHANGES_INLINE
                    rendered = rendered[:MAX_FILE_CHANGES_INLINE]
                    rendered.append(f"…(+{remaining})")
                title = ", ".join(rendered)
                title = _shorten(title, command_width)
                return f"files: {title}"
        title = _shorten(title, command_width)
        return f"files: {title}"
    if kind == "note":
        title = _shorten(title, MAX_QUERY_LEN)
        return title
    if kind == "warning":
        title = _shorten(title, MAX_QUERY_LEN)
        return title
    if kind == "turn":
        return _shorten(title, command_width)
    return _shorten(title, command_width)


def render_event_cli(
    event: TakopiEvent, last_item: int | None = None
) -> tuple[int | None, list[str]]:
    lines: list[str] = []
    if event.type == "started":
        lines.append(str(event.engine))
    elif event.type == "action":
        action = event.action
        phase = event.phase
        if phase == "completed":
            status = _action_status_symbol(action, completed=True, ok=event.ok)
            suffix = _action_exit_suffix(action)
        else:
            status = STATUS_UPDATE if phase == "updated" else STATUS_RUNNING
            suffix = ""
        title = _format_action_title(action, command_width=MAX_PROGRESS_CMD_LEN)
        lines.append(f"{status} {title}{suffix}")
    else:
        return last_item, []
    return last_item, lines


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
        self.recent_actions: deque[str] = deque(maxlen=max_actions)
        self._recent_action_ids: deque[str] = deque(maxlen=max_actions)
        self._recent_action_completed: deque[bool] = deque(maxlen=max_actions)
        self.action_count = 0
        self._started_counts: dict[str, int] = {}
        self.resume_token: ResumeToken | None = None
        self.session_title: str | None = None
        self._resume_formatter = resume_formatter
        self.show_title = show_title

    def note_event(self, event: TakopiEvent) -> bool:
        if event.type == "started":
            self.resume_token = event.resume
            self.session_title = event.title
            return True

        if event.type == "action":
            action = event.action
            phase = event.phase
            action_id = str(action.id or "")
            if not action_id:
                return False
            completed = phase == "completed"
            ok = event.ok if completed else None
            if completed:
                is_update = False
            else:
                started_count = self._started_counts.get(action_id, 0)
                is_update = phase == "updated" or started_count > 0
                if started_count == 0:
                    self.action_count += 1
                    self._started_counts[action_id] = 1
                elif phase == "started":
                    self._started_counts[action_id] = started_count + 1
                else:
                    self._started_counts[action_id] = started_count
        else:
            return False

        if completed:
            count = self._started_counts.get(action_id, 0)
            if count <= 0:
                self.action_count += 1
            elif count == 1:
                self._started_counts.pop(action_id, None)
            else:
                self._started_counts[action_id] = count - 1

        status = (
            STATUS_UPDATE
            if (is_update and not completed)
            else _action_status_symbol(action, completed=completed, ok=ok)
        )
        title = _format_action_title(action, command_width=self.command_width)
        suffix = _action_exit_suffix(action) if completed else ""
        line = f"{status} {title}{suffix}"

        self._append_action(action_id, completed=completed, line=line)
        return True

    def _append_action(self, action_id: str, *, completed: bool, line: str) -> None:
        if not completed:
            for i in range(len(self._recent_action_ids) - 1, -1, -1):
                if (
                    self._recent_action_ids[i] == action_id
                    and not self._recent_action_completed[i]
                ):
                    self.recent_actions[i] = line
                    return
        if completed:
            for i in range(len(self._recent_action_ids) - 1, -1, -1):
                if (
                    self._recent_action_ids[i] == action_id
                    and not self._recent_action_completed[i]
                ):
                    self.recent_actions[i] = line
                    self._recent_action_completed[i] = True
                    return

        if len(self.recent_actions) >= self.max_actions:
            self.recent_actions.popleft()
            self._recent_action_ids.popleft()
            self._recent_action_completed.popleft()

        self.recent_actions.append(line)
        self._recent_action_ids.append(action_id)
        self._recent_action_completed.append(completed)

    def render_progress(self, elapsed_s: float, label: str = "working") -> str:
        step = self.action_count or None
        header = format_header(elapsed_s, step, label=self._label_with_title(label))
        message = self._assemble(header, list(self.recent_actions))
        return self._append_resume(message)

    def render_final(self, elapsed_s: float, answer: str, status: str = "done") -> str:
        step = self.action_count or None
        header = format_header(elapsed_s, step, label=self._label_with_title(status))
        answer = (answer or "").strip()
        message = header + ("\n\n" + answer if answer else "")
        return self._append_resume(message)

    def _label_with_title(self, label: str) -> str:
        if self.show_title and self.session_title:
            return f"{label} ({self.session_title})"
        return label

    def _append_resume(self, message: str) -> str:
        if not self.resume_token or self._resume_formatter is None:
            return message
        return message + "\n\n" + self._resume_formatter(self.resume_token)

    @staticmethod
    def _assemble(header: str, lines: list[str]) -> str:
        return header if not lines else header + "\n\n" + HARD_BREAK.join(lines)
