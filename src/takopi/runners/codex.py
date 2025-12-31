from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from weakref import WeakValueDictionary

import anyio
from anyio.abc import ByteReceiveStream, Process
from anyio.streams.text import TextReceiveStream
from ..model import (
    Action,
    ActionKind,
    EngineId,
    ErrorEvent,
    LogEvent,
    LogLevel,
    ResumeToken,
    SessionStartedEvent,
    TakopiEvent,
)
from ..runner import Runner

logger = logging.getLogger(__name__)

ENGINE: EngineId = EngineId("codex")
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


def _session_started_event(token: ResumeToken, *, title: str) -> SessionStartedEvent:
    return {
        "type": "session.started",
        "engine": token.engine,
        "resume": token,
        "title": title,
    }


def _log_event(level: LogLevel, message: str) -> LogEvent:
    event: LogEvent = {
        "type": "log",
        "engine": ENGINE,
        "level": level,
        "message": message,
    }
    return event


def _error_event(message: str, *, detail: str | None = None) -> ErrorEvent:
    payload: ErrorEvent = {
        "type": "error",
        "engine": ENGINE,
        "message": message,
    }
    if detail:
        payload["detail"] = detail
    return payload


def _run_completed_event(token: ResumeToken, *, answer: str) -> TakopiEvent:
    payload = {
        "type": "run.completed",
        "engine": ENGINE,
        "resume": token,
        "answer": answer,
    }
    return cast(TakopiEvent, payload)


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
    if event_type == "action.started":
        payload = {"type": event_type, "engine": ENGINE, "action": action}
        return cast(TakopiEvent, payload)
    ok_value = True if ok is None else ok
    payload = {
        "type": "action.completed",
        "engine": ENGINE,
        "action": action,
        "ok": ok_value,
    }
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
            ok = True
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
                    ok=True,
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
                ok=True,
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
                ok=True,
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
                ok=True,
            )
        ]

    return []


def translate_codex_event(event: dict[str, Any], *, title: str) -> list[TakopiEvent]:
    etype = event.get("type")
    if etype == "thread.started":
        thread_id = event.get("thread_id")
        if thread_id:
            token = ResumeToken(engine=ENGINE, value=str(thread_id))
            return [_session_started_event(token, title=title)]
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
    """Ensure subprocesses receive SIGTERM, then SIGKILL after a 2s timeout."""
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


class CodexRunner(Runner):
    engine: EngineId = ENGINE

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

    async def run(self, prompt: str, resume: ResumeToken | None) -> AsyncIterator[TakopiEvent]:
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
        session_lock: anyio.Lock | None = None
        session_lock_acquired = False

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

                expected_session: ResumeToken | None = resume_token
                found_session: ResumeToken | None = None
                final_answer: str | None = None

                async with anyio.create_task_group() as tg:
                    tg.start_soon(_drain_stderr, proc_stderr, stderr_chunks)
                    await proc_stdin.send(prompt.encode())
                    await proc_stdin.aclose()

                    async for raw_line in _iter_text_lines(proc_stdout):
                        raw = raw_line.rstrip("\n")
                        logger.debug("[codex][jsonl] %s", raw)
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            evt = json.loads(line)
                        except json.JSONDecodeError:
                            yield _log_event("error", f"invalid json line: {line}")
                            continue

                        if evt.get("type") == "item.completed":
                            item = evt.get("item") or {}
                            item_type = item.get("type") or item.get("item_type")
                            if item_type == "assistant_message":
                                item_type = "agent_message"
                            if item_type == "agent_message" and isinstance(
                                item.get("text"), str
                            ):
                                if final_answer is None:
                                    final_answer = item["text"]
                                else:
                                    yield _log_event(
                                        "error",
                                        "codex emitted multiple agent messages; using the last one",
                                    )
                                    final_answer = item["text"]

                        for out_evt in translate_codex_event(evt, title=self.session_title):
                            if out_evt["type"] == "session.started":
                                session = out_evt["resume"]
                                if found_session is None:
                                    if session.engine != ENGINE:
                                        raise RuntimeError(
                                            f"codex emitted session token for engine {session.engine!r}"
                                        )
                                    if expected_session is not None and session != expected_session:
                                        message = (
                                            "codex emitted a different session id than expected"
                                        )
                                        yield _log_event("error", message)
                                        raise RuntimeError(message)
                                    if expected_session is None:
                                        session_lock = self._lock_for(session)
                                        await session_lock.acquire()
                                        session_lock_acquired = True
                                    found_session = session
                                    yield out_evt
                                continue
                            yield out_evt
                    rc = await proc.wait()

                logger.debug("[codex] process exit pid=%s rc=%s", proc.pid, rc)
                if rc != 0:
                    stderr_text = "".join(stderr_chunks)
                    message = f"codex exec failed (rc={rc})."
                    yield _error_event(message, detail=f"stderr tail:\n{stderr_text}")
                    raise RuntimeError(f"{message} stderr tail:\n{stderr_text}")

                if not found_session:
                    raise RuntimeError(
                        "codex exec finished but no session_id/thread_id was captured"
                    )

                logger.info("[codex] done run session=%s", found_session.value)
                yield _run_completed_event(found_session, answer=final_answer or "")
        finally:
            if session_lock is not None and session_lock_acquired:
                session_lock.release()
