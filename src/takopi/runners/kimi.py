"""Kimi Code CLI runner for takopi."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import msgspec

from ..backends import EngineBackend, EngineConfig
from ..events import EventFactory
from ..logging import get_logger
from ..model import Action, ActionKind, EngineId, ResumeToken, TakopiEvent
from ..runner import JsonlSubprocessRunner, ResumeTokenMixin, Runner
from ..schemas import kimi as kimi_schema
from .run_options import get_run_options
from .tool_actions import tool_input_path, tool_kind_and_title

logger = get_logger(__name__)

ENGINE: EngineId = "kimi"

# Matches: `kimi --session <token>` or `kimi -S <token>`
_RESUME_RE = re.compile(
    r"(?im)^\s*`?kimi\s+(?:--session|-S)\s+(?P<token>[^`\s]+)`?\s*$"
)


@dataclass(slots=True)
class KimiStreamState:
    """Mutable state for processing a Kimi stream."""

    factory: EventFactory = field(default_factory=lambda: EventFactory(ENGINE))
    pending_actions: dict[str, Action] = field(default_factory=dict)
    last_assistant_text: str | None = None
    note_seq: int = 0
    session_id: str | None = None
    did_start: bool = False


def _normalize_tool_result(content: Any) -> str:
    """Normalize tool result content to a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
    return str(content)


def _parse_tool_arguments(arguments: str) -> dict[str, Any]:
    """Parse the JSON arguments string from a tool call."""
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return {}


def _tool_kind_and_title(
    name: str, tool_input: dict[str, Any]
) -> tuple[ActionKind, str]:
    """Determine action kind and title for a tool call."""
    return tool_kind_and_title(name, tool_input, path_keys=("file_path", "path"))


def _tool_action(
    tool_call: kimi_schema.ToolCall,
) -> Action:
    """Create an Action from a Kimi tool call."""
    tool_id = tool_call.id
    tool_name = tool_call.function.name
    tool_input = _parse_tool_arguments(tool_call.function.arguments)

    kind, title = _tool_kind_and_title(tool_name, tool_input)

    detail: dict[str, Any] = {
        "name": tool_name,
        "input": tool_input,
    }

    if kind == "file_change":
        path = tool_input_path(tool_input, path_keys=("file_path", "path"))
        if path:
            detail["changes"] = [{"path": path, "kind": "update"}]

    return Action(id=tool_id, kind=kind, title=title, detail=detail)


def _tool_result_event(
    message: kimi_schema.ToolMessage,
    *,
    action: Action,
    factory: EventFactory,
) -> TakopiEvent:
    """Create an action_completed event from a tool result message."""
    raw_result = message.content
    normalized = _normalize_tool_result(raw_result)
    preview = normalized

    detail = action.detail | {
        "tool_use_id": message.tool_call_id,
        "result_preview": preview,
        "result_len": len(normalized),
        "is_error": False,
    }
    return factory.action_completed(
        action_id=action.id,
        kind=action.kind,
        title=action.title,
        ok=True,
        detail=detail,
    )


def translate_kimi_event(
    event: kimi_schema.StreamJsonMessage,
    *,
    title: str,
    state: KimiStreamState,
    factory: EventFactory,
) -> list[TakopiEvent]:
    """Translate a Kimi stream event to Takopi events."""
    match event:
        case kimi_schema.AssistantMessage(content=content, tool_calls=tool_calls):
            out: list[TakopiEvent] = []

            # Emit started event on first assistant message if we haven't already
            if not state.did_start:
                state.did_start = True
                # Generate a session ID if not provided by Kimi
                # (Kimi doesn't emit session IDs in the same way Claude does)
                if state.session_id is None:
                    import uuid

                    state.session_id = str(uuid.uuid4())
                token = ResumeToken(engine=ENGINE, value=state.session_id)
                out.append(factory.started(token, title=title))

            # Store assistant text for final answer
            if content:
                state.last_assistant_text = content

            # Process tool calls
            if tool_calls:
                for tool_call in tool_calls:
                    action = _tool_action(tool_call)
                    state.pending_actions[action.id] = action
                    out.append(
                        factory.action_started(
                            action_id=action.id,
                            kind=action.kind,
                            title=action.title,
                            detail=action.detail,
                        )
                    )

            return out

        case kimi_schema.ToolMessage(tool_call_id=tool_call_id):
            out: list[TakopiEvent] = []
            action = state.pending_actions.pop(tool_call_id, None)
            if action is None:
                action = Action(
                    id=tool_call_id,
                    kind="tool",
                    title="tool result",
                    detail={},
                )
            out.append(
                _tool_result_event(
                    event,
                    action=action,
                    factory=factory,
                )
            )
            return out

        case kimi_schema.UserMessage() | kimi_schema.SystemMessage():
            # User and system messages don't generate takopi events
            return []

        case _:
            return []


@dataclass(slots=True)
class KimiRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    """Runner for Kimi Code CLI."""

    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE

    kimi_cmd: str = "kimi"
    model: str | None = None
    extra_args: list[str] = field(default_factory=list)
    session_title: str = "kimi"
    logger = logger

    def format_resume(self, token: ResumeToken) -> str:
        if token.engine != ENGINE:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`kimi --session {token.value}`"

    def _build_args(self, prompt: str, resume: ResumeToken | None) -> list[str]:
        run_options = get_run_options()
        args: list[str] = ["--print", "--output-format", "stream-json"]

        if resume is not None:
            args.extend(["--session", resume.value])

        model = self.model
        if run_options is not None and run_options.model:
            model = run_options.model
        if model is not None:
            args.extend(["--model", str(model)])

        # Add any extra args from config
        args.extend(self.extra_args)

        args.extend(["-p", prompt])
        return args

    def command(self) -> str:
        return self.kimi_cmd

    def build_args(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> list[str]:
        return self._build_args(prompt, resume)

    def stdin_payload(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> bytes | None:
        # Kimi takes prompt via -p argument, not stdin
        return None

    def env(self, *, state: Any) -> dict[str, str] | None:
        return None

    def new_state(self, prompt: str, resume: ResumeToken | None) -> KimiStreamState:
        state = KimiStreamState()
        # If resuming, use the provided session ID
        if resume is not None:
            state.session_id = resume.value
        return state

    def start_run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: KimiStreamState,
    ) -> None:
        pass

    def decode_jsonl(
        self,
        *,
        line: bytes,
    ) -> kimi_schema.StreamJsonMessage:
        return kimi_schema.decode_stream_json_line(line)

    def decode_error_events(
        self,
        *,
        raw: str,
        line: str,
        error: Exception,
        state: KimiStreamState,
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

    def invalid_json_events(
        self,
        *,
        raw: str,
        line: str,
        state: KimiStreamState,
    ) -> list[TakopiEvent]:
        return []

    def translate(
        self,
        data: kimi_schema.StreamJsonMessage,
        *,
        state: KimiStreamState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        return translate_kimi_event(
            data,
            title=self.session_title,
            state=state,
            factory=state.factory,
        )

    def process_error_events(
        self,
        rc: int,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: KimiStreamState,
    ) -> list[TakopiEvent]:
        message = f"kimi failed (rc={rc})."
        resume_for_completed = found_session or resume
        return [
            self.note_event(message, state=state, ok=False),
            state.factory.completed_error(
                error=message,
                resume=resume_for_completed,
            ),
        ]

    def stream_end_events(
        self,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: KimiStreamState,
    ) -> list[TakopiEvent]:
        # Kimi doesn't emit a "result" message like Claude, so we synthesize
        # a completed event from the last assistant text
        if state.last_assistant_text:
            resume_token = found_session or resume
            if resume_token is None and state.session_id:
                resume_token = ResumeToken(engine=ENGINE, value=state.session_id)
            return [
                state.factory.completed_ok(
                    answer=state.last_assistant_text,
                    resume=resume_token,
                )
            ]

        if not found_session:
            message = "kimi finished but no session_id was captured"
            resume_for_completed = resume
            return [
                state.factory.completed_error(
                    error=message,
                    resume=resume_for_completed,
                )
            ]

        message = "kimi finished without a result"
        return [
            state.factory.completed_error(
                error=message,
                answer=state.last_assistant_text or "",
                resume=found_session,
            )
        ]


def build_runner(config: EngineConfig, _config_path: Path) -> Runner:
    """Build a KimiRunner from configuration."""
    kimi_cmd = shutil.which("kimi") or "kimi"

    model = config.get("model")
    extra_args = config.get("extra_args", [])
    title = str(model) if model is not None else "kimi"

    return KimiRunner(
        kimi_cmd=kimi_cmd,
        model=model,
        extra_args=extra_args,
        session_title=title,
    )


BACKEND = EngineBackend(
    id="kimi",
    build_runner=build_runner,
    install_cmd="curl -LsSf https://code.kimi.com/install.sh | bash",
)
