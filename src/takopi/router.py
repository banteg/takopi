from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from collections.abc import Iterable

from .model import EngineId, ResumeToken
from .runner import Runner


class RunnerUnavailableError(RuntimeError):
    def __init__(self, engine: EngineId, issue: str | None = None) -> None:
        message = f"engine {engine!r} is unavailable"
        if issue:
            message = f"{message}: {issue}"
        super().__init__(message)
        self.engine = engine
        self.issue = issue


type EngineStatus = Literal["ok", "missing_cli", "bad_config", "load_error"]


@dataclass(frozen=True, slots=True)
class RunnerEntry:
    engine: EngineId
    runner: Runner
    status: EngineStatus = "ok"
    issue: str | None = None

    @property
    def available(self) -> bool:
        # "bad_config" means we ignored user config and built the runner with defaults.
        # The engine is still runnable, but a warning should be surfaced to the user.
        return self.status in {"ok", "bad_config"}


class AutoRouter:
    def __init__(
        self, entries: Iterable[RunnerEntry], default_engine: EngineId
    ) -> None:
        self._entries = tuple(entries)
        if not self._entries:
            raise ValueError("AutoRouter requires at least one runner.")
        by_engine: dict[EngineId, RunnerEntry] = {}
        for entry in self._entries:
            if entry.engine in by_engine:
                raise ValueError(f"duplicate runner engine: {entry.engine}")
            by_engine[entry.engine] = entry
        if default_engine not in by_engine:
            raise ValueError(f"default engine {default_engine!r} is not configured")
        self._by_engine = by_engine
        self.default_engine = default_engine

    @property
    def entries(self) -> tuple[RunnerEntry, ...]:
        return self._entries

    @property
    def available_entries(self) -> tuple[RunnerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.available)

    @property
    def engine_ids(self) -> tuple[EngineId, ...]:
        return tuple(entry.engine for entry in self._entries)

    @property
    def default_entry(self) -> RunnerEntry:
        return self._by_engine[self.default_engine]

    def entry_for_engine(self, engine: EngineId | None) -> RunnerEntry:
        engine = self.default_engine if engine is None else engine
        entry = self._by_engine.get(engine)
        if entry is None:
            raise RunnerUnavailableError(engine, "engine not configured")
        return entry

    def entry_for(self, resume: ResumeToken | None) -> RunnerEntry:
        if resume is None:
            return self.entry_for_engine(None)
        return self.entry_for_engine(resume.engine)

    def runner_for(self, resume: ResumeToken | None) -> Runner:
        entry = self.entry_for(resume)
        if not entry.available:
            raise RunnerUnavailableError(entry.engine, entry.issue)
        return entry.runner

    def format_resume(self, token: ResumeToken) -> str:
        entry = self.entry_for(token)
        return entry.runner.format_resume(token)

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        if not text:
            return None
        for entry in self._entries:
            token = entry.runner.extract_resume(text)
            if token is not None:
                return token
        return None

    def resolve_resume(
        self, text: str | None, reply_text: str | None
    ) -> ResumeToken | None:
        token = self.extract_resume(text)
        if token is not None:
            return token
        if reply_text is None:
            return None
        return self.extract_resume(reply_text)

    def is_resume_line(self, line: str) -> bool:
        return any(entry.runner.is_resume_line(line) for entry in self._entries)
