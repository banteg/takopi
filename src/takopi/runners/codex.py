from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import msgspec

from ..backends import EngineBackend, EngineConfig
from ..config import ConfigError
from ..events import EventFactory
from ..logging import get_logger
from ..model import ActionPhase, EngineId, ResumeToken, TakopiEvent
from ..runner import BaseRunner, JsonlSubprocessRunner, ResumeTokenMixin, Runner
from .run_options import get_run_options
from ..schemas import codex as codex_schema
from ..utils.paths import get_run_base_dir, relativize_command
from ..utils.streams import drain_stderr, iter_bytes_lines

logger = get_logger(__name__)

ENGINE: EngineId = "codex"

__all__ = [
    "ENGINE",
    "AppServerCodexRunner",
    "CodexRunner",
    "find_exec_only_flag",
    "translate_codex_event",
]

_RESUME_RE = re.compile(r"(?im)^\s*`?codex\s+resume\s+(?P<token>[^`\s]+)`?\s*$")
_RECONNECTING_RE = re.compile(
    r"^Reconnecting\.{3}\s*(?P<attempt>\d+)/(?P<max>\d+)\s*$",
    re.IGNORECASE,
)
_EXEC_ONLY_FLAGS = {
    "--skip-git-repo-check",
    "--json",
    "--output-schema",
    "--output-last-message",
    "--color",
    "-o",
}
_EXEC_ONLY_PREFIXES = (
    "--output-schema=",
    "--output-last-message=",
    "--color=",
)


def find_exec_only_flag(extra_args: list[str]) -> str | None:
    for arg in extra_args:
        if arg in _EXEC_ONLY_FLAGS:
            return arg
        for prefix in _EXEC_ONLY_PREFIXES:
            if arg.startswith(prefix):
                return arg
    return None


def _parse_reconnect_message(message: str) -> tuple[int, int] | None:
    match = _RECONNECTING_RE.match(message)
    if not match:
        return None
    try:
        attempt = int(match.group("attempt"))
        max_attempts = int(match.group("max"))
    except TypeError, ValueError:
        return None
    return (attempt, max_attempts)


def _short_tool_name(server: str | None, tool: str | None) -> str:
    name = ".".join(part for part in (server, tool) if part)
    return name or "tool"


def _summarize_tool_result(result: Any) -> dict[str, Any] | None:
    if isinstance(result, codex_schema.McpToolCallItemResult):
        summary: dict[str, Any] = {}
        content = result.content
        if isinstance(content, list):
            summary["content_blocks"] = len(content)
        elif content is not None:
            summary["content_blocks"] = 1
        summary["has_structured"] = result.structured_content is not None
        return summary or None

    if isinstance(result, dict):
        summary = {}
        content = result.get("content")
        if isinstance(content, list):
            summary["content_blocks"] = len(content)
        elif content is not None:
            summary["content_blocks"] = 1

        structured_key: str | None = None
        if "structured_content" in result:
            structured_key = "structured_content"
        elif "structured" in result:
            structured_key = "structured"

        if structured_key is not None:
            summary["has_structured"] = result.get(structured_key) is not None
        return summary or None

    return None


def _normalize_change_list(changes: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for change in changes:
        path: str | None = None
        kind: str | None = None
        if isinstance(change, codex_schema.FileUpdateChange):
            path = change.path
            kind = change.kind
        elif isinstance(change, dict):
            path = change.get("path")
            kind = change.get("kind")
        if not isinstance(path, str) or not path:
            continue
        entry = {"path": path}
        if isinstance(kind, str) and kind:
            entry["kind"] = kind
        normalized.append(entry)
    return normalized


def _format_change_summary(changes: list[Any]) -> str:
    paths: list[str] = []
    for change in changes:
        if isinstance(change, codex_schema.FileUpdateChange):
            if change.path:
                paths.append(change.path)
            continue
        if isinstance(change, dict):
            path = change.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
    if not paths:
        total = len(changes)
        if total <= 0:
            return "files"
        return f"{total} files"
    return ", ".join(str(path) for path in paths)


@dataclass(frozen=True, slots=True)
class _TodoSummary:
    done: int
    total: int
    next_text: str | None


def _summarize_todo_list(items: Any) -> _TodoSummary:
    if not isinstance(items, list):
        return _TodoSummary(done=0, total=0, next_text=None)

    done = 0
    total = 0
    next_text: str | None = None

    for raw_item in items:
        if isinstance(raw_item, codex_schema.TodoItem):
            total += 1
            if raw_item.completed:
                done += 1
                continue
            if next_text is None:
                next_text = raw_item.text
            continue
        if not isinstance(raw_item, dict):
            continue
        total += 1
        completed = raw_item.get("completed") is True
        if completed:
            done += 1
            continue
        if next_text is None:
            text = raw_item.get("text")
            next_text = str(text) if text is not None else None

    return _TodoSummary(done=done, total=total, next_text=next_text)


def _todo_title(summary: _TodoSummary) -> str:
    if summary.total <= 0:
        return "todo"
    if summary.next_text:
        return f"todo {summary.done}/{summary.total}: {summary.next_text}"
    return f"todo {summary.done}/{summary.total}: done"


@dataclass(frozen=True, slots=True)
class _AgentMessageSummary:
    text: str
    phase: str | None


def _select_final_answer(agent_messages: list[_AgentMessageSummary]) -> str | None:
    for message in reversed(agent_messages):
        if message.phase == "final_answer":
            return message.text
    for message in reversed(agent_messages):
        if message.phase in {None, ""}:
            return message.text
    return None


def _translate_item_event(
    phase: ActionPhase, item: codex_schema.ThreadItem, *, factory: EventFactory
) -> list[TakopiEvent]:
    match item:
        case codex_schema.AgentMessageItem(
            id=action_id,
            text=text,
            phase="commentary",
        ):
            detail = {"phase": "commentary"}
            if phase in {"started", "updated"}:
                return [
                    factory.action(
                        phase=phase,
                        action_id=action_id,
                        kind="note",
                        title=text,
                        detail=detail,
                    )
                ]
            if phase == "completed":
                return [
                    factory.action_completed(
                        action_id=action_id,
                        kind="note",
                        title=text,
                        detail=detail,
                        ok=True,
                    )
                ]
            return []
        case codex_schema.AgentMessageItem():
            return []
        case codex_schema.ErrorItem(id=action_id, message=message):
            if phase != "completed":
                return []
            return [
                factory.action_completed(
                    action_id=action_id,
                    kind="warning",
                    title=message,
                    detail={"message": message},
                    ok=False,
                    message=message,
                    level="warning",
                ),
            ]
        case codex_schema.CommandExecutionItem(
            id=action_id,
            command=command,
            exit_code=exit_code,
            status=status,
        ):
            title = relativize_command(command)
            if phase in {"started", "updated"}:
                return [
                    factory.action(
                        phase=phase,
                        action_id=action_id,
                        kind="command",
                        title=title,
                    )
                ]
            if phase == "completed":
                ok = status == "completed"
                if isinstance(exit_code, int):
                    ok = ok and exit_code == 0
                detail = {"exit_code": exit_code, "status": status}
                return [
                    factory.action_completed(
                        action_id=action_id,
                        kind="command",
                        title=title,
                        detail=detail,
                        ok=ok,
                    ),
                ]
        case codex_schema.McpToolCallItem(
            id=action_id,
            server=server,
            tool=tool,
            arguments=arguments,
            status=status,
            result=result,
            error=error,
        ):
            title = _short_tool_name(server, tool)
            detail: dict[str, Any] = {
                "server": server,
                "tool": tool,
                "status": status,
                "arguments": arguments,
            }

            if phase in {"started", "updated"}:
                return [
                    factory.action(
                        phase=phase,
                        action_id=action_id,
                        kind="tool",
                        title=title,
                        detail=detail,
                    )
                ]
            if phase == "completed":
                ok = status == "completed" and error is None
                if error is not None:
                    detail["error_message"] = str(error.message)
                result_summary = _summarize_tool_result(result)
                if result_summary is not None:
                    detail["result_summary"] = result_summary
                return [
                    factory.action_completed(
                        action_id=action_id,
                        kind="tool",
                        title=title,
                        detail=detail,
                        ok=ok,
                    ),
                ]
        case codex_schema.WebSearchItem(id=action_id, query=query):
            detail = {"query": query}
            if phase in {"started", "updated"}:
                return [
                    factory.action(
                        phase=phase,
                        action_id=action_id,
                        kind="web_search",
                        title=query,
                        detail=detail,
                    )
                ]
            if phase == "completed":
                return [
                    factory.action_completed(
                        action_id=action_id,
                        kind="web_search",
                        title=query,
                        detail=detail,
                        ok=True,
                    )
                ]
        case codex_schema.FileChangeItem(id=action_id, changes=changes, status=status):
            if phase != "completed":
                return []
            title = _format_change_summary(changes)
            normalized_changes = _normalize_change_list(changes)
            detail = {
                "changes": normalized_changes,
                "status": status,
                "error": None,
            }
            ok = status == "completed"
            return [
                factory.action_completed(
                    action_id=action_id,
                    kind="file_change",
                    title=title,
                    detail=detail,
                    ok=ok,
                )
            ]
        case codex_schema.TodoListItem(id=action_id, items=items):
            summary = _summarize_todo_list(items)
            title = _todo_title(summary)
            detail = {"done": summary.done, "total": summary.total}
            if phase in {"started", "updated"}:
                return [
                    factory.action(
                        phase=phase,
                        action_id=action_id,
                        kind="note",
                        title=title,
                        detail=detail,
                    )
                ]
            if phase == "completed":
                return [
                    factory.action_completed(
                        action_id=action_id,
                        kind="note",
                        title=title,
                        detail=detail,
                        ok=True,
                    )
                ]
        case codex_schema.ReasoningItem(id=action_id, text=text):
            if phase in {"started", "updated"}:
                return [
                    factory.action(
                        phase=phase,
                        action_id=action_id,
                        kind="note",
                        title=text,
                    )
                ]
            if phase == "completed":
                return [
                    factory.action_completed(
                        action_id=action_id,
                        kind="note",
                        title=text,
                        ok=True,
                    )
                ]
    return []


def translate_codex_event(
    event: codex_schema.ThreadEvent,
    *,
    title: str,
    factory: EventFactory,
) -> list[TakopiEvent]:
    match event:
        case codex_schema.ThreadStarted(thread_id=thread_id):
            token = ResumeToken(engine=ENGINE, value=thread_id)
            return [factory.started(token, title=title)]
        case codex_schema.ItemStarted(item=item):
            return _translate_item_event("started", item, factory=factory)
        case codex_schema.ItemUpdated(item=item):
            return _translate_item_event("updated", item, factory=factory)
        case codex_schema.ItemCompleted(item=item):
            return _translate_item_event("completed", item, factory=factory)
        case _:
            return []


@dataclass(slots=True)
class CodexRunState:
    factory: EventFactory
    note_seq: int = 0
    final_answer: str | None = None
    turn_agent_messages: list[_AgentMessageSummary] = field(default_factory=list)
    turn_index: int = 0


class CodexRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    engine: EngineId = ENGINE
    resume_re = _RESUME_RE
    logger = logger

    def __init__(
        self,
        *,
        codex_cmd: str,
        extra_args: list[str],
        title: str = "Codex",
    ) -> None:
        self.codex_cmd = codex_cmd
        self.extra_args = extra_args
        self.session_title = title

    def command(self) -> str:
        return self.codex_cmd

    def build_args(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> list[str]:
        run_options = get_run_options()
        args = [*self.extra_args]
        if run_options is not None:
            if run_options.model:
                args.extend(["--model", str(run_options.model)])
            if run_options.reasoning:
                args.extend(
                    [
                        "-c",
                        f"model_reasoning_effort={run_options.reasoning}",
                    ]
                )
        args.extend(
            [
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--color=never",
            ]
        )
        if resume:
            args.extend(["resume", resume.value, "-"])
        else:
            args.append("-")
        return args

    def new_state(self, prompt: str, resume: ResumeToken | None) -> CodexRunState:
        return CodexRunState(factory=EventFactory(ENGINE))

    def start_run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: CodexRunState,
    ) -> None:
        pass

    def decode_jsonl(self, *, line: bytes) -> codex_schema.ThreadEvent:
        return codex_schema.decode_event(line)

    def decode_error_events(
        self,
        *,
        raw: str,
        line: str,
        error: Exception,
        state: CodexRunState,
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

    def pipes_error_message(self) -> str:
        return "codex exec failed to open subprocess pipes"

    def translate(
        self,
        data: codex_schema.ThreadEvent,
        *,
        state: CodexRunState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        factory = state.factory
        match data:
            case codex_schema.StreamError(message=message):
                reconnect = _parse_reconnect_message(message)
                if reconnect is not None:
                    attempt, max_attempts = reconnect
                    phase: ActionPhase = "started" if attempt <= 1 else "updated"
                    return [
                        factory.action(
                            phase=phase,
                            action_id="codex.reconnect",
                            kind="note",
                            title=message,
                            detail={"attempt": attempt, "max": max_attempts},
                            level="info",
                        )
                    ]
                return [self.note_event(message, state=state, ok=False)]
            case codex_schema.TurnFailed(error=error):
                resume_for_completed = found_session or resume
                return [
                    factory.completed_error(
                        error=error.message,
                        answer=state.final_answer or "",
                        resume=resume_for_completed,
                    )
                ]
            case codex_schema.TurnStarted():
                action_id = f"turn_{state.turn_index}"
                state.turn_index += 1
                state.final_answer = None
                state.turn_agent_messages.clear()
                return [
                    factory.action_started(
                        action_id=action_id,
                        kind="turn",
                        title="turn started",
                    )
                ]
            case codex_schema.TurnCompleted(usage=usage):
                resume_for_completed = found_session or resume
                return [
                    factory.completed_ok(
                        answer=state.final_answer or "",
                        resume=resume_for_completed,
                        usage=msgspec.to_builtins(usage),
                    )
                ]
            case codex_schema.ItemCompleted(
                item=codex_schema.AgentMessageItem(text=text, phase=message_phase)
            ):
                state.turn_agent_messages.append(
                    _AgentMessageSummary(text=text, phase=message_phase)
                )
                selected = _select_final_answer(state.turn_agent_messages)
                if selected is not None:
                    state.final_answer = selected
                if len(state.turn_agent_messages) > 1:
                    logger.debug("codex.multiple_agent_messages")
            case _:
                pass

        return translate_codex_event(
            data,
            title=self.session_title,
            factory=factory,
        )

    def process_error_events(
        self,
        rc: int,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: CodexRunState,
    ) -> list[TakopiEvent]:
        message = f"codex exec failed (rc={rc})."
        resume_for_completed = found_session or resume
        return [
            self.note_event(
                message,
                state=state,
                ok=False,
            ),
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
        state: CodexRunState,
    ) -> list[TakopiEvent]:
        if not found_session:
            message = "codex exec finished but no session_id/thread_id was captured"
            resume_for_completed = resume
            return [
                state.factory.completed_error(
                    error=message,
                    answer=state.final_answer or "",
                    resume=resume_for_completed,
                )
            ]
        logger.info("codex.session.completed", resume=found_session.value)
        return [
            state.factory.completed_ok(
                answer=state.final_answer or "",
                resume=found_session,
            )
        ]


@dataclass(slots=True)
class _AppServerWaiter:
    event: anyio.Event = field(default_factory=anyio.Event)
    result: Any | None = None
    error: Exception | None = None


class _AppServerClient:
    def __init__(self, *, codex_cmd: str, extra_args: list[str]) -> None:
        self.codex_cmd = codex_cmd
        self.extra_args = extra_args
        self._proc: Any | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._start_lock = anyio.Lock()
        self._write_lock = anyio.Lock()
        self._state_lock = anyio.Lock()
        self._waiters: dict[str, _AppServerWaiter] = {}
        self._turn_senders: dict[str, Any] = {}
        self._pending_by_turn: dict[str, list[dict[str, Any]]] = {}
        self._loaded_threads: set[str] = set()

    async def start(self) -> None:
        async with self._start_lock:
            if self._proc is not None:
                return

            cmd = [
                self.codex_cmd,
                *self.extra_args,
                "app-server",
                "--listen",
                "stdio://",
            ]
            kwargs: dict[str, Any] = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "cwd": get_run_base_dir(),
            }
            if os.name == "posix":
                kwargs["start_new_session"] = True
            self._proc = await anyio.open_process(cmd, **kwargs)

            if (
                self._proc.stdin is None
                or self._proc.stdout is None
                or self._proc.stderr is None
            ):
                raise RuntimeError("codex app-server failed to open subprocess pipes")

            self._reader_task = asyncio.create_task(self._reader_loop())
            self._stderr_task = asyncio.create_task(
                drain_stderr(self._proc.stderr, logger, ENGINE)
            )

            result = await self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "takopi",
                        "title": "Takopi",
                        "version": "0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            if not isinstance(result, dict):
                raise RuntimeError("codex app-server initialize returned non-object")
            await self.notify("initialized", {})

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._write({"method": method, "params": params or {}})

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        if method != "initialize":
            await self.start()
        request_id = str(uuid.uuid4())
        waiter = _AppServerWaiter()
        async with self._state_lock:
            self._waiters[request_id] = waiter
        try:
            await self._write({"id": request_id, "method": method, "params": params})
        except BaseException:
            async with self._state_lock:
                self._waiters.pop(request_id, None)
            raise

        await waiter.event.wait()
        if waiter.error is not None:
            raise waiter.error
        return waiter.result

    async def thread_start(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self.request("thread/start", params)
        if not isinstance(result, dict):
            raise RuntimeError("thread/start returned non-object")
        thread = result.get("thread")
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
            raise RuntimeError("thread/start returned no thread id")
        async with self._state_lock:
            self._loaded_threads.add(thread["id"])
        return result

    async def ensure_thread_loaded(self, thread_id: str) -> None:
        async with self._state_lock:
            if thread_id in self._loaded_threads:
                return
        result = await self.request("thread/resume", {"threadId": thread_id})
        if not isinstance(result, dict):
            raise RuntimeError("thread/resume returned non-object")
        async with self._state_lock:
            self._loaded_threads.add(thread_id)

    async def turn_start(
        self, thread_id: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        payload = {"threadId": thread_id, **params}
        result = await self.request("turn/start", payload)
        if not isinstance(result, dict):
            raise RuntimeError("turn/start returned non-object")
        return result

    async def turn_steer(self, thread_id: str, turn_id: str, text: str) -> None:
        result = await self.request(
            "turn/steer",
            {
                "threadId": thread_id,
                "expectedTurnId": turn_id,
                "input": [{"type": "text", "text": text}],
            },
        )
        if not isinstance(result, dict) or result.get("turnId") != turn_id:
            raise RuntimeError("turn/steer returned unexpected turn id")

    async def turn_interrupt(self, thread_id: str, turn_id: str) -> bool:
        await self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
        return True

    async def subscribe_turn(self, turn_id: str) -> Any:
        send, receive = anyio.create_memory_object_stream[dict[str, Any]](1000)
        while True:
            async with self._state_lock:
                pending = self._pending_by_turn.pop(turn_id, [])
                if not pending:
                    self._turn_senders[turn_id] = send
                    return receive
            for message in pending:
                await send.send(message)

    async def unsubscribe_turn(self, turn_id: str) -> None:
        async with self._state_lock:
            sender = self._turn_senders.pop(turn_id, None)
        if sender is not None:
            await sender.aclose()

    async def _write(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("codex app-server is not running")
        data = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
        async with self._write_lock:
            await self._proc.stdin.send(data)

    async def _reader_loop(self) -> None:
        proc = self._proc
        assert proc is not None
        assert proc.stdout is not None
        failure: BaseException | None = None
        try:
            async for raw_line in iter_bytes_lines(proc.stdout):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"invalid JSON-RPC from codex app-server: {line!r}"
                    ) from exc
                if not isinstance(message, dict):
                    continue
                if "method" in message and "id" in message:
                    response = self._handle_server_request(message)
                    await self._write({"id": message["id"], "result": response})
                    continue
                if "method" in message:
                    await self._route_notification(message)
                    continue
                await self._route_response(message)
        except Exception as exc:  # noqa: BLE001
            failure = exc
        if failure is None:
            failure = RuntimeError("codex app-server closed stdout")
        async with self._state_lock:
            if self._proc is proc:
                self._proc = None
                self._loaded_threads.clear()
        await self._fail_all(failure)

    def _handle_server_request(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method")
        params = message.get("params")
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return {"decision": "accept"}
        if method == "item/permissions/requestApproval" and isinstance(params, dict):
            permissions = params.get("permissions")
            return {"scope": "turn", "permissions": permissions or {}}
        if method == "mcpServer/elicitation/request":
            return {"action": "decline", "content": None}
        return {}

    async def _route_response(self, message: dict[str, Any]) -> None:
        request_id = str(message.get("id"))
        async with self._state_lock:
            waiter = self._waiters.pop(request_id, None)
        if waiter is None:
            return
        if "error" in message:
            error = message.get("error")
            text = error.get("message") if isinstance(error, dict) else None
            waiter.error = RuntimeError(str(text or error or "codex app-server error"))
        else:
            waiter.result = message.get("result")
        waiter.event.set()

    async def _route_notification(self, message: dict[str, Any]) -> None:
        turn_id = _app_notification_turn_id(message)
        if turn_id is None:
            return
        async with self._state_lock:
            sender = self._turn_senders.get(turn_id)
            if sender is None:
                self._pending_by_turn.setdefault(turn_id, []).append(message)
                return
        try:
            await sender.send(message)
        except anyio.ClosedResourceError:
            return

    async def _fail_all(self, exc: BaseException) -> None:
        async with self._state_lock:
            waiters = list(self._waiters.values())
            self._waiters.clear()
            senders = list(self._turn_senders.values())
            self._turn_senders.clear()
            self._pending_by_turn.clear()
        for waiter in waiters:
            waiter.error = RuntimeError(f"codex app-server closed: {exc}")
            waiter.event.set()
        for sender in senders:
            await sender.aclose()


def _app_notification_turn_id(message: dict[str, Any]) -> str | None:
    params = message.get("params")
    if not isinstance(params, dict):
        return None
    turn_id = params.get("turnId")
    if isinstance(turn_id, str):
        return turn_id
    turn = params.get("turn")
    if isinstance(turn, dict) and isinstance(turn.get("id"), str):
        return turn["id"]
    return None


@dataclass(slots=True)
class _AppServerRunState:
    factory: EventFactory
    final_answer: str | None = None
    turn_agent_messages: list[_AgentMessageSummary] = field(default_factory=list)
    agent_message_phases: dict[str, str | None] = field(default_factory=dict)
    agent_message_text: dict[str, str] = field(default_factory=dict)
    started_commentary_messages: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _AppServerTurnControl:
    client: _AppServerClient
    thread_id: str
    turn_id: str

    async def steer(self, text: str) -> None:
        await self.client.turn_steer(self.thread_id, self.turn_id, text)

    async def interrupt(self) -> bool:
        return await self.client.turn_interrupt(self.thread_id, self.turn_id)


def _app_item_title(item: dict[str, Any]) -> str:
    item_type = item.get("type")
    if item_type == "commandExecution":
        return relativize_command(str(item.get("command") or "command"))
    if item_type == "mcpToolCall":
        return _short_tool_name(item.get("server"), item.get("tool"))
    if item_type == "fileChange":
        changes = item.get("changes")
        if isinstance(changes, list):
            paths = [
                str(change.get("path"))
                for change in changes
                if isinstance(change, dict) and change.get("path")
            ]
            return ", ".join(paths) if paths else f"{len(changes)} files"
        return "files"
    if item_type == "webSearch":
        return str(item.get("query") or "web search")
    if item_type == "plan":
        return str(item.get("text") or "plan")
    if item_type == "contextCompaction":
        return "compacting context"
    if item_type == "reasoning":
        summary = item.get("summary")
        if isinstance(summary, list) and summary:
            return str(summary[-1])
        return "reasoning"
    return str(item_type or "item")


def _translate_app_item_event(
    method: str,
    item: dict[str, Any],
    *,
    state: _AppServerRunState,
) -> list[TakopiEvent]:
    item_id = str(item.get("id") or "")
    item_type = item.get("type")
    if not item_id:
        return []

    phase: ActionPhase | None
    if method == "item/started":
        phase = "started"
    elif method == "item/completed":
        phase = "completed"
    else:
        phase = None
    if phase is None:
        return []

    factory = state.factory
    if item_type == "agentMessage":
        text = str(item.get("text") or "")
        message_phase = item.get("phase")
        message_phase = message_phase if isinstance(message_phase, str) else None
        state.agent_message_phases[item_id] = message_phase
        state.agent_message_text[item_id] = text
        if method == "item/completed":
            state.turn_agent_messages.append(
                _AgentMessageSummary(text=text, phase=message_phase)
            )
            selected = _select_final_answer(state.turn_agent_messages)
            if selected is not None:
                state.final_answer = selected
        if message_phase == "commentary":
            detail = {"phase": "commentary"}
            if phase == "started":
                if not text:
                    return []
                state.started_commentary_messages.add(item_id)
                return [
                    factory.action_started(
                        action_id=item_id,
                        kind="note",
                        title=text,
                        detail=detail,
                    )
                ]
            if not text:
                return []
            return [
                factory.action_completed(
                    action_id=item_id,
                    kind="note",
                    title=text,
                    detail=detail,
                    ok=True,
                )
            ]
        return []

    if item_type == "commandExecution":
        status = item.get("status")
        detail = {
            "exit_code": item.get("exitCode"),
            "status": status,
            "cwd": item.get("cwd"),
        }
        title = _app_item_title(item)
        if phase == "started":
            return [
                factory.action_started(
                    action_id=item_id, kind="command", title=title, detail=detail
                )
            ]
        ok = status == "completed"
        exit_code = item.get("exitCode")
        if isinstance(exit_code, int):
            ok = ok and exit_code == 0
        return [
            factory.action_completed(
                action_id=item_id,
                kind="command",
                title=title,
                detail=detail,
                ok=ok,
            )
        ]

    if item_type == "mcpToolCall":
        status = item.get("status")
        detail = {
            "server": item.get("server"),
            "tool": item.get("tool"),
            "status": status,
            "arguments": item.get("arguments"),
        }
        error = item.get("error")
        if isinstance(error, dict):
            detail["error_message"] = str(error.get("message") or error)
        title = _app_item_title(item)
        if phase == "started":
            return [
                factory.action_started(
                    action_id=item_id, kind="tool", title=title, detail=detail
                )
            ]
        return [
            factory.action_completed(
                action_id=item_id,
                kind="tool",
                title=title,
                detail=detail,
                ok=status == "completed" and error is None,
            )
        ]

    if item_type == "fileChange":
        if phase != "completed":
            return []
        changes = item.get("changes")
        normalized_changes = _normalize_change_list(
            changes if isinstance(changes, list) else []
        )
        status = item.get("status")
        return [
            factory.action_completed(
                action_id=item_id,
                kind="file_change",
                title=_app_item_title(item),
                detail={"changes": normalized_changes, "status": status, "error": None},
                ok=status == "completed",
            )
        ]

    if item_type == "webSearch":
        detail = {"query": item.get("query"), "action": item.get("action")}
        title = _app_item_title(item)
        if phase == "started":
            return [
                factory.action_started(
                    action_id=item_id, kind="web_search", title=title, detail=detail
                )
            ]
        return [
            factory.action_completed(
                action_id=item_id,
                kind="web_search",
                title=title,
                detail=detail,
                ok=True,
            )
        ]

    if item_type == "reasoning":
        return []

    if item_type in {"plan", "contextCompaction"}:
        title = _app_item_title(item)
        if phase == "started":
            return [factory.action_started(action_id=item_id, kind="note", title=title)]
        return [
            factory.action_completed(
                action_id=item_id,
                kind="note",
                title=title,
                ok=True,
            )
        ]

    return []


def _translate_app_notification(
    message: dict[str, Any],
    *,
    state: _AppServerRunState,
    resume: ResumeToken,
) -> list[TakopiEvent]:
    method = message.get("method")
    params = message.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        return []
    factory = state.factory

    if method == "turn/started":
        turn = params.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else params.get("turnId")
        return [
            factory.action_started(
                action_id=str(turn_id or "turn"),
                kind="turn",
                title="turn started",
            )
        ]

    if method == "item/agentMessage/delta":
        item_id = params.get("itemId")
        delta = params.get("delta")
        if not isinstance(item_id, str) or not isinstance(delta, str):
            return []
        text = state.agent_message_text.get(item_id, "") + delta
        state.agent_message_text[item_id] = text
        if state.agent_message_phases.get(item_id) == "commentary":
            if not text:
                return []
            if item_id not in state.started_commentary_messages:
                state.started_commentary_messages.add(item_id)
                return [
                    factory.action_started(
                        action_id=item_id,
                        kind="note",
                        title=text,
                        detail={"phase": "commentary"},
                    )
                ]
            return [
                factory.action_updated(
                    action_id=item_id,
                    kind="note",
                    title=text,
                    detail={"phase": "commentary"},
                )
            ]
        return []

    if method in {"item/started", "item/completed"}:
        item = params.get("item")
        if isinstance(item, dict):
            return _translate_app_item_event(method, item, state=state)
        return []

    if method == "turn/plan/updated":
        plan = params.get("plan")
        if not isinstance(plan, list):
            return []
        total = len(plan)
        done = sum(
            1
            for item in plan
            if isinstance(item, dict) and item.get("status") == "completed"
        )
        next_text = None
        for item in plan:
            if isinstance(item, dict) and item.get("status") != "completed":
                raw_step = item.get("step")
                next_text = str(raw_step) if raw_step is not None else None
                break
        summary = _TodoSummary(done=done, total=total, next_text=next_text)
        return [
            factory.action_updated(
                action_id="app.plan",
                kind="note",
                title=_todo_title(summary),
                detail={"done": done, "total": total},
            )
        ]

    if method == "turn/completed":
        turn = params.get("turn")
        status = turn.get("status") if isinstance(turn, dict) else None
        error_message = None
        if isinstance(turn, dict):
            error = turn.get("error")
            if isinstance(error, dict):
                error_message = str(error.get("message") or "")
        ok = status == "completed"
        if ok:
            return [
                factory.completed_ok(
                    answer=state.final_answer or "",
                    resume=resume,
                )
            ]
        return [
            factory.completed_error(
                error=error_message or str(status or "turn failed"),
                answer=state.final_answer or "",
                resume=resume,
            )
        ]

    return []


class AppServerCodexRunner(ResumeTokenMixin, BaseRunner):
    engine: EngineId = ENGINE
    resume_re = _RESUME_RE
    logger = logger

    def __init__(
        self,
        *,
        codex_cmd: str,
        extra_args: list[str],
        title: str = "Codex",
    ) -> None:
        self.codex_cmd = codex_cmd
        self.extra_args = extra_args
        self.session_title = title
        self._client = _AppServerClient(codex_cmd=codex_cmd, extra_args=extra_args)

    async def run_impl(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        client = self._client
        await client.start()

        run_options = get_run_options()
        if resume is not None:
            thread_id = resume.value
            await client.ensure_thread_loaded(thread_id)
        else:
            thread_params: dict[str, Any] = {"cwd": str(get_run_base_dir())}
            if run_options is not None and run_options.model:
                thread_params["model"] = str(run_options.model)
            thread_started = await client.thread_start(thread_params)
            thread = thread_started["thread"]
            thread_id = str(thread["id"])

        token = ResumeToken(engine=ENGINE, value=thread_id)
        turn_params: dict[str, Any] = {"input": [{"type": "text", "text": prompt}]}
        if run_options is not None:
            if run_options.model:
                turn_params["model"] = str(run_options.model)
            if run_options.reasoning:
                turn_params["effort"] = str(run_options.reasoning)

        turn_started = await client.turn_start(thread_id, turn_params)
        turn = turn_started.get("turn")
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
            raise RuntimeError("turn/start returned no turn id")
        turn_id = turn["id"]
        control = _AppServerTurnControl(
            client=client, thread_id=thread_id, turn_id=turn_id
        )
        yield EventFactory(ENGINE).started(
            token,
            title=self.session_title,
            meta={"turn_id": turn_id, "control": control},
        )

        state = _AppServerRunState(factory=EventFactory(ENGINE))
        receive = await client.subscribe_turn(turn_id)
        try:
            async with receive:
                async for message in receive:
                    events = _translate_app_notification(
                        message,
                        state=state,
                        resume=token,
                    )
                    done = False
                    for event in events:
                        yield event
                        if event.type == "completed":
                            done = True
                    if done:
                        return
            raise RuntimeError("codex app-server closed before turn completed")
        finally:
            await client.unsubscribe_turn(turn_id)


def build_runner(config: EngineConfig, config_path: Path) -> Runner:
    codex_cmd = "codex"

    mode_value = config.get("mode", "app_server")
    if mode_value not in {"app_server", "exec"}:
        raise ConfigError(
            f"Invalid `codex.mode` in {config_path}; expected 'app_server' or 'exec'."
        )

    extra_args_value = config.get("extra_args")
    if extra_args_value is None:
        extra_args = ["-c", "notify=[]"]
    elif isinstance(extra_args_value, list) and all(
        isinstance(item, str) for item in extra_args_value
    ):
        extra_args = list(extra_args_value)
    else:
        raise ConfigError(
            f"Invalid `codex.extra_args` in {config_path}; expected a list of strings."
        )

    exec_only_flag = find_exec_only_flag(extra_args)
    if exec_only_flag:
        raise ConfigError(
            f"Invalid `codex.extra_args` in {config_path}; exec-only flag "
            f"{exec_only_flag!r} is managed by Takopi."
        )

    title = "Codex"
    profile_value = config.get("profile")
    if profile_value:
        if not isinstance(profile_value, str):
            raise ConfigError(
                f"Invalid `codex.profile` in {config_path}; expected a string."
            )
        extra_args.extend(["--profile", profile_value])
        title = profile_value

    if mode_value == "exec":
        return CodexRunner(codex_cmd=codex_cmd, extra_args=extra_args, title=title)
    return AppServerCodexRunner(codex_cmd=codex_cmd, extra_args=extra_args, title=title)


BACKEND = EngineBackend(
    id="codex",
    build_runner=build_runner,
    install_cmd="npm install -g @openai/codex",
)
