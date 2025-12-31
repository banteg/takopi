from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable

from .base import (
    EngineId,
    EventQueue,
    EventSink,
    ResumeToken,
    RunResult,
    SessionStartedEvent,
    TakopiEvent,
)

ENGINE: EngineId = "mock"


def _resume_token(value: str | None) -> ResumeToken:
    return ResumeToken(engine=ENGINE, value=value or uuid.uuid4().hex)


class MockRunner:
    engine: EngineId = ENGINE

    def __init__(
        self, *, events: Iterable[TakopiEvent] | None = None, answer: str = ""
    ) -> None:
        self._events = list(events or [])
        self._answer = answer

    async def run(
        self,
        prompt: str,
        resume: str | None,
        on_event: EventSink | None = None,
    ) -> RunResult:
        _ = prompt
        token_value = None
        if resume:
            token = resume.strip().strip("`")
            if ":" in token:
                engine, value = token.split(":", 1)
                if engine != ENGINE:
                    raise RuntimeError(
                        f"resume token is for engine {engine!r}, not {ENGINE!r}"
                    )
                token_value = value or None
            else:
                token_value = token
        token = _resume_token(token_value)
        session_evt: SessionStartedEvent = {
            "type": "session.started",
            "engine": ENGINE,
            "resume": {"engine": ENGINE, "value": token.value},
        }
        dispatcher = EventQueue(on_event, label="mock") if on_event else None
        try:
            if dispatcher is not None:
                dispatcher.emit(session_evt)

            for event in self._events:
                if dispatcher is not None:
                    dispatcher.emit(event)
                await asyncio.sleep(0)

            ok = bool(self._answer)
            return RunResult(resume=token, answer=self._answer, ok=ok)
        finally:
            if dispatcher is not None:
                await dispatcher.close()
