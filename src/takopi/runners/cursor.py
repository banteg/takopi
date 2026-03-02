"""Cursor CLI runner for Takopi.

Bridges the Cursor Agent CLI (``agent -p --output-format stream-json``) to Takopi's
normalized event model. The CLI requires a PTY to produce output, so we wrap it with
``script -qfc`` to allocate one.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgspec

from ..backends import EngineBackend, EngineConfig
from ..config import ConfigError
from ..events import EventFactory
from ..logging import get_logger
from ..model import ActionKind, EngineId, ResumeToken, TakopiEvent
from ..runner import JsonlSubprocessRunner, ResumeTokenMixin, Runner
from ..schemas import cursor as cursor_schema
from .run_options import get_run_options

logger = get_logger(__name__)

ENGINE: EngineId = "cursor"

__all__ = [
    "ENGINE",
    "CursorRunner",
    "translate_cursor_event",
    "BACKEND",
]

# Resume line: ``agent --resume <session_id>``
_RESUME_RE = re.compile(r"(?im)^\s*`?agent\s+--resume\s+(?P<token>[^`\s]+)`?\s*$")


def _extract_tool_title(tool_call: dict[str, Any] | None) -> tuple[ActionKind, str]:
    """Return (kind, title) from a Cursor tool_call dict."""
    if not tool_call:
        return "tool", "tool"

    # Cursor tool_call has keys like "readToolCall", "writeToolCall",
    # "lsToolCall", "shellToolCall", etc.
    for key, value in tool_call.items():
        if not isinstance(value, dict):
            continue
        args = value.get("args", {})
        if not isinstance(args, dict):
            args = {}

        if "shell" in key.lower() or "bash" in key.lower():
            command = args.get("command", "shell")
            return "command", str(command)[:80]
        if "read" in key.lower():
            path = args.get("path", "read")
            return "tool", f"read {Path(path).name}" if path else "read"
        if "write" in key.lower():
            path = args.get("path", "write")
            return "tool", f"write {Path(path).name}" if path else "write"
        if "ls" in key.lower():
            path = args.get("path", "ls")
            return "tool", f"ls {Path(path).name}" if path else "ls"
        if "grep" in key.lower() or "search" in key.lower():
            pattern = args.get("pattern", "search")
            return "tool", f"search {pattern}"[:60]
        # Generic fallback
        clean_key = key.replace("ToolCall", "").replace("toolCall", "")
        return "tool", clean_key or "tool"

    return "tool", "tool"


def _build_answer_with_thinking(
    thinking_blocks: list[str] | None,
    answer_text: str,
) -> str:
    """Build final answer with thinking blocks as Discord blockquotes."""
    if not thinking_blocks:
        return answer_text

    parts: list[str] = []
    for i, block in enumerate(thinking_blocks):
        # Format as Discord blockquote (> prefix on each line)
        header = (
            f"**💭 Thinking {i + 1}**"
            if len(thinking_blocks) > 1
            else "**💭 Thinking**"
        )
        quoted_lines = [
            f"> {line}" if line.strip() else ">" for line in block.split("\n")
        ]
        parts.append(f"> {header}\n" + "\n".join(quoted_lines))

    thinking_section = "\n\n".join(parts)
    return f"{thinking_section}\n\n---\n\n{answer_text}"


def translate_cursor_event(
    event: cursor_schema.CursorEvent,
    *,
    title: str,
    state: CursorRunState,
    factory: EventFactory,
) -> list[TakopiEvent]:
    """Translate a single Cursor CLI event into Takopi event(s)."""
    match event:
        case cursor_schema.SystemInit(session_id=session_id, model=model):
            token = ResumeToken(engine=ENGINE, value=session_id)
            event_title = str(model) if model else title
            return [factory.started(token, title=event_title)]

        case cursor_schema.ToolCall(
            subtype=subtype,
            call_id=call_id,
            tool_call=tool_call,
        ):
            if not call_id:
                return []
            kind, tool_title = _extract_tool_title(tool_call)
            if subtype == "started":
                return [
                    factory.action_started(
                        action_id=call_id,
                        kind=kind,
                        title=tool_title,
                    )
                ]
            if subtype == "completed":
                # Check for errors in tool result
                ok = True
                if tool_call:
                    for value in tool_call.values():
                        if isinstance(value, dict) and "result" in value:
                            result = value["result"]
                            if isinstance(result, dict) and result.get("error"):
                                ok = False
                return [
                    factory.action_completed(
                        action_id=call_id,
                        kind=kind,
                        title=tool_title,
                        ok=ok,
                    )
                ]

        case cursor_schema.AssistantResponse(message=message):
            # Extract text from assistant message for final_answer tracking
            if message and message.content:
                texts = [c.text for c in message.content if c.text]
                if texts:
                    answer_text = "\n".join(texts)
                    # Build final answer: thinking blocks + answer
                    state.final_answer = _build_answer_with_thinking(
                        state.thinking_blocks, answer_text
                    )
            return []

        case cursor_schema.Result(
            subtype=subtype,
            result=result,
            session_id=session_id,
            duration_ms=duration_ms,
        ):
            raw_answer = result or state.final_answer or ""
            # If final_answer wasn't built with thinking yet, add it now
            if (
                raw_answer
                and state.thinking_blocks
                and not raw_answer.startswith("> **")
            ):
                answer = _build_answer_with_thinking(state.thinking_blocks, raw_answer)
            else:
                answer = raw_answer
            resume = (
                ResumeToken(engine=ENGINE, value=session_id) if session_id else None
            )
            usage = {"duration_ms": duration_ms} if duration_ms else None
            if subtype == "success" or not getattr(event, "is_error", False):
                return [factory.completed_ok(answer=answer, resume=resume, usage=usage)]
            else:
                return [
                    factory.completed_error(
                        error=answer,
                        answer=answer,
                        resume=resume,
                        usage=usage,
                    )
                ]

        case cursor_schema.Thinking(subtype=subtype, text=text):
            events: list[TakopiEvent] = []
            if subtype == "delta" and text:
                # Start tracking thinking on first delta
                if state.thinking_chunks is None:
                    state.thinking_chunks = []
                    state.thinking_action_id = f"thinking-{state.note_seq}"
                    state.note_seq += 1
                    events.append(
                        factory.action_started(
                            action_id=state.thinking_action_id,
                            kind="note",
                            title="thinking...",
                        )
                    )
                state.thinking_chunks.append(text)
            elif subtype == "completed":
                # Finalize thinking block
                if state.thinking_action_id:
                    full_text = "".join(state.thinking_chunks or []).strip()
                    # Show a summary in the action title
                    summary = (
                        full_text[:80] + "..." if len(full_text) > 80 else full_text
                    )
                    events.append(
                        factory.action_completed(
                            action_id=state.thinking_action_id,
                            kind="note",
                            title=f"thought: {summary}",
                            ok=True,
                        )
                    )
                    # Accumulate full thinking text (don't overwrite previous blocks)
                    if full_text:
                        if state.thinking_blocks is None:
                            state.thinking_blocks = []
                        state.thinking_blocks.append(full_text)
                    state.thinking_chunks = None
                    state.thinking_action_id = None
            return events

        case cursor_schema.UserMessage():
            # Echo of user message; ignore
            return []

    return []


@dataclass(slots=True)
class CursorRunState:
    factory: EventFactory
    note_seq: int = 0
    final_answer: str | None = None
    thinking_chunks: list[str] | None = None
    thinking_action_id: str | None = None
    thinking_blocks: list[str] | None = None  # accumulated thinking texts


class CursorRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    """Takopi runner for the Cursor Agent CLI.

    Wraps ``agent -p --output-format stream-json`` in ``script -qfc`` to
    provide the PTY that the CLI requires for output.
    """

    engine: EngineId = ENGINE
    resume_re = _RESUME_RE
    logger = logger

    def __init__(
        self,
        *,
        model: str | None = None,
        workspace: str | None = None,
        title: str = "Cursor",
    ) -> None:
        self.model = model
        self.workspace = workspace
        self.session_title = title

    def format_resume(self, token: ResumeToken) -> str:
        if token.engine != ENGINE:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`agent --resume {token.value}`"

    def command(self) -> str:
        # We use ``script`` to wrap the agent command in a PTY.
        return "script"

    def build_args(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> list[str]:
        run_options = get_run_options()

        agent_args = [
            "agent",
            "-p",
            "--force",
            "--approve-mcps",
            "--output-format",
            "stream-json",
        ]

        # Model selection: explicit config > run_options > default
        model = self.model
        if run_options and run_options.model:
            model = run_options.model
        if model:
            agent_args.extend(["--model", model])

        # Workspace
        if self.workspace:
            agent_args.extend(["--workspace", self.workspace])

        # Resume
        if resume:
            agent_args.extend(["--resume", resume.value])

        # Prompt as positional argument
        agent_args.append(prompt)

        # Wrap in ``script -qfc '...' /dev/null`` for PTY allocation.
        # -q = quiet (no Script started/done messages)
        # -f = flush output immediately
        # -c = command to execute
        agent_cmd = " ".join(shlex.quote(a) for a in agent_args)
        return ["-qfc", agent_cmd, "/dev/null"]

    def stdin_payload(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> bytes | None:
        # Cursor CLI takes the prompt as a positional arg, not stdin.
        return None

    def new_state(self, prompt: str, resume: ResumeToken | None) -> CursorRunState:
        return CursorRunState(factory=EventFactory(ENGINE))

    def start_run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: CursorRunState,
    ) -> None:
        pass

    def decode_jsonl(self, *, line: bytes) -> cursor_schema.CursorEvent | None:
        # PTY wrapper may inject ANSI escapes; silently skip non-JSON lines.
        stripped = line.strip()
        if not stripped or not stripped.startswith(b"{"):
            return None
        return cursor_schema.decode_event(stripped)

    def decode_error_events(
        self,
        *,
        raw: str,
        line: str,
        error: Exception,
        state: CursorRunState,
    ) -> list[TakopiEvent]:
        if isinstance(error, msgspec.DecodeError):
            # Cursor stream may include non-JSON lines from PTY; silently skip.
            self.get_logger().debug(
                "cursor.jsonl.skip",
                tag=self.tag(),
                error=str(error),
                line=line[:120],
            )
            return []
        return super().decode_error_events(raw=raw, line=line, error=error, state=state)

    def translate(
        self,
        data: cursor_schema.CursorEvent | None,
        *,
        state: CursorRunState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        if data is None:
            return []
        return translate_cursor_event(
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
        state: CursorRunState,
    ) -> list[TakopiEvent]:
        message = f"cursor agent failed (rc={rc})."
        resume_for_completed = found_session or resume
        return [
            self.note_event(message, state=state, ok=False),
            state.factory.completed_error(
                error=message,
                answer=state.final_answer or "",
                resume=resume_for_completed,
            ),
        ]

    def stream_end_events(
        self,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: CursorRunState,
    ) -> list[TakopiEvent]:
        if not found_session:
            message = "cursor agent finished but no session_id was captured"
            return [
                state.factory.completed_error(
                    error=message,
                    answer=state.final_answer or "",
                    resume=resume,
                )
            ]
        return [
            state.factory.completed_ok(
                answer=state.final_answer or "",
                resume=found_session,
            )
        ]

    def pipes_error_message(self) -> str:
        return "cursor agent failed to open subprocess pipes"


def build_runner(config: EngineConfig, config_path: Path) -> Runner:
    """Build a CursorRunner from Takopi config."""
    model = config.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigError(
            f"Invalid `cursor.model` in {config_path}; expected a string."
        )

    workspace = config.get("workspace")
    if workspace is not None and not isinstance(workspace, str):
        raise ConfigError(
            f"Invalid `cursor.workspace` in {config_path}; expected a string."
        )

    title = str(model) if model else "Cursor"

    return CursorRunner(model=model, workspace=workspace, title=title)


BACKEND = EngineBackend(
    id="cursor",
    build_runner=build_runner,
    cli_cmd="agent",
    install_cmd="curl https://cursor.com/install -fsS | bash",
)
