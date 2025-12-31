from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, cast
from weakref import WeakValueDictionary

import anyio
from anyio.abc import ByteReceiveStream, Process
from anyio.streams.text import TextReceiveStream
from .base import (
    Action,
    ActionKind,
    EngineId,
    EventQueue,
    ErrorEvent,
    EventSink,
    LogEvent,
    ResumePayload,
    ResumeToken,
    RunResult,
    SessionStartedEvent,
    TakopiEvent,
)

logger = logging.getLogger(__name__)

ENGINE: EngineId = "codex"
STDERR_TAIL_LINES = 200

_ACTION_KIND_MAP: dict[str, ActionKind] = {
    "command_execution": "command",
    "mcp_tool_call": "tool",
    "web_search": "web_search",
    "file_change": "file_change",
    "reasoning": "note",
}

_RESUME_LINE = re.compile(
    r"^\s*`?(?P<cmd>codex\s+resume\s+[^`\s]+)`?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _resume_payload(token: ResumeToken) -> ResumePayload:
    return {"engine": token.engine, "value": token.value}


def _session_started_event(token: ResumeToken) -> SessionStartedEvent:
    return {
        "type": "session.started",
        "engine": token.engine,
        "resume": _resume_payload(token),
    }


def _log_event(level: str, message: str) -> LogEvent:
    return {
        "type": "log",
        "engine": ENGINE,
        "level": level,
        "message": message,
    }


def _error_event(message: str, *, fatal: bool = False) -> ErrorEvent:
    return {
        "type": "error",
        "engine": ENGINE,
        "message": message,
        "fatal": fatal,
    }


def _action_event(
    *,
    event_type: str,
    action_id: str,
    kind: ActionKind,
    title: str,
    detail: dict[str, Any] | None = None,
    ok: bool | None = None,
) -> TakopiEvent:
    action: Action = {
        "id": action_id,
        "kind": kind,
        "title": title,
        "detail": detail or {},
    }
    if ok is not None:
        action["ok"] = ok
    payload: dict[str, Any] = {"type": event_type, "engine": ENGINE, "action": action}
    return cast(TakopiEvent, payload)


def _short_tool_name(item: dict[str, Any]) -> str:
    name = ".".join(part for part in (item.get("server"), item.get("tool")) if part)
    return name or "tool"


def _format_change_summary(item: dict[str, Any]) -> str:
    changes = item.get("changes") or []
    paths = [c.get("path") for c in changes if c.get("path")]
    if not paths:
        total = len(changes)
        if total <= 0:
            return "files"
        return f"{total} files"
    return ", ".join(str(path) for path in paths)


def _translate_item_event(etype: str, item: dict[str, Any]) -> list[TakopiEvent]:
    item_type = item.get("type") or item.get("item_type")
    if item_type == "assistant_message":
        item_type = "agent_message"

    if not item_type:
        return []

    if item_type == "agent_message":
        return []

    action_id = item.get("id")
    if not isinstance(action_id, str) or not action_id:
        return [_log_event("error", "missing item id in codex event")]

    kind = _ACTION_KIND_MAP.get(item_type)
    if kind is None:
        if item_type == "error" and etype == "item.completed":
            message = str(item.get("message") or "codex item error")
            return [_error_event(message)]
        return []

    if kind == "command":
        title = str(item.get("command") or "")
        if etype == "item.started":
            return [
                _action_event(
                    event_type="action.started",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                )
            ]
        if etype == "item.completed":
            exit_code = item.get("exit_code")
            ok = None
            if isinstance(exit_code, int):
                ok = exit_code == 0
            detail = {"exit_code": exit_code} if exit_code is not None else {}
            return [
                _action_event(
                    event_type="action.completed",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                    ok=ok,
                )
            ]

    if kind == "tool":
        title = _short_tool_name(item)
        detail = {"server": item.get("server"), "tool": item.get("tool")}
        if etype == "item.started":
            return [
                _action_event(
                    event_type="action.started",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                )
            ]
        if etype == "item.completed":
            return [
                _action_event(
                    event_type="action.completed",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                )
            ]

    if kind == "web_search":
        if etype != "item.completed":
            return []
        title = str(item.get("query") or "")
        return [
            _action_event(
                event_type="action.completed",
                action_id=action_id,
                kind=kind,
                title=title,
            )
        ]

    if kind == "file_change":
        if etype != "item.completed":
            return []
        title = _format_change_summary(item)
        detail = {"changes": item.get("changes") or []}
        return [
            _action_event(
                event_type="action.completed",
                action_id=action_id,
                kind=kind,
                title=title,
                detail=detail,
            )
        ]

    if kind == "note":
        if etype != "item.completed":
            return []
        title = str(item.get("text") or "")
        return [
            _action_event(
                event_type="action.completed",
                action_id=action_id,
                kind=kind,
                title=title,
            )
        ]

    return []


def translate_codex_event(event: dict[str, Any]) -> list[TakopiEvent]:
    etype = event.get("type")
    if etype == "thread.started":
        thread_id = event.get("thread_id")
        if thread_id:
            token = ResumeToken(engine=ENGINE, value=str(thread_id))
            return [_session_started_event(token)]
        return [_log_event("error", "codex thread.started missing thread_id")]

    if etype == "error":
        message = str(event.get("message") or "codex stream error")
        return [_error_event(message)]

    if etype == "turn.failed":
        error = event.get("error") or {}
        message = str(error.get("message") or "codex turn failed")
        return [_error_event(message)]

    if etype in {"item.started", "item.updated", "item.completed"}:
        item = event.get("item") or {}
        return _translate_item_event(etype, item)

    return []


async def _iter_text_lines(stream: ByteReceiveStream):
    text_stream = TextReceiveStream(stream, errors="replace")
    buffer = ""
    while True:
        try:
            chunk = await text_stream.receive()
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
        async for line in _iter_text_lines(stderr):
            logger.debug("[codex][stderr] %s", line.rstrip())
            chunks.append(line)
    except Exception as e:
        logger.debug("[codex][stderr] drain error: %s", e)


async def _wait_for_process(proc: Process, timeout: float) -> bool:
    with anyio.move_on_after(timeout) as scope:
        await proc.wait()
    return scope.cancel_called


def _terminate_process(proc: Process) -> None:
    if proc.returncode is not None:
        return
    if os.name == "posix" and proc.pid is not None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            return
        except ProcessLookupError:
            return
        except Exception as e:
            logger.debug("[codex] failed to terminate process group: %s", e)
    try:
        proc.terminate()
    except ProcessLookupError:
        return


def _kill_process(proc: Process) -> None:
    if proc.returncode is not None:
        return
    if os.name == "posix" and proc.pid is not None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except Exception as e:
            logger.debug("[codex] failed to kill process group: %s", e)
    try:
        proc.kill()
    except ProcessLookupError:
        return


@asynccontextmanager
async def manage_subprocess(*args, **kwargs):
    if os.name == "posix":
        kwargs.setdefault("start_new_session", True)
    proc = await anyio.open_process(args, **kwargs)
    try:
        yield proc
    finally:
        if proc.returncode is None:
            with anyio.CancelScope(shield=True):
                _terminate_process(proc)
                timed_out = await _wait_for_process(proc, timeout=2.0)
                if timed_out:
                    _kill_process(proc)
                    await proc.wait()


class CodexRunner:
    engine: EngineId = ENGINE

    def __init__(
        self,
        *,
        codex_cmd: str,
        extra_args: list[str],
    ) -> None:
        self.codex_cmd = codex_cmd
        self.extra_args = extra_args
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
        return f"`codex resume {token.value}`"

    def is_resume_line(self, line: str) -> bool:
        return bool(_RESUME_LINE.match(line))

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        if not text:
            return None
        found: str | None = None
        for match in _RESUME_LINE.finditer(text):
            cmd = match.group("cmd").strip()
            token = self._parse_resume_command(cmd)
            if token:
                found = token
        if not found:
            return None
        return ResumeToken(engine=ENGINE, value=found)

    def _parse_resume_command(self, cmd: str) -> str | None:
        if not cmd:
            return None
        m = re.match(r"^codex\s+resume\s+(?P<token>\S+)$", cmd, flags=re.IGNORECASE)
        if m:
            return m.group("token")
        return None

    def _emit_event(self, dispatcher: EventQueue | None, event: TakopiEvent) -> None:
        if dispatcher is None:
            return
        dispatcher.emit(event)

    async def run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        on_event: EventSink | None = None,
    ) -> RunResult:
        resume_token = resume
        if resume_token is not None and resume_token.engine != ENGINE:
            raise RuntimeError(
                f"resume token is for engine {resume_token.engine!r}, not {ENGINE!r}"
            )
        if resume_token is None:
            return await self._run(prompt, resume_token, on_event)
        lock = self._lock_for(resume_token)
        async with lock:
            return await self._run(prompt, resume_token, on_event)

    async def _run(
        self,
        prompt: str,
        resume_token: ResumeToken | None,
        on_event: EventSink | None,
    ) -> RunResult:
        logger.info(
            "[codex] start run resume=%r", resume_token.value if resume_token else None
        )
        logger.debug("[codex] prompt: %s", prompt)
        args = [self.codex_cmd]
        args.extend(self.extra_args)
        args.extend(["exec", "--json"])

        if resume_token:
            args.extend(["resume", resume_token.value, "-"])
        else:
            args.append("-")

        dispatcher = (
            EventQueue(on_event, label="codex") if on_event is not None else None
        )
        if dispatcher is not None:
            await dispatcher.start()

        cancelled_exc_type = anyio.get_cancelled_exc_class()
        cancelled_exc: BaseException | None = None

        try:
            async with manage_subprocess(
                *args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ) as proc:
                if proc.stdin is None or proc.stdout is None or proc.stderr is None:
                    raise RuntimeError("codex exec failed to open subprocess pipes")
                proc_stdin = proc.stdin
                proc_stdout = proc.stdout
                proc_stderr = proc.stderr
                logger.debug("[codex] spawn pid=%s args=%r", proc.pid, args)

                stderr_chunks: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
                rc: int | None = None

                found_session: ResumeToken | None = resume_token
                saw_session_started = False
                last_agent_text: str | None = None
                saw_agent_message = False

                async with anyio.create_task_group() as tg:
                    tg.start_soon(_drain_stderr, proc_stderr, stderr_chunks)

                    try:
                        await proc_stdin.send(prompt.encode())
                        await proc_stdin.aclose()

                        if resume_token is not None:
                            saw_session_started = True
                            self._emit_event(
                                dispatcher, _session_started_event(resume_token)
                            )

                        async for raw_line in _iter_text_lines(proc_stdout):
                            raw = raw_line.rstrip("\n")
                            logger.debug("[codex][jsonl] %s", raw)
                            line = raw.strip()
                            if not line:
                                continue
                            try:
                                evt = json.loads(line)
                            except json.JSONDecodeError:
                                self._emit_event(
                                    dispatcher,
                                    _log_event("error", f"invalid json line: {line}"),
                                )
                                continue

                            if evt.get("type") == "item.completed":
                                item = evt.get("item") or {}
                                item_type = item.get("type") or item.get("item_type")
                                if item_type == "assistant_message":
                                    item_type = "agent_message"
                                if item_type == "agent_message" and isinstance(
                                    item.get("text"), str
                                ):
                                    last_agent_text = item["text"]
                                    saw_agent_message = True

                            for out_evt in translate_codex_event(evt):
                                if out_evt["type"] == "session.started":
                                    token = out_evt["resume"]
                                    session = ResumeToken(
                                        engine=token["engine"], value=token["value"]
                                    )
                                    if found_session is None:
                                        found_session = session
                                        saw_session_started = True
                                        self._emit_event(dispatcher, out_evt)
                                    elif session != found_session:
                                        self._emit_event(
                                            dispatcher,
                                            _log_event(
                                                "error",
                                                "codex emitted a different session id than expected",
                                            ),
                                        )
                                    continue
                                self._emit_event(dispatcher, out_evt)
                    except cancelled_exc_type as exc:
                        cancelled_exc = exc
                        tg.cancel_scope.cancel()
                    finally:
                        if cancelled_exc is None:
                            rc = await proc.wait()

                if cancelled_exc is not None:
                    raise cancelled_exc

                logger.debug("[codex] process exit pid=%s rc=%s", proc.pid, rc)
                if rc != 0:
                    stderr_text = "".join(stderr_chunks)
                    if saw_agent_message:
                        self._emit_event(
                            dispatcher,
                            _log_event(
                                "error",
                                f"codex exited rc={rc}. stderr tail:\n{stderr_text}",
                            ),
                        )
                    else:
                        raise RuntimeError(
                            f"codex exec failed (rc={rc}). stderr tail:\n{stderr_text}"
                        )

                if not saw_session_started or not found_session:
                    raise RuntimeError(
                        "codex exec finished but no session_id/thread_id was captured"
                    )

                ok = bool(saw_agent_message) and rc == 0
                logger.info("[codex] done run session=%s", found_session.value)
                return RunResult(
                    resume=found_session,
                    answer=last_agent_text or "",
                    ok=ok,
                )
        finally:
            if dispatcher is not None:
                await dispatcher.close()
