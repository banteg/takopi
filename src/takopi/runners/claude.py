from __future__ import annotations

import json
import logging
import re
import subprocess
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from weakref import WeakValueDictionary

import anyio
from anyio.abc import ByteReceiveStream
from anyio.streams.text import TextReceiveStream

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
from ..runner import ResumeRunnerMixin, Runner
from . import codex

logger = logging.getLogger(__name__)

ENGINE: EngineId = EngineId("claude")
STDERR_TAIL_LINES = 200

_RESUME_RE = re.compile(
    r"(?im)^\s*`?claude\s+(?:--resume|-r)\s+(?P<token>[^`\s]+)`?\s*$"
)


@dataclass
class ClaudeStreamState:
    pending_actions: dict[str, Action] = field(default_factory=dict)
    last_assistant_text: str | None = None


class _IdleTimeout(Exception):
    pass


def _action_event(
    *,
    phase: str,
    action: Action,
    ok: bool | None = None,
    message: str | None = None,
    level: str | None = None,
) -> ActionEvent:
    return ActionEvent(
        engine=ENGINE,
        action=action,
        phase=phase,
        ok=ok,
        message=message,
        level=level,
    )


def _note_completed(
    action_id: str,
    message: str,
    *,
    ok: bool = False,
    detail: dict[str, Any] | None = None,
) -> ActionEvent:
    return _action_event(
        phase="completed",
        action=Action(
            id=action_id,
            kind="warning",
            title=message,
            detail=detail or {},
        ),
        ok=ok,
        message=message,
        level="warning" if not ok else "info",
    )


def _normalize_tool_result(content: Any) -> str:
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("text"), str):
                    parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return str(content)


def _truncate(text: str, limit: int = 200) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _tool_input_path(tool_input: dict[str, Any]) -> str | None:
    for key in ("file_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _tool_kind_and_title(name: str, tool_input: dict[str, Any]) -> tuple[ActionKind, str]:
    if name in {"Bash", "Shell", "KillShell"}:
        command = tool_input.get("command")
        return "command", str(command or name)
    if name in {"Edit", "Write", "NotebookEdit", "MultiEdit"}:
        path = _tool_input_path(tool_input)
        return "file_change", str(path or name)
    if name == "Read":
        path = _tool_input_path(tool_input)
        if path:
            return "tool", f"read: {path}"
        return "tool", "read"
    if name == "Glob":
        pattern = tool_input.get("pattern")
        if pattern:
            return "tool", f"glob: {pattern}"
        return "tool", "glob"
    if name == "Grep":
        pattern = tool_input.get("pattern")
        if pattern:
            return "tool", f"grep: {pattern}"
        return "tool", "grep"
    if name == "WebSearch":
        query = tool_input.get("query")
        return "web_search", str(query or "search")
    if name == "WebFetch":
        url = tool_input.get("url")
        return "web_search", str(url or "fetch")
    if name in {"TodoWrite", "TodoRead"}:
        return "note", "update todos" if name == "TodoWrite" else "read todos"
    if name == "AskUserQuestion":
        return "note", "ask user"
    if name in {"Task", "Agent"}:
        desc = tool_input.get("description") or tool_input.get("prompt")
        return "tool", str(desc or name)
    return "tool", name


def _tool_action(
    content: dict[str, Any],
    *,
    message_id: str | None,
    parent_tool_use_id: str | None,
) -> Action | None:
    tool_id = content.get("id")
    if not isinstance(tool_id, str) or not tool_id:
        return None
    tool_name = str(content.get("name") or "tool")
    tool_input = content.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    kind, title = _tool_kind_and_title(tool_name, tool_input)

    detail: dict[str, Any] = {
        "name": tool_name,
        "input": tool_input,
    }
    if message_id:
        detail["message_id"] = message_id
    if parent_tool_use_id:
        detail["parent_tool_use_id"] = parent_tool_use_id

    if kind == "file_change":
        path = _tool_input_path(tool_input)
        if path:
            detail["changes"] = [{"path": path, "kind": "update"}]

    return Action(id=tool_id, kind=kind, title=title, detail=detail)


def _tool_result_event(
    content: dict[str, Any],
    *,
    action: Action,
    message_id: str | None,
) -> ActionEvent:
    is_error = content.get("is_error") is True
    raw_result = content.get("content")
    normalized = _normalize_tool_result(raw_result)
    preview = _truncate(normalized.strip())

    detail = dict(action.detail)
    detail.update(
        {
            "tool_use_id": content.get("tool_use_id"),
            "result_preview": preview,
            "result_len": len(normalized),
            "is_error": is_error,
        }
    )
    if message_id:
        detail["message_id"] = message_id

    return _action_event(
        phase="completed",
        action=Action(
            id=action.id,
            kind=action.kind,
            title=action.title,
            detail=detail,
        ),
        ok=not is_error,
    )


def _extract_error(event: dict[str, Any]) -> str | None:
    error = event.get("error")
    if isinstance(error, str) and error:
        return error
    errors = event.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, dict):
                message = item.get("message") or item.get("error")
                if isinstance(message, str) and message:
                    return message
            elif isinstance(item, str) and item:
                return item
    if event.get("is_error"):
        return "claude run failed"
    return None


def _usage_payload(event: dict[str, Any]) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for key in (
        "total_cost_usd",
        "duration_ms",
        "duration_api_ms",
        "num_turns",
    ):
        value = event.get(key)
        if value is not None:
            usage[key] = value
    for key in ("usage", "modelUsage"):
        value = event.get(key)
        if value is not None:
            usage[key] = value
    return usage


def translate_claude_event(
    event: dict[str, Any],
    *,
    title: str,
    state: ClaudeStreamState,
) -> list[TakopiEvent]:
    etype = event.get("type")
    if etype == "system" and event.get("subtype") == "init":
        session_id = event.get("session_id")
        if not session_id:
            return []
        model = event.get("model")
        event_title = str(model) if model else title
        meta: dict[str, Any] = {}
        for key in ("cwd", "tools", "permissionMode", "output_style", "apiKeySource"):
            if key in event:
                meta[key] = event.get(key)
        if "mcp_servers" in event:
            meta["mcp_servers"] = event.get("mcp_servers")

        return [
            StartedEvent(
                engine=ENGINE,
                resume=ResumeToken(engine=ENGINE, value=str(session_id)),
                title=event_title,
                meta=meta or None,
            )
        ]

    if etype == "assistant":
        message = event.get("message")
        if not isinstance(message, dict):
            return []
        message_id = message.get("id")
        if not isinstance(message_id, str):
            message_id = None
        parent_tool_use_id = event.get("parent_tool_use_id")
        if not isinstance(parent_tool_use_id, str):
            parent_tool_use_id = None
        content_blocks = message.get("content")
        if not isinstance(content_blocks, list):
            return []
        out: list[TakopiEvent] = []
        for content in content_blocks:
            if not isinstance(content, dict):
                continue
            ctype = content.get("type")
            if ctype == "tool_use":
                action = _tool_action(
                    content,
                    message_id=message_id,
                    parent_tool_use_id=parent_tool_use_id,
                )
                if action is None:
                    continue
                state.pending_actions[action.id] = action
                out.append(_action_event(phase="started", action=action))
            elif ctype == "text":
                text = content.get("text")
                if isinstance(text, str) and text:
                    state.last_assistant_text = text
        return out

    if etype == "user":
        message = event.get("message")
        if not isinstance(message, dict):
            return []
        message_id = message.get("id")
        if not isinstance(message_id, str):
            message_id = None
        content_blocks = message.get("content")
        if not isinstance(content_blocks, list):
            return []
        out: list[TakopiEvent] = []
        for content in content_blocks:
            if not isinstance(content, dict):
                continue
            if content.get("type") != "tool_result":
                continue
            tool_use_id = content.get("tool_use_id")
            if not isinstance(tool_use_id, str) or not tool_use_id:
                continue
            action = state.pending_actions.get(tool_use_id)
            if action is None:
                action = Action(
                    id=tool_use_id,
                    kind="tool",
                    title="tool result",
                    detail={},
                )
            out.append(
                _tool_result_event(content, action=action, message_id=message_id)
            )
        return out

    if etype == "result":
        out: list[TakopiEvent] = []
        for idx, denial in enumerate(event.get("permission_denials") or []):
            if not isinstance(denial, dict):
                continue
            tool_name = denial.get("tool_name")
            title = "permission denied"
            if isinstance(tool_name, str) and tool_name:
                title = f"permission denied: {tool_name}"
            tool_use_id = denial.get("tool_use_id")
            action_id = (
                f"claude.permission.{tool_use_id}"
                if isinstance(tool_use_id, str) and tool_use_id
                else f"claude.permission.{idx}"
            )
            out.append(
                _action_event(
                    phase="completed",
                    action=Action(
                        id=action_id,
                        kind="warning",
                        title=title,
                        detail=denial,
                    ),
                    ok=False,
                    level="warning",
                )
            )

        ok = not event.get("is_error", False)
        result_text = event.get("result")
        if not isinstance(result_text, str):
            result_text = ""
        if ok and not result_text and state.last_assistant_text:
            result_text = state.last_assistant_text

        resume_value = event.get("session_id")
        resume = (
            ResumeToken(engine=ENGINE, value=str(resume_value))
            if resume_value
            else None
        )
        error = None if ok else _extract_error(event)
        usage = _usage_payload(event)

        out.append(
            CompletedEvent(
                engine=ENGINE,
                ok=ok,
                answer=result_text,
                resume=resume,
                error=error,
                usage=usage or None,
            )
        )
        return out

    return []


async def _iter_text_lines(
    stream: ByteReceiveStream,
    *,
    idle_timeout_s: float | None,
    idle_armed: Callable[[], bool],
):
    text_stream = TextReceiveStream(stream, errors="replace")
    buffer = ""
    while True:
        try:
            if idle_timeout_s is not None and idle_armed():
                with anyio.fail_after(idle_timeout_s):
                    chunk = await text_stream.receive()
            else:
                chunk = await text_stream.receive()
        except TimeoutError as exc:
            raise _IdleTimeout from exc
        except anyio.EndOfStream:
            if buffer:
                yield buffer
            return
        buffer += chunk
        while True:
            split_at = buffer.find("\n")
            if split_at < 0:
                break
            line = buffer[: split_at + 1]
            buffer = buffer[split_at + 1 :]
            yield line


async def _drain_stderr(stderr: ByteReceiveStream, chunks: deque[str]) -> None:
    try:
        async for line in codex._iter_text_lines(stderr):
            logger.debug("[claude][stderr] %s", line.rstrip())
            chunks.append(line)
    except Exception as e:
        logger.debug("[claude][stderr] drain error: %s", e)


@dataclass
class ClaudeRunner(ResumeRunnerMixin, Runner):
    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE

    claude_cmd: str = "claude"
    model: str | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    permission_mode: str | None = None
    output_style: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    tools: list[str] | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None
    include_partial_messages: bool = False
    dangerously_skip_permissions: bool = False
    allow_dangerously_skip_permissions: bool = False
    mcp_config: list[str] | None = None
    add_dirs: list[str] | None = None
    extra_args: list[str] = field(default_factory=list)
    idle_timeout_s: float | None = None
    session_title: str = "claude"

    def __post_init__(self) -> None:
        self._session_locks: WeakValueDictionary[str, anyio.Lock] = (
            WeakValueDictionary()
        )

    def _lock_for(self, token: ResumeToken) -> anyio.Lock:
        key = f"{token.engine}:{token.value}"
        lock = self._session_locks.get(key)
        if lock is None:
            lock = anyio.Lock()
            self._session_locks[key] = lock
        return lock

    def format_resume(self, token: ResumeToken) -> str:
        if token.engine != ENGINE:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`claude --resume {token.value}`"

    def _build_args(self, prompt: str, resume: ResumeToken | None) -> list[str]:
        args: list[str] = ["-p", "--output-format", "stream-json", "--verbose"]
        if resume is not None:
            args.extend(["--resume", resume.value])
        if self.model:
            args.extend(["--model", self.model])
        if self.system_prompt:
            args.extend(["--system-prompt", self.system_prompt])
        if self.append_system_prompt:
            args.extend(["--append-system-prompt", self.append_system_prompt])
        if self.permission_mode:
            args.extend(["--permission-mode", self.permission_mode])
        if self.output_style:
            args.extend(["--output-style", self.output_style])
        if self.allowed_tools:
            args.extend(["--allowedTools", ",".join(self.allowed_tools)])
        if self.disallowed_tools:
            args.extend(["--disallowedTools", ",".join(self.disallowed_tools)])
        if self.tools:
            args.extend(["--tools", ",".join(self.tools)])
        if self.max_turns is not None:
            args.extend(["--max-turns", str(self.max_turns)])
        if self.max_budget_usd is not None:
            args.extend(["--max-budget-usd", str(self.max_budget_usd)])
        if self.include_partial_messages:
            args.append("--include-partial-messages")
        if self.dangerously_skip_permissions:
            args.append("--dangerously-skip-permissions")
        if self.allow_dangerously_skip_permissions:
            args.append("--allow-dangerously-skip-permissions")
        if self.mcp_config:
            for cfg in self.mcp_config:
                args.extend(["--mcp-config", cfg])
        if self.add_dirs:
            for directory in self.add_dirs:
                args.extend(["--add-dir", directory])
        args.extend(self.extra_args)
        args.append("--")
        args.append(prompt)
        return args

    async def run(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        resume_token = resume
        if resume_token is not None and resume_token.engine != ENGINE:
            raise RuntimeError(
                f"resume token is for engine {resume_token.engine!r}, not {ENGINE!r}"
            )
        if resume_token is None:
            async for evt in self._run(prompt, resume_token):
                yield evt
            return
        lock = self._lock_for(resume_token)
        async with lock:
            async for evt in self._run(prompt, resume_token):
                yield evt

    async def _run(  # noqa: C901
        self,
        prompt: str,
        resume_token: ResumeToken | None,
    ) -> AsyncIterator[TakopiEvent]:
        logger.info(
            "[claude] start run resume=%r",
            resume_token.value if resume_token else None,
        )
        logger.debug("[claude] prompt: %s", prompt)
        args = [self.claude_cmd]
        args.extend(self._build_args(prompt, resume_token))

        session_lock: anyio.Lock | None = None
        session_lock_acquired = False
        did_emit_completed = False
        note_seq = 0
        state = ClaudeStreamState()
        expected_session = resume_token
        found_session: ResumeToken | None = None

        def next_note_id() -> str:
            nonlocal note_seq
            note_seq += 1
            return f"claude.note.{note_seq}"

        def idle_armed() -> bool:
            return found_session is not None

        try:
            async with codex.manage_subprocess(
                *args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ) as proc:
                if proc.stdout is None or proc.stderr is None:
                    raise RuntimeError("claude failed to open subprocess pipes")
                proc_stdout = proc.stdout
                proc_stderr = proc.stderr
                if proc.stdin is not None:
                    await proc.stdin.aclose()

                stderr_chunks: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
                rc: int | None = None

                async with anyio.create_task_group() as tg:
                    tg.start_soon(_drain_stderr, proc_stderr, stderr_chunks)
                    try:
                        async for raw_line in _iter_text_lines(
                            proc_stdout,
                            idle_timeout_s=self.idle_timeout_s,
                            idle_armed=idle_armed,
                        ):
                            raw = raw_line.rstrip("\n")
                            logger.debug("[claude][jsonl] %s", raw)
                            line = raw.strip()
                            if not line:
                                continue
                            if did_emit_completed:
                                continue
                            try:
                                evt = json.loads(line)
                            except json.JSONDecodeError:
                                logger.debug("[claude] invalid json line: %s", line)
                                yield _note_completed(
                                    next_note_id(),
                                    "invalid JSON from claude; ignoring line",
                                    ok=False,
                                    detail={"line": _truncate(line, 400)},
                                )
                                continue

                            for out_evt in translate_claude_event(
                                evt,
                                title=self.session_title,
                                state=state,
                            ):
                                if isinstance(out_evt, StartedEvent):
                                    session = out_evt.resume
                                    if session.engine != ENGINE:
                                        raise RuntimeError(
                                            "claude emitted session token for wrong engine"
                                        )
                                    if expected_session is not None and session != expected_session:
                                        raise RuntimeError(
                                            "claude emitted a different session id than expected"
                                        )
                                    if expected_session is None:
                                        session_lock = self._lock_for(session)
                                        await session_lock.acquire()
                                        session_lock_acquired = True
                                    found_session = session
                                    yield out_evt
                                    continue
                                yield out_evt
                                if isinstance(out_evt, CompletedEvent):
                                    did_emit_completed = True
                                    break
                    except _IdleTimeout:
                        message = "claude stream idle timeout"
                        yield _note_completed(next_note_id(), message, ok=False)
                        resume_for_completed = found_session or resume_token
                        yield CompletedEvent(
                            engine=ENGINE,
                            ok=False,
                            answer="",
                            resume=resume_for_completed,
                            error=message,
                        )
                        did_emit_completed = True
                        return
                    rc = await proc.wait()

                logger.debug("[claude] process exit pid=%s rc=%s", proc.pid, rc)
                if did_emit_completed:
                    return

                if rc != 0:
                    stderr_text = "".join(stderr_chunks)
                    message = f"claude failed (rc={rc})."
                    yield _note_completed(
                        next_note_id(),
                        message,
                        ok=False,
                        detail={"stderr_tail": stderr_text},
                    )
                    resume_for_completed = found_session or resume_token
                    yield CompletedEvent(
                        engine=ENGINE,
                        ok=False,
                        answer="",
                        resume=resume_for_completed,
                        error=message,
                    )
                    return

                if not found_session:
                    message = (
                        "claude finished but no session_id was captured"
                    )
                    resume_for_completed = resume_token
                    yield CompletedEvent(
                        engine=ENGINE,
                        ok=False,
                        answer="",
                        resume=resume_for_completed,
                        error=message,
                    )
                    return

                message = "claude finished without a result event"
                yield CompletedEvent(
                    engine=ENGINE,
                    ok=False,
                    answer=state.last_assistant_text or "",
                    resume=found_session,
                    error=message,
                )
        finally:
            if session_lock is not None and session_lock_acquired:
                session_lock.release()
