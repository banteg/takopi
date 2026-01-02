"""OpenCode CLI runner.

This runner integrates with the OpenCode CLI (https://github.com/sst/opencode).

OpenCode outputs JSON events in a streaming format with types:
- step_start: Marks the beginning of a processing step
- tool_use: Tool invocation with input/output
- text: Text output from the model
- step_finish: Marks the end of a step (with reason: "stop" or "tool-calls")

Session IDs use the format: ses_XXXX (e.g., ses_494719016ffe85dkDMj0FPRbHK)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..backends import EngineBackend, EngineConfig
from ..config import ConfigError
from ..model import (
    Action,
    ActionEvent,
    ActionKind,
    CompletedEvent,
    EngineId,
    ResumeToken,
    StartedEvent,
    TakopiEvent,
)
from ..runner import JsonlSubprocessRunner, ResumeTokenMixin, Runner
from ..utils.paths import relativize_command, relativize_path

logger = logging.getLogger(__name__)

ENGINE: EngineId = EngineId("opencode")
STDERR_TAIL_LINES = 200

_RESUME_RE = re.compile(
    r"(?im)^\s*`?opencode(?:\s+run)?\s+(?:--session|-s)\s+(?P<token>ses_[A-Za-z0-9]+)`?\s*$"
)


@dataclass(slots=True)
class OpenCodeStreamState:
    """State tracked during OpenCode JSONL streaming."""

    pending_actions: dict[str, Action] = field(default_factory=dict)
    last_text: str | None = None
    note_seq: int = 0
    session_id: str | None = None
    emitted_started: bool = False
    saw_step_finish: bool = False
    total_cost: float = 0.0
    total_tokens: dict[str, int] = field(default_factory=dict)


def _action_event(
    *,
    phase: Literal["started", "updated", "completed"],
    action: Action,
    ok: bool | None = None,
    message: str | None = None,
    level: Literal["debug", "info", "warning", "error"] | None = None,
) -> ActionEvent:
    return ActionEvent(
        engine=ENGINE,
        action=action,
        phase=phase,
        ok=ok,
        message=message,
        level=level,
    )


def _tool_kind_and_title(
    tool_name: str, tool_input: dict[str, Any]
) -> tuple[ActionKind, str]:
    """Map OpenCode tool names to Takopi action kinds and titles."""
    name_lower = tool_name.lower()

    if name_lower in {"bash", "shell"}:
        command = tool_input.get("command")
        display = relativize_command(str(command or tool_name))
        return "command", display

    if name_lower in {"edit", "write", "multiedit"}:
        path = tool_input.get("file_path") or tool_input.get("filePath")
        if path:
            return "file_change", relativize_path(str(path))
        return "file_change", str(tool_name)

    if name_lower == "read":
        path = tool_input.get("file_path") or tool_input.get("filePath")
        if path:
            return "tool", f"read: `{relativize_path(str(path))}`"
        return "tool", "read"

    if name_lower == "glob":
        pattern = tool_input.get("pattern")
        if pattern:
            return "tool", f"glob: `{pattern}`"
        return "tool", "glob"

    if name_lower == "grep":
        pattern = tool_input.get("pattern")
        if pattern:
            return "tool", f"grep: {pattern}"
        return "tool", "grep"

    if name_lower in {"websearch", "web_search"}:
        query = tool_input.get("query")
        return "web_search", str(query or "search")

    if name_lower in {"webfetch", "web_fetch"}:
        url = tool_input.get("url")
        return "web_search", str(url or "fetch")

    if name_lower in {"todowrite", "todoread"}:
        return "note", "update todos" if "write" in name_lower else "read todos"

    if name_lower == "task":
        desc = tool_input.get("description") or tool_input.get("prompt")
        return "tool", str(desc or tool_name)

    return "tool", tool_name


def _normalize_tool_title(
    title: str,
    *,
    tool_input: dict[str, Any],
) -> str:
    if "`" in title:
        return title

    path = tool_input.get("file_path") or tool_input.get("filePath")
    if isinstance(path, str) and path:
        rel_path = relativize_path(path)
        if title == path or title == rel_path:
            return f"`{rel_path}`"

    return title


def _extract_tool_action(event: dict[str, Any]) -> Action | None:
    """Extract an Action from an OpenCode tool_use event."""
    part = event.get("part") or {}
    state = part.get("state") or {}

    call_id = part.get("callID")
    if not isinstance(call_id, str) or not call_id:
        call_id = part.get("id")
        if not isinstance(call_id, str) or not call_id:
            return None

    tool_name = part.get("tool") or "tool"
    tool_input = state.get("input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    kind, title = _tool_kind_and_title(tool_name, tool_input)

    state_title = state.get("title")
    if isinstance(state_title, str) and state_title:
        title = _normalize_tool_title(state_title, tool_input=tool_input)

    detail: dict[str, Any] = {
        "name": tool_name,
        "input": tool_input,
        "callID": call_id,
    }

    if kind == "file_change":
        path = tool_input.get("file_path") or tool_input.get("filePath")
        if path:
            detail["changes"] = [{"path": path, "kind": "update"}]

    return Action(id=call_id, kind=kind, title=title, detail=detail)


def _usage_from_tokens(tokens: dict[str, int], cost: float) -> dict[str, Any]:
    """Build usage payload from accumulated token counts."""
    usage: dict[str, Any] = {}
    if cost > 0:
        usage["total_cost_usd"] = cost
    if tokens:
        usage["tokens"] = tokens
    return usage


def translate_opencode_event(
    event: dict[str, Any],
    *,
    title: str,
    state: OpenCodeStreamState,
) -> list[TakopiEvent]:
    """Translate an OpenCode JSON event into Takopi events."""
    etype = event.get("type")
    session_id = event.get("sessionID")

    if isinstance(session_id, str) and session_id:
        if state.session_id is None:
            state.session_id = session_id

    if etype == "step_start":
        if not state.emitted_started and state.session_id:
            state.emitted_started = True
            return [
                StartedEvent(
                    engine=ENGINE,
                    resume=ResumeToken(engine=ENGINE, value=state.session_id),
                    title=title,
                )
            ]
        return []

    if etype == "tool_use":
        part = event.get("part") or {}
        tool_state = part.get("state") or {}
        status = tool_state.get("status")

        action = _extract_tool_action(event)
        if action is None:
            return []

        if status == "completed":
            output = tool_state.get("output")
            metadata = tool_state.get("metadata") or {}
            exit_code = metadata.get("exit")

            is_error = False
            if isinstance(exit_code, int) and exit_code != 0:
                is_error = True

            detail = dict(action.detail)
            if output is not None:
                detail["output_preview"] = (
                    str(output)[:500] if len(str(output)) > 500 else str(output)
                )
            detail["exit_code"] = exit_code

            state.pending_actions.pop(action.id, None)

            return [
                _action_event(
                    phase="completed",
                    action=Action(
                        id=action.id,
                        kind=action.kind,
                        title=action.title,
                        detail=detail,
                    ),
                    ok=not is_error,
                )
            ]
        if status == "error":
            error = tool_state.get("error")
            metadata = tool_state.get("metadata") or {}
            exit_code = metadata.get("exit")

            detail = dict(action.detail)
            if error is not None:
                detail["error"] = error
            detail["exit_code"] = exit_code

            state.pending_actions.pop(action.id, None)

            return [
                _action_event(
                    phase="completed",
                    action=Action(
                        id=action.id,
                        kind=action.kind,
                        title=action.title,
                        detail=detail,
                    ),
                    ok=False,
                    message=str(error) if error is not None else None,
                )
            ]
        else:
            state.pending_actions[action.id] = action
            return [_action_event(phase="started", action=action)]

    if etype == "text":
        part = event.get("part") or {}
        text = part.get("text")
        if isinstance(text, str) and text:
            if state.last_text is None:
                state.last_text = text
            else:
                state.last_text += text
        return []

    if etype == "step_finish":
        part = event.get("part") or {}
        reason = part.get("reason")
        state.saw_step_finish = True

        tokens = part.get("tokens") or {}
        if isinstance(tokens, dict):
            for key in ("input", "output", "reasoning"):
                value = tokens.get(key)
                if isinstance(value, int):
                    state.total_tokens[key] = state.total_tokens.get(key, 0) + value
            cache = tokens.get("cache") or {}
            if isinstance(cache, dict):
                for key in ("read", "write"):
                    value = cache.get(key)
                    if not isinstance(value, int):
                        continue
                    cache_key = f"cache_{key}"
                    state.total_tokens[cache_key] = (
                        state.total_tokens.get(cache_key, 0) + value
                    )

        cost = part.get("cost")
        if isinstance(cost, (int, float)):
            state.total_cost += cost

        if reason == "stop":
            resume = None
            if state.session_id:
                resume = ResumeToken(engine=ENGINE, value=state.session_id)

            usage = _usage_from_tokens(state.total_tokens, state.total_cost)

            return [
                CompletedEvent(
                    engine=ENGINE,
                    ok=True,
                    answer=state.last_text or "",
                    resume=resume,
                    usage=usage or None,
                )
            ]
        return []

    if etype == "error":
        raw_message = event.get("message")
        if raw_message is None:
            raw_message = event.get("error")

        message = raw_message
        if isinstance(message, dict):
            data = message.get("data")
            if isinstance(data, dict) and data.get("message"):
                message = data.get("message")
            else:
                message = (
                    message.get("message") or message.get("name") or "opencode error"
                )
        elif message is None:
            message = "opencode error"

        resume = None
        if state.session_id:
            resume = ResumeToken(engine=ENGINE, value=state.session_id)

        return [
            CompletedEvent(
                engine=ENGINE,
                ok=False,
                answer=state.last_text or "",
                resume=resume,
                error=str(message),
            )
        ]

    return []


@dataclass
class OpenCodeRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    """Runner for OpenCode CLI."""

    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE

    opencode_cmd: str = "opencode"
    model: str | None = None
    session_title: str = "opencode"
    stderr_tail_lines: int = STDERR_TAIL_LINES
    logger: logging.Logger = logger

    def format_resume(self, token: ResumeToken) -> str:
        if token.engine != ENGINE:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`opencode --session {token.value}`"

    def command(self) -> str:
        return self.opencode_cmd

    def build_args(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> list[str]:
        _ = state
        args = ["run", "--format", "json"]
        if resume is not None:
            args.extend(["--session", resume.value])
        if self.model is not None:
            args.extend(["--model", str(self.model)])
        args.extend(["--", prompt])
        return args

    def stdin_payload(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> bytes | None:
        _ = prompt, resume, state
        return None

    def new_state(self, prompt: str, resume: ResumeToken | None) -> OpenCodeStreamState:
        _ = prompt, resume
        return OpenCodeStreamState()

    def start_run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: OpenCodeStreamState,
    ) -> None:
        _ = state
        logger.info(
            "[opencode] start run resume=%r",
            resume.value if resume else None,
        )
        logger.debug("[opencode] prompt: %s", prompt)

    def invalid_json_events(
        self,
        *,
        raw: str,
        line: str,
        state: OpenCodeStreamState,
    ) -> list[TakopiEvent]:
        _ = line
        message = "invalid JSON from opencode; ignoring line"
        return [self.note_event(message, state=state, detail={"line": raw})]

    def translate(
        self,
        data: dict[str, Any],
        *,
        state: OpenCodeStreamState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        _ = resume, found_session
        return translate_opencode_event(
            data,
            title=self.session_title,
            state=state,
        )

    def process_error_events(
        self,
        rc: int,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        stderr_tail: str,
        state: OpenCodeStreamState,
    ) -> list[TakopiEvent]:
        message = f"opencode failed (rc={rc})."
        resume_for_completed = found_session or resume
        return [
            self.note_event(
                message,
                state=state,
                ok=False,
                detail={"stderr_tail": stderr_tail},
            ),
            CompletedEvent(
                engine=ENGINE,
                ok=False,
                answer=state.last_text or "",
                resume=resume_for_completed,
                error=message,
            ),
        ]

    def stream_end_events(
        self,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        stderr_tail: str,
        state: OpenCodeStreamState,
    ) -> list[TakopiEvent]:
        _ = stderr_tail
        if not found_session:
            message = "opencode finished but no session_id was captured"
            resume_for_completed = resume
            return [
                CompletedEvent(
                    engine=ENGINE,
                    ok=False,
                    answer=state.last_text or "",
                    resume=resume_for_completed,
                    error=message,
                )
            ]

        if state.saw_step_finish:
            usage = _usage_from_tokens(state.total_tokens, state.total_cost)
            return [
                CompletedEvent(
                    engine=ENGINE,
                    ok=True,
                    answer=state.last_text or "",
                    resume=found_session,
                    usage=usage or None,
                )
            ]

        message = "opencode finished without a result event"
        return [
            CompletedEvent(
                engine=ENGINE,
                ok=False,
                answer=state.last_text or "",
                resume=found_session,
                error=message,
            )
        ]


def build_runner(config: EngineConfig, config_path: Path) -> Runner:
    """Build an OpenCodeRunner from configuration."""
    opencode_cmd = "opencode"

    model = config.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigError(
            f"Invalid `opencode.model` in {config_path}; expected a string."
        )

    title = str(model) if model is not None else "opencode"

    return OpenCodeRunner(
        opencode_cmd=opencode_cmd,
        model=model,
        session_title=title,
    )


BACKEND = EngineBackend(
    id="opencode",
    build_runner=build_runner,
    install_cmd="npm i -g opencode-ai@latest",
)
