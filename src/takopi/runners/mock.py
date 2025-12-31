from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TypeAlias

import anyio

from ..model import EngineId, ResumeToken, RunResult, SessionStartedEvent, TakopiEvent
from ..runner import EventQueue, EventSink

ENGINE: EngineId = EngineId("mock")


@dataclass(frozen=True, slots=True)
class Emit:
    event: TakopiEvent
    at: float | None = None


@dataclass(frozen=True, slots=True)
class Advance:
    now: float


@dataclass(frozen=True, slots=True)
class Sleep:
    seconds: float


@dataclass(frozen=True, slots=True)
class Wait:
    event: anyio.Event


@dataclass(frozen=True, slots=True)
class Return:
    answer: str


@dataclass(frozen=True, slots=True)
class Raise:
    error: Exception


ScriptStep: TypeAlias = Emit | Advance | Sleep | Wait | Return | Raise


def _resume_token(engine: EngineId, value: str | None) -> ResumeToken:
    return ResumeToken(engine=engine, value=value or uuid.uuid4().hex)


def _resume_patterns(
    engine: EngineId,
) -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    name = re.escape(engine)
    line_re = re.compile(rf"^\s*`?{name}\s+resume\s+[^`\s]+`?\s*$", flags=re.IGNORECASE)
    cmd_re = re.compile(
        rf"^\s*`?(?P<cmd>{name}\s+resume\s+[^`\s]+)`?\s*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    parse_re = re.compile(rf"^{name}\s+resume\s+(?P<token>\S+)$", flags=re.IGNORECASE)
    return line_re, cmd_re, parse_re


class MockRunner:
    engine: EngineId

    def __init__(
        self,
        *,
        events: Iterable[TakopiEvent] | None = None,
        answer: str = "",
        engine: EngineId = ENGINE,
        resume_value: str | None = None,
        title: str | None = None,
    ) -> None:
        self.engine = engine
        self._events = list(events or [])
        self._answer = answer
        self._resume_value = resume_value
        self.title = title or str(engine).title()
        (
            self._resume_line_re,
            self._resume_cmd_re,
            self._resume_parse_re,
        ) = _resume_patterns(engine)

    def format_resume(self, token: ResumeToken) -> str:
        if token.engine != self.engine:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`{self.engine} resume {token.value}`"

    def is_resume_line(self, line: str) -> bool:
        return bool(self._resume_line_re.match(line))

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        if not text:
            return None
        found: str | None = None
        for match in self._resume_cmd_re.finditer(text):
            cmd = match.group("cmd").strip()
            token = self._parse_resume_command(cmd)
            if token:
                found = token
        if not found:
            return None
        return ResumeToken(engine=self.engine, value=found)

    def _parse_resume_command(self, cmd: str) -> str | None:
        if not cmd:
            return None
        m = self._resume_parse_re.match(cmd)
        if m:
            return m.group("token")
        return None

    async def run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        on_event: EventSink,
    ) -> RunResult:
        _ = prompt
        token_value = None
        if resume is not None:
            if resume.engine != self.engine:
                raise RuntimeError(
                    f"resume token is for engine {resume.engine!r}, not {self.engine!r}"
                )
            token_value = resume.value
        if token_value is None:
            token_value = self._resume_value
        token = _resume_token(self.engine, token_value)
        session_evt: SessionStartedEvent = {
            "type": "session.started",
            "engine": self.engine,
            "resume": token,
            "title": self.title,
        }
        dispatcher = EventQueue(on_event, label=str(self.engine))
        await dispatcher.start()
        try:
            dispatcher.emit(session_evt)

            for event in self._events:
                if event.get("type") == "action.completed" and "ok" not in event:
                    event = {**event, "ok": True}
                dispatcher.emit(event)
                await anyio.sleep(0)

            return RunResult(resume=token, answer=self._answer)
        finally:
            await dispatcher.close()


class ScriptRunner(MockRunner):
    def __init__(
        self,
        script: Iterable[ScriptStep],
        *,
        engine: EngineId = ENGINE,
        resume_value: str | None = None,
        emit_session_start: bool = True,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        advance: Callable[[float], None] | None = None,
        default_answer: str = "",
        title: str | None = None,
    ) -> None:
        super().__init__(
            events=[],
            answer=default_answer,
            engine=engine,
            resume_value=resume_value,
            title=title,
        )
        self.calls: list[tuple[str, ResumeToken | None]] = []
        self._script = list(script)
        self._emit_session_start = emit_session_start
        self._sleep = sleep
        self._advance = advance

    def _advance_to(self, now: float) -> None:
        if self._advance is None:
            raise RuntimeError("ScriptRunner advance callback is not configured.")
        self._advance(now)

    async def run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        on_event: EventSink,
    ) -> RunResult:
        self.calls.append((prompt, resume))
        _ = prompt
        token_value = None
        if resume is not None:
            if resume.engine != self.engine:
                raise RuntimeError(
                    f"resume token is for engine {resume.engine!r}, not {self.engine!r}"
                )
            token_value = resume.value
        if token_value is None:
            token_value = self._resume_value
        token = _resume_token(self.engine, token_value)
        session_evt: SessionStartedEvent = {
            "type": "session.started",
            "engine": self.engine,
            "resume": token,
            "title": self.title,
        }

        async def emit(event: TakopiEvent) -> None:
            if event.get("type") == "action.completed" and "ok" not in event:
                event = {**event, "ok": True}
            res = on_event(event)
            if res is not None:
                await res

        if self._emit_session_start:
            await emit(session_evt)
            await anyio.sleep(0)

        for step in self._script:
            if isinstance(step, Emit):
                if step.at is not None:
                    self._advance_to(step.at)
                await emit(step.event)
                await anyio.sleep(0)
                continue
            if isinstance(step, Advance):
                self._advance_to(step.now)
                continue
            if isinstance(step, Sleep):
                await self._sleep(step.seconds)
                continue
            if isinstance(step, Wait):
                await step.event.wait()
                continue
            if isinstance(step, Raise):
                raise step.error
            if isinstance(step, Return):
                return RunResult(resume=token, answer=step.answer)
            raise RuntimeError(f"Unhandled script step: {step!r}")

        return RunResult(resume=token, answer=self._answer)
