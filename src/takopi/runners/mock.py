from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, replace

import anyio

from ..model import (
    ActionEvent,
    CompletedEvent,
    EngineId,
    ResumeToken,
    StartedEvent,
    TakopiEvent,
)
from ..runner import ResumeTokenMixin, Runner, SessionLockMixin

ENGINE: EngineId = "mock"


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


type ScriptStep = Emit | Advance | Sleep | Wait | Return | Raise


def _resume_token(engine: EngineId, value: str | None) -> ResumeToken:
    return ResumeToken(engine=engine, value=value or uuid.uuid4().hex)


class MockRunner(SessionLockMixin, ResumeTokenMixin, Runner):
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
        engine_name = re.escape(str(engine))
        self.resume_re = re.compile(
            rf"(?im)^\s*`?{engine_name}\s+resume\s+(?P<token>[^`\s]+)`?\s*$"
        )

    async def run(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
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
        session_evt = StartedEvent(
            engine=self.engine,
            resume=token,
            title=self.title,
        )
        lock = self.lock_for(token)
        async with lock:
            yield session_evt

            for event in self._events:
                event_out: TakopiEvent = event
                if (
                    isinstance(event_out, ActionEvent)
                    and event_out.phase == "completed"
                    and event_out.ok is None
                ):
                    event_out = replace(event_out, ok=True)
                yield event_out
                await anyio.sleep(0)

            yield CompletedEvent(
                engine=self.engine,
                resume=token,
                ok=True,
                answer=self._answer,
            )


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
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        self.calls.append((prompt, resume))
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
        session_evt = StartedEvent(
            engine=self.engine,
            resume=token,
            title=self.title,
        )
        lock = self.lock_for(token)

        async with lock:
            if self._emit_session_start:
                yield session_evt
                await anyio.sleep(0)

            for step in self._script:
                if isinstance(step, Emit):
                    if step.at is not None:
                        self._advance_to(step.at)
                    event_out: TakopiEvent = step.event
                    if (
                        isinstance(event_out, ActionEvent)
                        and event_out.phase == "completed"
                        and event_out.ok is None
                    ):
                        event_out = replace(event_out, ok=True)
                    yield event_out
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
                    yield CompletedEvent(
                        engine=self.engine,
                        resume=token,
                        ok=True,
                        answer=step.answer,
                    )
                    return
                raise RuntimeError(f"Unhandled script step: {step!r}")

            yield CompletedEvent(
                engine=self.engine,
                resume=token,
                ok=True,
                answer=self._answer,
            )
