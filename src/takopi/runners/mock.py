from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

import anyio

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

    def format_resume(self, token: ResumeToken) -> str:
        if token.engine != ENGINE:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`mock resume {token.value}`"

    def is_resume_line(self, line: str) -> bool:
        return bool(
            re.match(r"^\s*`?mock\s+resume\s+[^`\s]+`?\s*$", line, flags=re.IGNORECASE)
        )

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        if not text:
            return None
        found: str | None = None
        for match in re.finditer(
            r"^\s*`?(?P<cmd>mock\s+resume\s+[^`\s]+)`?\s*$",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        ):
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
        m = re.match(r"^mock\s+resume\s+(?P<token>\S+)$", cmd, flags=re.IGNORECASE)
        if m:
            return m.group("token")
        return None

    async def run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        on_event: EventSink | None = None,
    ) -> RunResult:
        _ = prompt
        token_value = None
        if resume is not None:
            if resume.engine != ENGINE:
                raise RuntimeError(
                    f"resume token is for engine {resume.engine!r}, not {ENGINE!r}"
                )
            token_value = resume.value
        token = _resume_token(token_value)
        session_evt: SessionStartedEvent = {
            "type": "session.started",
            "engine": ENGINE,
            "resume": {"engine": ENGINE, "value": token.value},
        }
        dispatcher = EventQueue(on_event, label="mock") if on_event else None
        if dispatcher is not None:
            await dispatcher.start()
        try:
            if dispatcher is not None:
                dispatcher.emit(session_evt)

            for event in self._events:
                if dispatcher is not None:
                    dispatcher.emit(event)
                await anyio.sleep(0)

            ok = bool(self._answer)
            return RunResult(resume=token, answer=self._answer, ok=ok)
        finally:
            if dispatcher is not None:
                with anyio.CancelScope(shield=True):
                    await dispatcher.close()
