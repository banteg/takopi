from __future__ import annotations

import os
import re
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import msgspec

from ..backends import EngineBackend, EngineConfig
from ..events import EventFactory
from ..logging import get_logger
from ..model import Action, ActionKind, EngineId, InteractiveOption, InteractiveRequest, ResumeToken, TakopiEvent
from ..runner import JsonlSubprocessRunner, JsonlStreamState, ResumeTokenMixin, Runner
from .run_options import get_run_options
from ..schemas import claude as claude_schema
from .tool_actions import tool_input_path, tool_kind_and_title

logger = get_logger(__name__)

ENGINE: EngineId = "claude"
DEFAULT_ALLOWED_TOOLS = ["Bash", "Read", "Edit", "Write"]

_RESUME_RE = re.compile(
    r"(?im)^\s*`?claude\s+(?:--resume|-r)\s+(?P<token>[^`\s]+)`?\s*$"
)


@dataclass(slots=True)
class ClaudeStreamState:
    factory: EventFactory = field(default_factory=lambda: EventFactory(ENGINE))
    pending_actions: dict[str, Action] = field(default_factory=dict)
    last_assistant_text: str | None = None
    note_seq: int = 0
    stdin: Any = None


def _normalize_tool_result(content: Any) -> str:
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


def _coerce_comma_list(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value if item is not None]
        joined = ",".join(part for part in parts if part)
        return joined or None
    text = str(value)
    return text or None


def _tool_kind_and_title(
    name: str, tool_input: dict[str, Any]
) -> tuple[ActionKind, str]:
    return tool_kind_and_title(name, tool_input, path_keys=("file_path", "path"))


def _tool_action(
    content: claude_schema.StreamToolUseBlock,
    *,
    parent_tool_use_id: str | None,
) -> Action:
    tool_id = content.id
    tool_name = str(content.name or "tool")
    tool_input = content.input

    kind, title = _tool_kind_and_title(tool_name, tool_input)

    detail: dict[str, Any] = {
        "name": tool_name,
        "input": tool_input,
    }
    if parent_tool_use_id:
        detail["parent_tool_use_id"] = parent_tool_use_id

    if kind == "file_change":
        path = tool_input_path(tool_input, path_keys=("file_path", "path"))
        if path:
            detail["changes"] = [{"path": path, "kind": "update"}]

    return Action(id=tool_id, kind=kind, title=title, detail=detail)


def _tool_result_event(
    content: claude_schema.StreamToolResultBlock,
    *,
    action: Action,
    factory: EventFactory,
) -> TakopiEvent:
    is_error = content.is_error is True
    raw_result = content.content
    normalized = _normalize_tool_result(raw_result)
    preview = normalized

    detail = action.detail | {
        "tool_use_id": content.tool_use_id,
        "result_preview": preview,
        "result_len": len(normalized),
        "is_error": is_error,
    }
    return factory.action_completed(
        action_id=action.id,
        kind=action.kind,
        title=action.title,
        ok=not is_error,
        detail=detail,
    )


def _extract_error(event: claude_schema.StreamResultMessage) -> str | None:
    if event.is_error:
        if isinstance(event.result, str) and event.result:
            return event.result
        subtype = event.subtype
        if subtype:
            return f"claude run failed ({subtype})"
        return "claude run failed"
    return None


def _usage_payload(event: claude_schema.StreamResultMessage) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for key in (
        "total_cost_usd",
        "duration_ms",
        "duration_api_ms",
        "num_turns",
    ):
        value = getattr(event, key, None)
        if value is not None:
            usage[key] = value
    if event.usage is not None:
        usage["usage"] = event.usage
    return usage


def translate_claude_event(
    event: claude_schema.StreamJsonMessage,
    *,
    title: str,
    state: ClaudeStreamState,
    factory: EventFactory,
) -> list[TakopiEvent]:
    match event:
        case claude_schema.StreamSystemMessage(subtype=subtype):
            if subtype != "init":
                return []
            session_id = event.session_id
            if not session_id:
                return []
            meta: dict[str, Any] = {}
            for key in (
                "cwd",
                "tools",
                "permissionMode",
                "output_style",
                "apiKeySource",
                "mcp_servers",
            ):
                value = getattr(event, key, None)
                if value is not None:
                    meta[key] = value
            model = event.model
            token = ResumeToken(engine=ENGINE, value=session_id)
            event_title = str(model) if isinstance(model, str) and model else title
            return [factory.started(token, title=event_title, meta=meta or None)]
        case claude_schema.StreamAssistantMessage(
            message=message, parent_tool_use_id=parent_tool_use_id
        ):
            out: list[TakopiEvent] = []
            for content in message.content:
                match content:
                    case claude_schema.StreamToolUseBlock():
                        action = _tool_action(
                            content,
                            parent_tool_use_id=parent_tool_use_id,
                        )
                        state.pending_actions[action.id] = action
                        out.append(
                            factory.action_started(
                                action_id=action.id,
                                kind=action.kind,
                                title=action.title,
                                detail=action.detail,
                            )
                        )
                    case claude_schema.StreamThinkingBlock(
                        thinking=thinking, signature=signature
                    ):
                        if not thinking:
                            continue
                        state.note_seq += 1
                        action_id = f"claude.thinking.{state.note_seq}"
                        detail: dict[str, Any] = {}
                        if parent_tool_use_id:
                            detail["parent_tool_use_id"] = parent_tool_use_id
                        if signature:
                            detail["signature"] = signature
                        out.append(
                            factory.action_completed(
                                action_id=action_id,
                                kind="note",
                                title=thinking,
                                ok=True,
                                detail=detail,
                            )
                        )
                    case claude_schema.StreamTextBlock(text=text):
                        if text:
                            state.last_assistant_text = text
                    case _:
                        continue
            return out
        case claude_schema.StreamUserMessage(message=message):
            if not isinstance(message.content, list):
                return []
            out: list[TakopiEvent] = []
            for content in message.content:
                if not isinstance(content, claude_schema.StreamToolResultBlock):
                    continue
                tool_use_id = content.tool_use_id
                action = state.pending_actions.pop(tool_use_id, None)
                if action is None:
                    action = Action(
                        id=tool_use_id,
                        kind="tool",
                        title="tool result",
                        detail={},
                    )
                out.append(
                    _tool_result_event(
                        content,
                        action=action,
                        factory=factory,
                    )
                )
            return out
        case claude_schema.StreamResultMessage():
            ok = not event.is_error
            result_text = event.result or ""
            if ok and not result_text and state.last_assistant_text:
                result_text = state.last_assistant_text

            resume = ResumeToken(engine=ENGINE, value=event.session_id)
            error = None if ok else _extract_error(event)
            usage = _usage_payload(event)

            return [
                factory.completed(
                    ok=ok,
                    answer=result_text,
                    resume=resume,
                    error=error,
                    usage=usage or None,
                )
            ]
        case _:
            return []


@dataclass(slots=True)
class ClaudeRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE

    claude_cmd: str = "claude"
    model: str | None = None
    allowed_tools: list[str] | None = None
    dangerously_skip_permissions: bool = False
    use_api_billing: bool = False
    session_title: str = "claude"
    logger = logger

    def format_resume(self, token: ResumeToken) -> str:
        if token.engine != ENGINE:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`claude --resume {token.value}`"

    def keep_stdin_open(self) -> bool:
        return not self.dangerously_skip_permissions

    async def _after_send_payload(self, proc: Any, *, state: ClaudeStreamState) -> None:
        state.stdin = proc.stdin

    def _build_args(self, prompt: str, resume: ResumeToken | None) -> list[str]:
        run_options = get_run_options()
        args: list[str] = ["-p", "--output-format", "stream-json", "--verbose"]
        if resume is not None:
            args.extend(["--resume", resume.value])
        model = self.model
        if run_options is not None and run_options.model:
            model = run_options.model
        if model is not None:
            args.extend(["--model", str(model)])
        allowed_tools = _coerce_comma_list(self.allowed_tools)
        if allowed_tools is not None:
            args.extend(["--allowedTools", allowed_tools])
        if self.dangerously_skip_permissions is True:
            args.append("--dangerously-skip-permissions")
        args.append("--")
        args.append(prompt)
        return args

    def command(self) -> str:
        return self.claude_cmd

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
        # When using the control protocol (stdin stays open), send a newline
        # immediately so claude doesn't block waiting for stdin at startup.
        if self.keep_stdin_open():
            return b"\n"
        return None

    def env(self, *, state: Any) -> dict[str, str] | None:
        if self.use_api_billing is not True:
            env = dict(os.environ)
            env.pop("ANTHROPIC_API_KEY", None)
            return env
        return None

    def new_state(self, prompt: str, resume: ResumeToken | None) -> ClaudeStreamState:
        return ClaudeStreamState()

    def start_run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: ClaudeStreamState,
    ) -> None:
        pass

    def _to_interactive_request(
        self, ctrl_req: claude_schema.StreamControlRequest
    ) -> InteractiveRequest | None:
        req = ctrl_req.request
        if not isinstance(req, claude_schema.ControlCanUseToolRequest):
            return None
        tool_name = req.tool_name or "tool"
        description: str | None = None
        if req.input:
            cmd = req.input.get("command")
            if isinstance(cmd, str):
                description = cmd
            else:
                path = req.input.get("file_path") or req.input.get("path")
                if isinstance(path, str):
                    description = path
        return InteractiveRequest(
            request_id=ctrl_req.request_id,
            kind="permission",
            title=f"Allow {tool_name}?",
            description=description,
            options=[
                InteractiveOption(id="allow", label="Allow"),
                InteractiveOption(id="deny", label="Deny"),
            ],
        )

    async def _send_control_response(
        self,
        request_id: str,
        *,
        ok: bool,
        error: str | None = None,
        send_to_stdin: Callable[[bytes], Awaitable[None]],
    ) -> None:
        if ok:
            inner: claude_schema.ControlResponse = claude_schema.ControlSuccessResponse(
                request_id=request_id
            )
        else:
            inner = claude_schema.ControlErrorResponse(
                request_id=request_id, error=error or "denied"
            )
        response = claude_schema.StreamControlResponse(response=inner)
        encoded = msgspec.json.encode(response) + b"\n"
        await send_to_stdin(encoded)

    async def handle_interactive(
        self,
        request: InteractiveRequest,
        *,
        state: ClaudeStreamState,
        send_to_stdin: Callable[[bytes], Awaitable[None]],
    ) -> None:
        run_opts = get_run_options()
        handler = run_opts.interactive_handler if run_opts is not None else None
        if handler is None:
            await self._send_control_response(
                request.request_id,
                ok=False,
                error="denied",
                send_to_stdin=send_to_stdin,
            )
            return
        try:
            option_id = await handler(request)
        except Exception:  # noqa: BLE001
            option_id = "deny"
        if option_id == "allow":
            await self._send_control_response(
                request.request_id, ok=True, send_to_stdin=send_to_stdin
            )
        else:
            await self._send_control_response(
                request.request_id,
                ok=False,
                error="denied",
                send_to_stdin=send_to_stdin,
            )

    async def _process_control_request(
        self,
        ctrl_req: claude_schema.StreamControlRequest,
        *,
        state: ClaudeStreamState,
    ) -> None:
        assert state.stdin is not None

        async def send_to_stdin(data: bytes) -> None:
            await state.stdin.send(data)

        ireq = self._to_interactive_request(ctrl_req)
        if ireq is None:
            await self._send_control_response(
                ctrl_req.request_id, ok=True, send_to_stdin=send_to_stdin
            )
        else:
            await self.handle_interactive(ireq, state=state, send_to_stdin=send_to_stdin)

    async def _iter_jsonl_events(
        self,
        *,
        stdout: Any,
        stream: JsonlStreamState,
        state: ClaudeStreamState,
        resume: ResumeToken | None,
        logger: Any,
        pid: int,
    ) -> AsyncIterator[TakopiEvent]:
        async for raw_line in self.iter_json_lines(stdout):
            line = raw_line.strip()
            if line and state.stdin is not None:
                try:
                    decoded = self.decode_jsonl(line=line)
                except Exception:  # noqa: BLE001
                    decoded = None
                if isinstance(decoded, claude_schema.StreamControlRequest):
                    await self._process_control_request(decoded, state=state)
                    continue
            for evt in self._handle_jsonl_line(
                raw_line=raw_line,
                stream=stream,
                state=state,
                resume=resume,
                logger=logger,
                pid=pid,
            ):
                yield evt

    def decode_jsonl(
        self,
        *,
        line: bytes,
    ) -> claude_schema.StreamJsonMessage:
        return claude_schema.decode_stream_json_line(line)

    def decode_error_events(
        self,
        *,
        raw: str,
        line: str,
        error: Exception,
        state: ClaudeStreamState,
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
        state: ClaudeStreamState,
    ) -> list[TakopiEvent]:
        return []

    def translate(
        self,
        data: claude_schema.StreamJsonMessage,
        *,
        state: ClaudeStreamState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        return translate_claude_event(
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
        state: ClaudeStreamState,
    ) -> list[TakopiEvent]:
        message = f"claude failed (rc={rc})."
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
        state: ClaudeStreamState,
    ) -> list[TakopiEvent]:
        if not found_session:
            message = "claude finished but no session_id was captured"
            resume_for_completed = resume
            return [
                state.factory.completed_error(
                    error=message,
                    resume=resume_for_completed,
                )
            ]

        message = "claude finished without a result event"
        return [
            state.factory.completed_error(
                error=message,
                answer=state.last_assistant_text or "",
                resume=found_session,
            )
        ]


def build_runner(config: EngineConfig, _config_path: Path) -> Runner:
    claude_cmd = shutil.which("claude") or "claude"

    model = config.get("model")
    if "allowed_tools" in config:
        allowed_tools = config.get("allowed_tools")
    else:
        allowed_tools = DEFAULT_ALLOWED_TOOLS
    dangerously_skip_permissions = config.get("dangerously_skip_permissions") is True
    use_api_billing = config.get("use_api_billing") is True
    title = str(model) if model is not None else "claude"

    return ClaudeRunner(
        claude_cmd=claude_cmd,
        model=model,
        allowed_tools=allowed_tools,
        dangerously_skip_permissions=dangerously_skip_permissions,
        use_api_billing=use_api_billing,
        session_title=title,
    )


BACKEND = EngineBackend(
    id="claude",
    build_runner=build_runner,
    install_cmd="npm install -g @anthropic-ai/claude-code",
)
