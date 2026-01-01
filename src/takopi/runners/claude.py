from __future__ import annotations

import logging
import os
import re
import subprocess
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import anyio

from ..backends import EngineBackend, EngineConfig
from ..backends_helpers import which_issue
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
from ..runner import BaseRunner, Runner
from ..utils.paths import relativize_command, relativize_path
from ..utils.streams import drain_stderr, iter_jsonl
from ..utils.subprocess import manage_subprocess

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


def _coerce_comma_list(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value if item is not None]
        joined = ",".join(part for part in parts if part)
        return joined or None
    text = str(value)
    return text or None


def _tool_input_path(tool_input: dict[str, Any]) -> str | None:
    for key in ("file_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _tool_kind_and_title(
    name: str, tool_input: dict[str, Any]
) -> tuple[ActionKind, str]:
    if name in {"Bash", "Shell", "KillShell"}:
        command = tool_input.get("command")
        display = relativize_command(str(command or name))
        return "command", display
    if name in {"Edit", "Write", "NotebookEdit", "MultiEdit"}:
        path = _tool_input_path(tool_input)
        if path:
            return "file_change", relativize_path(str(path))
        return "file_change", str(name)
    if name == "Read":
        path = _tool_input_path(tool_input)
        if path:
            return "tool", f"read: `{relativize_path(str(path))}`"
        return "tool", "read"
    if name == "Glob":
        pattern = tool_input.get("pattern")
        if pattern:
            return "tool", f"glob: `{pattern}`"
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
    preview = normalized

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
            action = state.pending_actions.pop(tool_use_id, None)
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
            denial_title = "permission denied"
            if isinstance(tool_name, str) and tool_name:
                denial_title = f"permission denied: {tool_name}"
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
                        title=denial_title,
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


@dataclass
class ClaudeRunner(BaseRunner):
    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE

    claude_cmd: str = "claude"
    model: str | None = None
    allowed_tools: list[str] | None = None
    dangerously_skip_permissions: bool = False
    use_api_billing: bool = False
    session_title: str = "claude"

    def format_resume(self, token: ResumeToken) -> str:
        if token.engine != ENGINE:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`claude --resume {token.value}`"

    def _build_args(self, prompt: str, resume: ResumeToken | None) -> list[str]:
        args: list[str] = ["-p", "--output-format", "stream-json", "--verbose"]
        if resume is not None:
            args.extend(["--resume", resume.value])
        if self.model is not None:
            args.extend(["--model", str(self.model)])
        allowed_tools = _coerce_comma_list(self.allowed_tools)
        if allowed_tools is not None:
            args.extend(["--allowedTools", allowed_tools])
        if self.dangerously_skip_permissions is True:
            args.append("--dangerously-skip-permissions")
        args.append("--")
        args.append(prompt)
        return args

    async def run_impl(  # noqa: C901
        self,
        prompt: str,
        resume: ResumeToken | None,
    ) -> AsyncIterator[TakopiEvent]:
        logger.info(
            "[claude] start run resume=%r",
            resume.value if resume else None,
        )
        logger.debug("[claude] prompt: %s", prompt)
        args = [self.claude_cmd]
        args.extend(self._build_args(prompt, resume))

        did_emit_completed = False
        note_seq = 0
        state = ClaudeStreamState()
        expected_session = resume
        found_session: ResumeToken | None = None

        def next_note_id() -> str:
            nonlocal note_seq
            note_seq += 1
            return f"claude.note.{note_seq}"

        env: dict[str, str] | None = None
        if self.use_api_billing is not True:
            env = dict(os.environ)
            env.pop("ANTHROPIC_API_KEY", None)
        async with manage_subprocess(
            *args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
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
                tg.start_soon(
                    drain_stderr,
                    proc_stderr,
                    stderr_chunks,
                    logger,
                    "claude",
                )
                async for json_line in iter_jsonl(
                    proc_stdout, logger=logger, tag="claude"
                ):
                    if did_emit_completed:
                        continue
                    if json_line.data is None:
                        yield _note_completed(
                            next_note_id(),
                            "invalid JSON from claude; ignoring line",
                            ok=False,
                            detail={"line": json_line.raw},
                        )
                        continue
                    evt = json_line.data

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
                            if (
                                expected_session is not None
                                and session != expected_session
                            ):
                                raise RuntimeError(
                                    "claude emitted a different session id than expected"
                                )
                            found_session = session
                            yield out_evt
                            continue
                        yield out_evt
                        if isinstance(out_evt, CompletedEvent):
                            did_emit_completed = True
                            break
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
                resume_for_completed = found_session or resume
                yield CompletedEvent(
                    engine=ENGINE,
                    ok=False,
                    answer="",
                    resume=resume_for_completed,
                    error=message,
                )
                return

            if not found_session:
                message = "claude finished but no session_id was captured"
                resume_for_completed = resume
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


INSTALL_CMDS = ("npm install -g @anthropic-ai/claude-code",)

check_setup = which_issue("claude", INSTALL_CMDS)


def build_runner(config: EngineConfig, _config_path: Path) -> Runner:
    claude_cmd = "claude"

    model = config.get("model")
    allowed_tools = config.get("allowed_tools")
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


def startup_message(cwd: str) -> str:
    return f"claude is ready\npwd: {cwd}"


BACKEND = EngineBackend(
    id="claude",
    check_setup=check_setup,
    build_runner=build_runner,
    startup_message=startup_message,
    install_cmds=INSTALL_CMDS,
)
