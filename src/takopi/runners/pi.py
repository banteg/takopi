from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any
from uuid import uuid4

import msgspec

from ..backends import EngineBackend, EngineConfig
from ..config import ConfigError
from ..logging import get_logger
from ..model import (
    Action,
    ActionEvent,
    ActionKind,
    ActionLevel,
    ActionPhase,
    CompletedEvent,
    EngineId,
    ResumeToken,
    StartedEvent,
    TakopiEvent,
)
from ..runner import JsonlSubprocessRunner, ResumeTokenMixin, Runner
from ..schemas import pi as pi_schema
from ..utils.paths import get_run_base_dir, relativize_command, relativize_path

logger = get_logger(__name__)

ENGINE: EngineId = EngineId("pi")

_RESUME_RE = re.compile(r"(?im)^\s*`?pi\s+--session\s+(?P<token>.+?)`?\s*$")

_SESSION_ID_PREFIX_LEN = 8


@dataclass(slots=True)
class PiStreamState:
    resume: ResumeToken
    session_path: str | None = None
    allow_id_promotion: bool = False
    pending_actions: dict[str, Action] = field(default_factory=dict)
    last_assistant_text: str | None = None
    last_assistant_error: str | None = None
    last_usage: dict[str, Any] | None = None
    started: bool = False
    note_seq: int = 0


def _looks_like_session_path(token: str) -> bool:
    if not token:
        return False
    if token.endswith(".jsonl"):
        return True
    if "/" in token or "\\" in token:
        return True
    if token.startswith("~"):
        return True
    return False


def _short_session_id(session_id: str) -> str:
    if not session_id:
        return session_id
    if "-" in session_id:
        return session_id.split("-", 1)[0]
    if len(session_id) > _SESSION_ID_PREFIX_LEN:
        return session_id[:_SESSION_ID_PREFIX_LEN]
    return session_id


def _session_id_from_line(line: str) -> str | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    event_type = data.get("type")
    if event_type is not None and event_type != "session":
        return None
    session_id = data.get("id")
    if isinstance(session_id, str) and session_id:
        return _short_session_id(session_id)
    return None


def _session_id_from_path(path: Path) -> str | None:
    path = path.expanduser()
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                return _session_id_from_line(line)
    except OSError:
        return None
    return None


def _maybe_promote_session_id(state: PiStreamState) -> None:
    if not state.allow_id_promotion:
        return
    session_path = state.session_path
    if not session_path:
        return
    if state.resume.value != session_path:
        return
    session_id = _session_id_from_path(Path(session_path))
    if session_id:
        state.resume = ResumeToken(engine=ENGINE, value=session_id)


def _action_event(
    *,
    phase: ActionPhase,
    action: Action,
    ok: bool | None = None,
    message: str | None = None,
    level: ActionLevel | None = None,
) -> ActionEvent:
    return ActionEvent(
        engine=ENGINE,
        action=action,
        phase=phase,
        ok=ok,
        message=message,
        level=level,
    )


def _extract_text_blocks(content: Any) -> str | None:
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    if not parts:
        return None
    return "".join(parts).strip() or None


def _assistant_error(message: dict[str, Any]) -> str | None:
    stop_reason = message.get("stopReason")
    if stop_reason in {"error", "aborted"}:
        error = message.get("errorMessage")
        if isinstance(error, str) and error:
            return error
        return f"pi run {stop_reason}"
    return None


def _tool_kind_and_title(
    name: str,
    args: dict[str, Any],
) -> tuple[ActionKind, str]:
    tool = name.lower()
    if tool == "bash":
        command = args.get("command")
        return "command", relativize_command(str(command or "bash"))
    if tool in {"edit", "write"}:
        path = args.get("path")
        if path:
            return "file_change", relativize_path(str(path))
        return "file_change", tool
    if tool == "read":
        path = args.get("path")
        if path:
            return "tool", f"read: `{relativize_path(str(path))}`"
        return "tool", "read"
    if tool == "grep":
        pattern = args.get("pattern")
        return "tool", f"grep: {pattern}" if pattern else "grep"
    if tool == "find":
        pattern = args.get("pattern")
        return "tool", f"find: {pattern}" if pattern else "find"
    if tool == "ls":
        path = args.get("path")
        if path:
            return "tool", f"ls: `{relativize_path(str(path))}`"
        return "tool", "ls"
    return "tool", name


def _last_assistant_message(messages: Any) -> dict[str, Any] | None:
    if not isinstance(messages, list):
        return None
    for item in reversed(messages):
        if isinstance(item, dict) and item.get("role") == "assistant":
            return item
    return None


def translate_pi_event(
    event: pi_schema.PiEvent,
    *,
    title: str,
    meta: dict[str, Any] | None,
    state: PiStreamState,
) -> list[TakopiEvent]:
    out: list[TakopiEvent] = []
    _maybe_promote_session_id(state)
    if not state.started:
        out.append(
            StartedEvent(
                engine=ENGINE,
                resume=state.resume,
                title=title,
                meta=meta or None,
            )
        )
        state.started = True

    match event:
        case pi_schema.ToolExecutionStart(
            toolCallId=tool_id, toolName=tool_name, args=args
        ):
            if not isinstance(args, dict):
                args = {}
            if isinstance(tool_id, str) and tool_id:
                name = str(tool_name or "tool")
                kind, title_str = _tool_kind_and_title(name, args)
                detail: dict[str, Any] = {"tool_name": name, "args": args}
                if kind == "file_change":
                    path = args.get("path")
                    if path:
                        detail["changes"] = [{"path": str(path), "kind": "update"}]
                action = Action(id=tool_id, kind=kind, title=title_str, detail=detail)
                state.pending_actions[action.id] = action
                out.append(_action_event(phase="started", action=action))
            return out

        case pi_schema.ToolExecutionEnd(
            toolCallId=tool_id, toolName=tool_name, result=result, isError=is_error
        ):
            if isinstance(tool_id, str) and tool_id:
                action = state.pending_actions.pop(tool_id, None)
                name = str(tool_name or "tool")
                if action is None:
                    action = Action(id=tool_id, kind="tool", title=name, detail={})
                detail = dict(action.detail)
                detail["result"] = result
                detail["is_error"] = is_error
                out.append(
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
                )
            return out

        case pi_schema.MessageEnd(message=message):
            if isinstance(message, dict) and message.get("role") == "assistant":
                text = _extract_text_blocks(message.get("content"))
                if text:
                    state.last_assistant_text = text
                usage = message.get("usage")
                if isinstance(usage, dict):
                    state.last_usage = usage
                error = _assistant_error(message)
                if error:
                    state.last_assistant_error = error
            return out

        case pi_schema.AgentEnd(messages=messages):
            assistant = _last_assistant_message(messages)
            if assistant:
                text = _extract_text_blocks(assistant.get("content"))
                if text:
                    state.last_assistant_text = text
                usage = assistant.get("usage")
                if isinstance(usage, dict):
                    state.last_usage = usage
                error = _assistant_error(assistant)
                if error:
                    state.last_assistant_error = error

            ok = state.last_assistant_error is None
            error = state.last_assistant_error
            answer = state.last_assistant_text or ""

            out.append(
                CompletedEvent(
                    engine=ENGINE,
                    ok=ok,
                    answer=answer,
                    resume=state.resume,
                    error=error,
                    usage=state.last_usage,
                )
            )
            return out

        case _:
            return out


class PiRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE
    session_title: str = "pi"
    logger = logger

    def __init__(
        self,
        *,
        extra_args: list[str],
        model: str | None,
        provider: str | None,
    ) -> None:
        self.extra_args = extra_args
        self.model = model
        self.provider = provider

    def format_resume(self, token: ResumeToken) -> str:
        if token.engine != ENGINE:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`pi --session {self._quote_token(token.value)}`"

    def run(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        return super().run(prompt, self._normalize_resume_token(resume))

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        if not text:
            return None
        found: str | None = None
        for match in self.resume_re.finditer(text):
            token = match.group("token")
            if not token:
                continue
            token = token.strip()
            if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
                token = token[1:-1]
            found = token
        if not found:
            return None
        if _looks_like_session_path(found):
            session_id = _session_id_from_path(Path(found))
            if session_id:
                found = session_id
        return ResumeToken(engine=self.engine, value=found)

    def _normalize_resume_token(self, resume: ResumeToken | None) -> ResumeToken | None:
        if resume is None:
            return None
        if resume.engine != ENGINE:
            return resume
        if not _looks_like_session_path(resume.value):
            return resume
        session_id = _session_id_from_path(Path(resume.value))
        if session_id:
            return ResumeToken(engine=ENGINE, value=session_id)
        return resume

    def command(self) -> str:
        return "pi"

    def build_args(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: PiStreamState,
    ) -> list[str]:
        args: list[str] = [*self.extra_args, "--print", "--mode", "json"]
        if self.provider:
            args.extend(["--provider", self.provider])
        if self.model:
            args.extend(["--model", self.model])
        args.extend(["--session", state.resume.value])
        args.append(self._sanitize_prompt(prompt))
        return args

    def stdin_payload(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: PiStreamState,
    ) -> bytes | None:
        return None

    def env(self, *, state: PiStreamState) -> dict[str, str] | None:
        env = dict(os.environ)
        env.setdefault("NO_COLOR", "1")
        env.setdefault("CI", "1")
        return env

    def new_state(self, prompt: str, resume: ResumeToken | None) -> PiStreamState:
        if resume is None:
            session_path = self._new_session_path()
            token = ResumeToken(engine=ENGINE, value=session_path)
            return PiStreamState(
                resume=token,
                session_path=session_path,
                allow_id_promotion=True,
            )
        session_path = resume.value if _looks_like_session_path(resume.value) else None
        return PiStreamState(resume=resume, session_path=session_path)

    def translate(
        self,
        data: pi_schema.PiEvent,
        *,
        state: PiStreamState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        meta: dict[str, Any] = {"cwd": os.getcwd()}
        if self.model:
            meta["model"] = self.model
        if self.provider:
            meta["provider"] = self.provider
        return translate_pi_event(
            data,
            title=self.session_title,
            meta=meta or None,
            state=state,
        )

    def decode_jsonl(
        self,
        *,
        line: bytes,
    ) -> pi_schema.PiEvent:
        return pi_schema.decode_event(line)

    def decode_error_events(
        self,
        *,
        raw: str,
        line: str,
        error: Exception,
        state: PiStreamState,
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
        state: PiStreamState,
    ) -> list[TakopiEvent]:
        message = f"pi failed (rc={rc})."
        resume_for_completed = found_session or resume or state.resume
        return [
            self.note_event(message, state=state),
            CompletedEvent(
                engine=ENGINE,
                ok=False,
                answer=state.last_assistant_text or "",
                resume=resume_for_completed,
                error=message,
                usage=state.last_usage,
            ),
        ]

    def stream_end_events(
        self,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: PiStreamState,
    ) -> list[TakopiEvent]:
        resume_for_completed = found_session or resume or state.resume
        message = "pi finished without an agent_end event"
        return [
            CompletedEvent(
                engine=ENGINE,
                ok=False,
                answer=state.last_assistant_text or "",
                resume=resume_for_completed,
                error=message,
                usage=state.last_usage,
            )
        ]

    def _new_session_path(self) -> str:
        cwd = get_run_base_dir() or Path.cwd()
        session_dir = _default_session_dir(cwd)
        session_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        safe_timestamp = timestamp.replace(":", "-").replace(".", "-")
        token = uuid4().hex
        filename = f"{safe_timestamp}_{token}.jsonl"
        return str(session_dir / filename)

    def _sanitize_prompt(self, prompt: str) -> str:
        if prompt.startswith("-"):
            return f" {prompt}"
        return prompt

    def _quote_token(self, token: str) -> str:
        if not token:
            return token
        needs_quotes = any(ch.isspace() for ch in token)
        if not needs_quotes and '"' not in token:
            return token
        escaped = token.replace('"', '\\"')
        return f'"{escaped}"'


def _default_session_dir(cwd: PurePath) -> Path:
    agent_dir = os.environ.get("PI_CODING_AGENT_DIR")
    base = Path(agent_dir).expanduser() if agent_dir else Path.home() / ".pi" / "agent"
    safe_path = f"--{str(cwd).lstrip('/\\\\').replace('/', '-').replace('\\', '-').replace(':', '-')}--"
    return base / "sessions" / safe_path


def build_runner(config: EngineConfig, config_path: Path) -> Runner:
    extra_args_value = config.get("extra_args")
    if extra_args_value is None:
        extra_args = []
    elif isinstance(extra_args_value, list) and all(
        isinstance(x, str) for x in extra_args_value
    ):
        extra_args = list(extra_args_value)
    else:
        raise ConfigError(
            f"Invalid `pi.extra_args` in {config_path}; expected a list of strings."
        )

    model = config.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigError(f"Invalid `pi.model` in {config_path}; expected a string.")

    provider = config.get("provider")
    if provider is not None and not isinstance(provider, str):
        raise ConfigError(f"Invalid `pi.provider` in {config_path}; expected a string.")

    return PiRunner(
        extra_args=extra_args,
        model=model,
        provider=provider,
    )


BACKEND = EngineBackend(
    id="pi",
    build_runner=build_runner,
    cli_cmd="pi",
    install_cmd="npm install -g @mariozechner/pi-coding-agent",
)
