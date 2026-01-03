"""Runner protocol and shared runner definitions."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast
from weakref import WeakValueDictionary

import anyio

from .model import (
    Action,
    ActionEvent,
    CompletedEvent,
    EngineId,
    ResumeToken,
    StartedEvent,
    TakopiEvent,
)
from .utils.streams import drain_stderr, iter_bytes_lines
from .utils.subprocess import manage_subprocess


class ResumeTokenMixin:
    engine: EngineId
    resume_re: re.Pattern[str]

    def format_resume(self, token: ResumeToken) -> str:
        if token.engine != self.engine:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`{self.engine} resume {token.value}`"

    def is_resume_line(self, line: str) -> bool:
        return bool(self.resume_re.match(line))

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        if not text:
            return None
        found: str | None = None
        for match in self.resume_re.finditer(text):
            token = match.group("token")
            if token:
                found = token
        if not found:
            return None
        return ResumeToken(engine=self.engine, value=found)


class SessionLockMixin:
    engine: EngineId
    session_locks: WeakValueDictionary[str, anyio.Lock] | None = None

    def lock_for(self, token: ResumeToken) -> anyio.Lock:
        locks = self.session_locks
        if locks is None:
            locks = WeakValueDictionary()
            self.session_locks = locks
        key = f"{token.engine}:{token.value}"
        lock = locks.get(key)
        if lock is None:
            lock = anyio.Lock()
            locks[key] = lock
        return lock

    async def run_with_resume_lock(
        self,
        prompt: str,
        resume: ResumeToken | None,
        run_fn: Callable[[str, ResumeToken | None], AsyncIterator[TakopiEvent]],
    ) -> AsyncIterator[TakopiEvent]:
        resume_token = resume
        if resume_token is not None and resume_token.engine != self.engine:
            raise RuntimeError(
                f"resume token is for engine {resume_token.engine!r}, not {self.engine!r}"
            )
        if resume_token is None:
            async for evt in run_fn(prompt, resume_token):
                yield evt
            return
        lock = self.lock_for(resume_token)
        async with lock:
            async for evt in run_fn(prompt, resume_token):
                yield evt


class BaseRunner(SessionLockMixin):
    engine: EngineId

    def run(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        return self.run_locked(prompt, resume)

    async def run_locked(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        if resume is not None:
            async for evt in self.run_with_resume_lock(prompt, resume, self.run_impl):
                yield evt
            return

        lock: anyio.Lock | None = None
        acquired = False
        try:
            async for evt in self.run_impl(prompt, None):
                if lock is None and isinstance(evt, StartedEvent):
                    lock = self.lock_for(evt.resume)
                    await lock.acquire()
                    acquired = True
                yield evt
        finally:
            if acquired and lock is not None:
                lock.release()

    async def run_impl(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        if False:
            yield  # pragma: no cover
        raise NotImplementedError


@dataclass(slots=True)
class JsonlRunState:
    note_seq: int = 0


class JsonlSubprocessRunner(BaseRunner):
    def get_logger(self) -> logging.Logger:
        return getattr(self, "logger", logging.getLogger(__name__))

    def command(self) -> str:
        raise NotImplementedError

    def tag(self) -> str:
        return str(self.engine)

    def build_args(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> list[str]:
        raise NotImplementedError

    def stdin_payload(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> bytes | None:
        return prompt.encode()

    def env(self, *, state: Any) -> dict[str, str] | None:
        return None

    def new_state(self, prompt: str, resume: ResumeToken | None) -> Any:
        return JsonlRunState()

    def start_run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> None:
        return None

    def pipes_error_message(self) -> str:
        return f"{self.tag()} failed to open subprocess pipes"

    def next_note_id(self, state: Any) -> str:
        try:
            note_seq = state.note_seq
        except AttributeError as exc:
            raise RuntimeError(
                "state must define note_seq or override next_note_id"
            ) from exc
        state.note_seq = note_seq + 1
        return f"{self.tag()}.note.{state.note_seq}"

    def note_event(
        self,
        message: str,
        *,
        state: Any,
        ok: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> TakopiEvent:
        note_id = self.next_note_id(state)
        action = Action(
            id=note_id,
            kind="warning",
            title=message,
            detail=detail or {},
        )
        return ActionEvent(
            engine=self.engine,
            action=action,
            phase="completed",
            ok=ok,
            message=message,
            level="info" if ok else "warning",
        )

    def invalid_json_events(
        self,
        *,
        raw: str,
        line: str,
        state: Any,
    ) -> list[TakopiEvent]:
        message = f"invalid JSON from {self.tag()}; ignoring line"
        return [self.note_event(message, state=state, detail={"line": line})]

    def decode_jsonl(self, *, line: bytes) -> Any | None:
        text = line.decode("utf-8", errors="replace")
        try:
            return cast(dict[str, Any], json.loads(text))
        except json.JSONDecodeError:
            return None

    async def iter_json_lines(
        self,
        stream: Any,
        *,
        logger: logging.Logger,
        tag: str,
    ) -> AsyncIterator[bytes]:
        async for raw_line in iter_bytes_lines(stream):
            raw = raw_line.rstrip(b"\n")
            text = raw.decode("utf-8", errors="replace")
            logger.debug("[%s][jsonl] %s", tag, text)
            yield raw

    def decode_error_events(
        self,
        *,
        raw: str,
        line: str,
        error: Exception,
        state: Any,
    ) -> list[TakopiEvent]:
        message = f"invalid event from {self.tag()}; ignoring line"
        detail = {"line": line, "error": str(error)}
        return [self.note_event(message, state=state, detail=detail)]

    def translate_error_events(
        self,
        *,
        data: Any,
        error: Exception,
        state: Any,
    ) -> list[TakopiEvent]:
        message = f"{self.tag()} translation error; ignoring event"
        detail: dict[str, Any] = {"error": str(error)}
        if isinstance(data, dict):
            detail["type"] = data.get("type")
            item = data.get("item")
            if isinstance(item, dict):
                detail["item_type"] = item.get("type") or item.get("item_type")
        return [self.note_event(message, state=state, detail=detail)]

    def process_error_events(
        self,
        rc: int,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: Any,
    ) -> list[TakopiEvent]:
        message = f"{self.tag()} failed (rc={rc})."
        resume_for_completed = found_session or resume
        return [
            self.note_event(message, state=state),
            CompletedEvent(
                engine=self.engine,
                ok=False,
                answer="",
                resume=resume_for_completed,
                error=message,
            ),
        ]

    def stream_end_events(
        self,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: Any,
    ) -> list[TakopiEvent]:
        message = f"{self.tag()} finished without a result event"
        resume_for_completed = found_session or resume
        return [
            CompletedEvent(
                engine=self.engine,
                ok=False,
                answer="",
                resume=resume_for_completed,
                error=message,
            )
        ]

    def translate(
        self,
        data: Any,
        *,
        state: Any,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        raise NotImplementedError

    def handle_started_event(
        self,
        event: StartedEvent,
        *,
        expected_session: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> tuple[ResumeToken | None, bool]:
        if event.engine != self.engine:
            raise RuntimeError(
                f"{self.tag()} emitted session token for engine {event.engine!r}"
            )
        if expected_session is not None and event.resume != expected_session:
            message = (
                f"{self.tag()} emitted session id {event.resume.value} "
                f"but expected {expected_session.value}"
            )
            raise RuntimeError(message)
        if found_session is None:
            return event.resume, True
        if event.resume != found_session:
            message = (
                f"{self.tag()} emitted session id {event.resume.value} "
                f"but expected {found_session.value}"
            )
            raise RuntimeError(message)
        return found_session, False

    async def run_impl(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        state = self.new_state(prompt, resume)
        self.start_run(prompt, resume, state=state)

        tag = self.tag()
        logger = self.get_logger()
        cmd = [self.command(), *self.build_args(prompt, resume, state=state)]
        payload = self.stdin_payload(prompt, resume, state=state)
        env = self.env(state=state)

        async with manage_subprocess(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        ) as proc:
            if proc.stdout is None or proc.stderr is None:
                raise RuntimeError(self.pipes_error_message())
            if payload is not None and proc.stdin is None:
                raise RuntimeError(self.pipes_error_message())

            logger.debug("[%s] spawn pid=%s args=%r", tag, proc.pid, cmd)

            if payload is not None:
                assert proc.stdin is not None
                await proc.stdin.send(payload)
                await proc.stdin.aclose()
            elif proc.stdin is not None:
                await proc.stdin.aclose()

            rc: int | None = None
            expected_session: ResumeToken | None = resume
            found_session: ResumeToken | None = None
            did_emit_completed = False

            async with anyio.create_task_group() as tg:
                tg.start_soon(
                    drain_stderr,
                    proc.stderr,
                    logger,
                    tag,
                )
                async for raw_line in self.iter_json_lines(
                    proc.stdout, logger=logger, tag=tag
                ):
                    if did_emit_completed:
                        continue
                    line = raw_line.strip()
                    if not line:
                        continue
                    raw_text = raw_line.decode("utf-8", errors="replace")
                    line_text = line.decode("utf-8", errors="replace")
                    try:
                        decoded = self.decode_jsonl(line=line)
                    except Exception as exc:
                        events = self.decode_error_events(
                            raw=raw_text,
                            line=line_text,
                            error=exc,
                            state=state,
                        )
                    else:
                        if decoded is None:
                            events = self.invalid_json_events(
                                raw=raw_text,
                                line=line_text,
                                state=state,
                            )
                        else:
                            try:
                                events = self.translate(
                                    decoded,
                                    state=state,
                                    resume=resume,
                                    found_session=found_session,
                                )
                            except Exception as exc:
                                events = self.translate_error_events(
                                    data=decoded,
                                    error=exc,
                                    state=state,
                                )

                    for evt in events:
                        if isinstance(evt, StartedEvent):
                            found_session, emit = self.handle_started_event(
                                evt,
                                expected_session=expected_session,
                                found_session=found_session,
                            )
                            if not emit:
                                continue
                        if isinstance(evt, CompletedEvent):
                            did_emit_completed = True
                            yield evt
                            break
                        yield evt

                rc = await proc.wait()

            logger.debug("[%s] process exit pid=%s rc=%s", tag, proc.pid, rc)
            if did_emit_completed:
                return
            if rc is not None and rc != 0:
                events = self.process_error_events(
                    rc,
                    resume=resume,
                    found_session=found_session,
                    state=state,
                )
                for evt in events:
                    yield evt
                return

            events = self.stream_end_events(
                resume=resume,
                found_session=found_session,
                state=state,
            )
            for evt in events:
                yield evt


class Runner(Protocol):
    engine: str

    def is_resume_line(self, line: str) -> bool: ...

    def format_resume(self, token: ResumeToken) -> str: ...

    def extract_resume(self, text: str | None) -> ResumeToken | None: ...

    def run(
        self,
        prompt: str,
        resume: ResumeToken | None,
    ) -> AsyncIterator[TakopiEvent]: ...
