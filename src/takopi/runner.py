"""Runner protocol and shared runner definitions."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from typing import Protocol
from weakref import WeakValueDictionary

import anyio

from .model import EngineId, ResumeToken, StartedEvent, TakopiEvent


def compile_resume_pattern(engine: EngineId) -> re.Pattern[str]:
    name = re.escape(str(engine))
    return re.compile(rf"(?im)^\s*`?{name}\s+resume\s+(?P<token>[^`\s]+)`?\s*$")


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


class BaseRunner(SessionLockMixin, ResumeTokenMixin):
    engine: EngineId

    def ensure_resume_re(self) -> re.Pattern[str]:
        resume_re = getattr(self, "resume_re", None)
        if resume_re is None:
            resume_re = compile_resume_pattern(self.engine)
            self.resume_re = resume_re
        return resume_re

    def is_resume_line(self, line: str) -> bool:
        self.ensure_resume_re()
        return super().is_resume_line(line)

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        self.ensure_resume_re()
        return super().extract_resume(text)

    async def run(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        self.ensure_resume_re()
        async for evt in self.run_locked(prompt, resume):
            yield evt

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
