from __future__ import annotations

import json
import logging
import subprocess
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast
from weakref import WeakValueDictionary

import anyio
from anyio.abc import ByteReceiveStream
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
from ..runner import ResumeRunnerMixin, Runner, compile_resume_pattern
from ..utils.streams import iter_text_lines
from ..utils.subprocess import manage_subprocess

logger = logging.getLogger(__name__)

ENGINE: EngineId = EngineId("codex")
STDERR_TAIL_LINES = 200

_ACTION_KIND_MAP: dict[str, ActionKind] = {
    "command_execution": "command",
    "mcp_tool_call": "tool",
    "tool_call": "tool",
    "web_search": "web_search",
    "file_change": "file_change",
    "reasoning": "note",
    "todo_list": "note",
}

_RESUME_RE = compile_resume_pattern(ENGINE)


def _started_event(token: ResumeToken, *, title: str) -> StartedEvent:
    return StartedEvent(engine=token.engine, resume=token, title=title)


def _completed_event(
    *,
    resume: ResumeToken | None,
    ok: bool,
    answer: str,
    error: str | None = None,
    usage: dict[str, Any] | None = None,
) -> TakopiEvent:
    return CompletedEvent(
        engine=ENGINE,
        ok=ok,
        answer=answer,
        resume=resume,
        error=error,
        usage=usage,
    )


def _action_event(
    *,
    phase: ActionPhase,
    action_id: str,
    kind: ActionKind,
    title: str,
    detail: dict[str, Any] | None = None,
    ok: bool | None = None,
    message: str | None = None,
    level: ActionLevel | None = None,
) -> TakopiEvent:
    action = Action(
        id=action_id,
        kind=kind,
        title=title,
        detail=detail or {},
    )
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
) -> TakopiEvent:
    return _action_event(
        phase="completed",
        action_id=action_id,
        kind="warning",
        title=message,
        detail=detail,
        ok=ok,
        message=message,
        level="warning" if not ok else "info",
    )


def _short_tool_name(item: dict[str, Any]) -> str:
    name = ".".join(part for part in (item.get("server"), item.get("tool")) if part)
    return name or "tool"


def _summarize_tool_result(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    summary: dict[str, Any] = {}
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


def _format_change_summary(item: dict[str, Any]) -> str:
    changes = item.get("changes") or []
    paths = [c.get("path") for c in changes if c.get("path")]
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
        logger.debug("[codex] missing item id in codex event: %r", item)
        return []

    phase = cast(ActionPhase, etype.split(".")[-1])

    if item_type == "error":
        if phase != "completed":
            return []
        message = str(item.get("message") or "codex item error")
        return [
            _action_event(
                phase="completed",
                action_id=action_id,
                kind="warning",
                title=message,
                detail={"message": message},
                ok=False,
                message=message,
                level="warning",
            )
        ]

    kind = _ACTION_KIND_MAP.get(item_type)
    if kind is None:
        return []

    if kind == "command":
        title = str(item.get("command") or "")
        if phase in {"started", "updated"}:
            return [
                _action_event(
                    phase=phase,
                    action_id=action_id,
                    kind=kind,
                    title=title,
                )
            ]
        if phase == "completed":
            exit_code = item.get("exit_code")
            ok = item.get("status") != "failed"
            if isinstance(exit_code, int):
                ok = ok and exit_code == 0
            detail = {
                "exit_code": exit_code,
                "status": item.get("status"),
            }
            return [
                _action_event(
                    phase="completed",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                    ok=ok,
                )
            ]

    if kind == "tool":
        tool_name = _short_tool_name(item)
        title = tool_name
        detail = {
            "server": item.get("server"),
            "tool": item.get("tool"),
            "status": item.get("status"),
        }
        if "arguments" in item:
            detail["arguments"] = item.get("arguments")
        if item_type == "tool_call":
            name = item.get("name")
            tool_name = str(name) if name else "tool"
            title = tool_name
            detail = {"name": name, "status": item.get("status")}
            if "arguments" in item:
                detail["arguments"] = item.get("arguments")

        if phase in {"started", "updated"}:
            return [
                _action_event(
                    phase=phase,
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                )
            ]
        if phase == "completed":
            ok = item.get("status") != "failed" and not item.get("error")
            error = item.get("error")
            if error:
                detail["error_message"] = str(
                    error.get("message") if isinstance(error, dict) else error
                )
            result_summary = _summarize_tool_result(item.get("result"))
            if result_summary is not None:
                detail["result_summary"] = result_summary
            return [
                _action_event(
                    phase="completed",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                    ok=ok,
                )
            ]

    if kind == "web_search":
        title = str(item.get("query") or "")
        detail = {"query": item.get("query")}
        if phase in {"started", "updated"}:
            return [
                _action_event(
                    phase=phase,
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                )
            ]
        if phase == "completed":
            return [
                _action_event(
                    phase="completed",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                    ok=True,
                )
            ]

    if kind == "file_change":
        if phase != "completed":
            return []
        title = _format_change_summary(item)
        detail = {
            "changes": item.get("changes") or [],
            "status": item.get("status"),
            "error": item.get("error"),
        }
        ok = item.get("status") != "failed"
        return [
            _action_event(
                phase="completed",
                action_id=action_id,
                kind=kind,
                title=title,
                detail=detail,
                ok=ok,
            )
        ]

    if kind == "note":
        if item_type == "todo_list":
            summary = _summarize_todo_list(item.get("items"))
            title = _todo_title(summary)
            detail = {"done": summary.done, "total": summary.total}
        else:
            title = str(item.get("text") or "")
            detail = None

        if phase in {"started", "updated"}:
            return [
                _action_event(
                    phase=phase,
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                )
            ]
        if phase == "completed":
            return [
                _action_event(
                    phase="completed",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
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
            return [_started_event(token, title=title)]
        logger.debug("[codex] codex thread.started missing thread_id: %r", event)
        return []

    if etype in {"item.started", "item.updated", "item.completed"}:
        item = event.get("item") or {}
        return _translate_item_event(etype, item)

    return []


async def _drain_stderr(stderr: ByteReceiveStream, chunks: deque[str]) -> None:
    try:
        async for line in iter_text_lines(stderr):
            logger.debug("[codex][stderr] %s", line.rstrip())
            chunks.append(line)
    except Exception as e:
        logger.debug("[codex][stderr] drain error: %s", e)


class CodexRunner(ResumeRunnerMixin, Runner):
    engine: EngineId = ENGINE
    resume_re = _RESUME_RE

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
                note_seq = 0
                did_emit_completed = False
                turn_index = 0

                def next_note_id() -> str:
                    nonlocal note_seq
                    note_seq += 1
                    return f"codex.note.{note_seq}"

                async with anyio.create_task_group() as tg:
                    tg.start_soon(_drain_stderr, proc_stderr, stderr_chunks)
                    await proc_stdin.send(prompt.encode())
                    await proc_stdin.aclose()

                    async for raw_line in iter_text_lines(proc_stdout):
                        raw = raw_line.rstrip("\n")
                        logger.debug("[codex][jsonl] %s", raw)
                        line = raw.strip()
                        if not line:
                            continue
                        if did_emit_completed:
                            continue
                        try:
                            evt = json.loads(line)
                        except json.JSONDecodeError:
                            logger.debug("[codex] invalid json line: %s", line)
                            note = _note_completed(
                                next_note_id(),
                                "invalid JSON from codex; ignoring line",
                                ok=False,
                                detail={"line": line},
                            )
                            yield note
                            continue

                        etype = evt.get("type")
                        if etype == "error":
                            message = str(evt.get("message") or "codex error")
                            fatal_flag = evt.get("fatal")
                            fatal = fatal_flag is True or fatal_flag is None
                            if fatal:
                                resume_for_completed = found_session or resume_token
                                yield _completed_event(
                                    resume=resume_for_completed,
                                    ok=False,
                                    answer=final_answer or "",
                                    error=message,
                                )
                                did_emit_completed = True
                                continue
                            note = _note_completed(
                                next_note_id(),
                                message,
                                ok=False,
                                detail={
                                    "code": evt.get("code"),
                                    "fatal": evt.get("fatal"),
                                },
                            )
                            yield note
                            continue
                        if etype == "turn.failed":
                            error = evt.get("error") or {}
                            message = str(error.get("message") or "codex turn failed")
                            resume_for_completed = found_session or resume_token
                            yield _completed_event(
                                resume=resume_for_completed,
                                ok=False,
                                answer=final_answer or "",
                                error=message,
                            )
                            did_emit_completed = True
                            continue
                        if etype == "turn.rate_limited":
                            retry_ms = evt.get("retry_after_ms")
                            message = "rate limited"
                            if isinstance(retry_ms, int):
                                message = f"rate limited (retry after {retry_ms}ms)"
                            note = _note_completed(next_note_id(), message, ok=False)
                            yield note
                            continue
                        if etype == "turn.started":
                            action_id = f"turn_{turn_index}"
                            turn_index += 1
                            yield _action_event(
                                phase="started",
                                action_id=action_id,
                                kind="turn",
                                title="turn started",
                            )
                            continue
                        if etype == "turn.completed":
                            resume_for_completed = found_session or resume_token
                            yield _completed_event(
                                resume=resume_for_completed,
                                ok=True,
                                answer=final_answer or "",
                                usage=evt.get("usage"),
                            )
                            did_emit_completed = True
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
                                    logger.debug(
                                        "[codex] emitted multiple agent messages; using the last one"
                                    )
                                    final_answer = item["text"]

                        for out_evt in translate_codex_event(
                            evt, title=self.session_title
                        ):
                            if isinstance(out_evt, StartedEvent):
                                session = out_evt.resume
                                if found_session is None:
                                    if session.engine != ENGINE:
                                        raise RuntimeError(
                                            f"codex emitted session token for engine {session.engine!r}"
                                        )
                                    if (
                                        expected_session is not None
                                        and session != expected_session
                                    ):
                                        message = "codex emitted a different session id than expected"
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
                if did_emit_completed:
                    return
                if rc != 0:
                    stderr_text = "".join(stderr_chunks)
                    message = f"codex exec failed (rc={rc})."
                    yield _note_completed(
                        next_note_id(),
                        message,
                        ok=False,
                        detail={"stderr_tail": stderr_text},
                    )
                    resume_for_completed = found_session or resume_token
                    yield _completed_event(
                        resume=resume_for_completed,
                        ok=False,
                        answer=final_answer or "",
                        error=message,
                    )
                    return

                if not found_session:
                    message = (
                        "codex exec finished but no session_id/thread_id was captured"
                    )
                    resume_for_completed = resume_token
                    yield _completed_event(
                        resume=resume_for_completed,
                        ok=False,
                        answer=final_answer or "",
                        error=message,
                    )
                    return

                logger.info("[codex] done run session=%s", found_session.value)
                yield _completed_event(
                    resume=found_session,
                    ok=True,
                    answer=final_answer or "",
                )
        finally:
            if session_lock is not None and session_lock_acquired:
                session_lock.release()
