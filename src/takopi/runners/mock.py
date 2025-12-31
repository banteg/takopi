from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Iterable
from typing import cast

from .base import EngineId, EventSink, ResumeToken, SessionStartedEvent, TakopiEvent

logger = logging.getLogger(__name__)

ENGINE: EngineId = "mock"


def _resume_token(value: str | None) -> ResumeToken:
    return ResumeToken(engine=ENGINE, value=value or uuid.uuid4().hex)


async def _await_event(awaitable: Awaitable[None]) -> None:
    await awaitable


class MockRunner:
    engine: EngineId = ENGINE

    def __init__(
        self, *, events: Iterable[TakopiEvent] | None = None, answer: str = ""
    ) -> None:
        self._events = list(events or [])
        self._answer = answer

    def _emit_event(self, on_event: EventSink | None, event: TakopiEvent) -> None:
        if on_event is None:
            return
        try:
            res = on_event(event)
        except Exception as e:
            logger.info("[mock][on_event] callback error: %s", e)
            return
        if res is None:
            return
        awaitable = res
        task = asyncio.create_task(_await_event(awaitable))

        def _done(t: asyncio.Task[None]) -> None:
            try:
                t.result()
            except asyncio.CancelledError:
                return
            except Exception as e:  # pragma: no cover - defensive
                logger.info("[mock][on_event] callback error: %s", e)

        task.add_done_callback(_done)

    async def run(
        self,
        prompt: str,
        resume: str | None,
        on_event: EventSink | None = None,
    ) -> tuple[ResumeToken, str, bool]:
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
        self._emit_event(on_event, session_evt)

        for event in self._events:
            self._emit_event(on_event, event)
            await asyncio.sleep(0)

        saw_agent_message = bool(self._answer)
        return (token, self._answer, saw_agent_message)
