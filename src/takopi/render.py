"""Pure renderers for Takopi events (no engine-native event handling)."""

from __future__ import annotations

import textwrap
from collections import deque
from collections.abc import Callable
from pathlib import Path

from .model import Action, ActionEvent, ResumeToken, StartedEvent, TakopiEvent
from .utils.paths import relativize_path

STATUS_RUNNING = "▸"
STATUS_UPDATE = "↻"
STATUS_DONE = "✓"
STATUS_FAIL = "✗"
HEADER_SEP = " · "
HARD_BREAK = "  \n"

MAX_PROGRESS_CMD_LEN = 300
MAX_FILE_CHANGES_INLINE = 3


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
    return textwrap.shorten(text, width=width, placeholder="…")


def action_status_symbol(
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


def action_exit_suffix(action: Action) -> str:
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


def phase_status_and_suffix(event: ActionEvent) -> tuple[str, str]:
    action = event.action
    match event.phase:
        case "completed":
            status = action_status_symbol(action, completed=True, ok=event.ok)
            suffix = action_exit_suffix(action)
            return status, suffix
        case "updated":
            return STATUS_UPDATE, ""
        case _:
            return STATUS_RUNNING, ""


def render_event_cli(event: TakopiEvent) -> list[str]:
    match event:
        case StartedEvent(engine=engine):
            return [str(engine)]
        case ActionEvent() as action_event:
            action = action_event.action
            if action.kind == "turn":
                return []
            status, suffix = phase_status_and_suffix(action_event)
            title = format_action_title(action, command_width=MAX_PROGRESS_CMD_LEN)
            return [f"{status} {title}{suffix}"]
        case _:
            return []


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
                completed = phase == "completed"
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
            case _:
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
            else action_status_symbol(action, completed=completed, ok=ok)
        )
        title = format_action_title(action, command_width=self.command_width)
        suffix = action_exit_suffix(action) if completed else ""
        line = f"{status} {title}{suffix}"

        self._append_action(action_id, completed=completed, line=line)
        return True

    def _append_action(self, action_id: str, *, completed: bool, line: str) -> None:
        for i in range(len(self._recent_action_ids) - 1, -1, -1):
            if (
                self._recent_action_ids[i] == action_id
                and not self._recent_action_completed[i]
            ):
                self.recent_actions[i] = line
                if completed:
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
