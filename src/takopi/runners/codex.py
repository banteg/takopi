from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import signal
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, cast
from weakref import WeakValueDictionary

from .base import (
    Action,
    ActionKind,
    EngineId,
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


class _EventDispatcher:
    def __init__(self, on_event: EventSink) -> None:
        self._on_event = on_event
        self._queue: asyncio.Queue[TakopiEvent | None] = asyncio.Queue()
        self._closed = False
        self._task = asyncio.create_task(self._drain())

    def emit(self, event: TakopiEvent) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except Exception as e:  # pragma: no cover - defensive
            logger.info("[codex][on_event] enqueue error: %s", e)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(None)
        try:
            await self._task
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            logger.info("[codex][on_event] drain error: %s", e)

    async def _drain(self) -> None:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            try:
                res = self._on_event(event)
            except Exception as e:
                logger.info("[codex][on_event] callback error: %s", e)
                continue
            if res is None:
                continue
            try:
                if inspect.isawaitable(res):
                    await res
                else:
                    logger.info(
                        "[codex][on_event] callback returned non-awaitable result"
                    )
            except asyncio.CancelledError:
                return
            except Exception as e:  # pragma: no cover - defensive
                logger.info("[codex][on_event] callback error: %s", e)


async def _drain_stderr(stderr: asyncio.StreamReader, chunks: deque[str]) -> None:
    try:
        while True:
            line = await stderr.readline()
            if not line:
                return
            decoded = line.decode(errors="replace")
            logger.debug("[codex][stderr] %s", decoded.rstrip())
            chunks.append(decoded)
    except Exception as e:
        logger.debug("[codex][stderr] drain error: %s", e)


def _terminate_process(proc: asyncio.subprocess.Process) -> None:
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


def _kill_process(proc: asyncio.subprocess.Process) -> None:
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
    proc = await asyncio.create_subprocess_exec(*args, **kwargs)
    try:
        yield proc
    finally:
        if proc.returncode is None:
            _terminate_process(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
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
        self._session_locks: WeakValueDictionary[str, asyncio.Lock] = (
            WeakValueDictionary()
        )

    def _lock_for(self, token: ResumeToken) -> asyncio.Lock:
        key = f"{token.engine}:{token.value}"
        lock = self._session_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[key] = lock
        return lock

    def _parse_resume(self, resume: str | None) -> ResumeToken | None:
        if not resume:
            return None
        token = resume.strip().strip("`")
        if ":" in token:
            engine, value = token.split(":", 1)
        else:
            engine, value = ENGINE, token
        if engine != ENGINE:
            raise RuntimeError(f"resume token is for engine {engine!r}, not {ENGINE!r}")
        if not value:
            raise RuntimeError("resume token is empty")
        return ResumeToken(engine=ENGINE, value=value)

    def _emit_event(
        self, dispatcher: _EventDispatcher | None, event: TakopiEvent
    ) -> None:
        if dispatcher is None:
            return
        dispatcher.emit(event)

    async def run(
        self,
        prompt: str,
        resume: str | None,
        on_event: EventSink | None = None,
    ) -> RunResult:
        resume_token = self._parse_resume(resume)
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

        dispatcher = _EventDispatcher(on_event) if on_event is not None else None

        async with manage_subprocess(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ) as proc:
            proc_stdin = cast(asyncio.StreamWriter, proc.stdin)
            proc_stdout = cast(asyncio.StreamReader, proc.stdout)
            proc_stderr = cast(asyncio.StreamReader, proc.stderr)
            logger.debug("[codex] spawn pid=%s args=%r", proc.pid, args)

            stderr_chunks: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
            stderr_task = asyncio.create_task(_drain_stderr(proc_stderr, stderr_chunks))

            found_session: ResumeToken | None = resume_token
            saw_session_started = False
            last_agent_text: str | None = None
            saw_agent_message = False

            cancelled = False
            rc: int | None = None

            try:
                proc_stdin.write(prompt.encode())
                await proc_stdin.drain()
                proc_stdin.close()

                if resume_token is not None:
                    saw_session_started = True
                    self._emit_event(dispatcher, _session_started_event(resume_token))

                async for raw_line in proc_stdout:
                    raw = raw_line.decode(errors="replace")
                    logger.debug("[codex][jsonl] %s", raw.rstrip("\n"))
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
            except asyncio.CancelledError:
                cancelled = True
            finally:
                if cancelled:
                    if not stderr_task.done():
                        stderr_task.cancel()
                    task = cast(asyncio.Task, asyncio.current_task())
                    while task.cancelling():
                        task.uncancel()
                if not cancelled:
                    rc = await proc.wait()
                await asyncio.gather(stderr_task, return_exceptions=True)

            if cancelled:
                if dispatcher is not None:
                    await dispatcher.close()
                raise asyncio.CancelledError

            try:
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
