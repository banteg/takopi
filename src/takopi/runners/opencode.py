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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import msgspec

from ..backends import EngineBackend, EngineConfig
from ..config import ConfigError
from ..logging import get_logger
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
from .run_options import get_run_options
from ..schemas import opencode as opencode_schema
from ..utils.paths import relativize_path
from .tool_actions import tool_input_path, tool_kind_and_title

logger = get_logger(__name__)

ENGINE: EngineId = "opencode"

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
    return tool_kind_and_title(
        tool_name,
        tool_input,
        path_keys=("file_path", "filePath"),
        task_kind="tool",
    )


def _normalize_tool_title(
    title: str,
    *,
    tool_input: dict[str, Any],
) -> str:
    if "`" in title:
        return title

    path = tool_input_path(tool_input, path_keys=("file_path", "filePath"))
    if isinstance(path, str) and path:
        rel_path = relativize_path(path)
        if title in (path, rel_path):
            return f"`{rel_path}`"

    return title


def _extract_tool_action(part: dict[str, Any]) -> Action | None:
    """Extract an Action from an OpenCode tool_use part."""
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


def translate_opencode_event(
    event: opencode_schema.OpenCodeEvent,
    *,
    title: str,
    state: OpenCodeStreamState,
) -> list[TakopiEvent]:
    """Translate an OpenCode JSON event into Takopi events."""
    session_id = event.sessionID

    if isinstance(session_id, str) and session_id and state.session_id is None:
        state.session_id = session_id

    match event:
        case opencode_schema.StepStart():
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

        case opencode_schema.ToolUse(part=part):
            part = part or {}
            tool_state = part.get("state") or {}
            status = tool_state.get("status")

            action = _extract_tool_action(part)
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

        case opencode_schema.Text(part=part):
            part = part or {}
            text = part.get("text")
            if isinstance(text, str) and text:
                if state.last_text is None:
                    state.last_text = text
                else:
                    state.last_text += text
            return []

        case opencode_schema.StepFinish(part=part):
            part = part or {}
            reason = part.get("reason")
            state.saw_step_finish = True

            if reason == "stop":
                resume = None
                if state.session_id:
                    resume = ResumeToken(engine=ENGINE, value=state.session_id)

                return [
                    CompletedEvent(
                        engine=ENGINE,
                        ok=True,
                        answer=state.last_text or "",
                        resume=resume,
                    )
                ]
            return []

        case opencode_schema.Error(error=error_value, message=message_value):
            raw_message = message_value if message_value is not None else error_value

            message = raw_message
            if isinstance(message, dict):
                data = message.get("data")
                if isinstance(data, dict) and data.get("message"):
                    message = data.get("message")
                else:
                    message = (
                        message.get("message")
                        or message.get("name")
                        or "opencode error"
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

        case _:
            return []


@dataclass(slots=True)
class OpenCodeRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    """Runner for OpenCode CLI."""

    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE

    opencode_cmd: str = "opencode"
    model: str | None = None
    session_title: str = "opencode"
    logger = logger

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
        run_options = get_run_options()
        args = ["run", "--format", "json"]
        if resume is not None:
            args.extend(["--session", resume.value])
        model = self.model
        if run_options is not None and run_options.model:
            model = run_options.model
        if model is not None:
            args.extend(["--model", str(model)])
        args.extend(["--", prompt])
        return args

    def stdin_payload(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> bytes | None:
        return None

    def new_state(self, prompt: str, resume: ResumeToken | None) -> OpenCodeStreamState:
        return OpenCodeStreamState()

    def start_run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: OpenCodeStreamState,
    ) -> None:
        pass

    def invalid_json_events(
        self,
        *,
        raw: str,
        line: str,
        state: OpenCodeStreamState,
    ) -> list[TakopiEvent]:
        message = "invalid JSON from opencode; ignoring line"
        return [self.note_event(message, state=state, detail={"line": raw})]

    def translate(
        self,
        data: opencode_schema.OpenCodeEvent,
        *,
        state: OpenCodeStreamState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        return translate_opencode_event(
            data,
            title=self.session_title,
            state=state,
        )

    def decode_jsonl(self, *, line: bytes) -> opencode_schema.OpenCodeEvent:
        return opencode_schema.decode_event(line)

    def decode_error_events(
        self,
        *,
        raw: str,
        line: str,
        error: Exception,
        state: OpenCodeStreamState,
    ) -> list[TakopiEvent]:
        if isinstance(error, msgspec.DecodeError):
            self.get_logger().warning(
                "jsonl.msgspec.invalid",
                tag=self.tag(),
                error=str(error),
                error_type=error.__class__.__name__,
            )
            return []
        return super().decode_error_events(
            raw=raw,
            line=line,
            error=error,
            state=state,
        )

    def process_error_events(
        self,
        rc: int,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: OpenCodeStreamState,
    ) -> list[TakopiEvent]:
        message = f"opencode failed (rc={rc})."
        resume_for_completed = found_session or resume
        return [
            self.note_event(
                message,
                state=state,
                ok=False,
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
        state: OpenCodeStreamState,
    ) -> list[TakopiEvent]:
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
            return [
                CompletedEvent(
                    engine=ENGINE,
                    ok=True,
                    answer=state.last_text or "",
                    resume=found_session,
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
    install_cmd="npm install -g opencode-ai@latest",
)
