from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import msgspec

from ..context import RunContext
from ..logging import get_logger
from ..model import ResumeToken
from .state_store import JsonStateStore

logger = get_logger(__name__)

STATE_VERSION = 1
STATE_FILENAME = "telegram_topics_state.json"


@dataclass(frozen=True, slots=True)
class TopicThreadSnapshot:
    chat_id: int
    thread_id: int
    context: RunContext | None
    sessions: dict[str, str]
    topic_title: str | None
    default_engine: str | None


class _ContextState(msgspec.Struct, forbid_unknown_fields=False):
    project: str | None = None
    branch: str | None = None


class _SessionState(msgspec.Struct, forbid_unknown_fields=False):
    resume: str


class _ThreadState(msgspec.Struct, forbid_unknown_fields=False):
    context: _ContextState | None = None
    sessions: dict[str, _SessionState] = msgspec.field(default_factory=dict)
    topic_title: str | None = None
    default_engine: str | None = None


class _TopicState(msgspec.Struct, forbid_unknown_fields=False):
    version: int
    threads: dict[str, _ThreadState] = msgspec.field(default_factory=dict)


def resolve_state_path(config_path: Path) -> Path:
    return config_path.with_name(STATE_FILENAME)


def _thread_key(chat_id: int, thread_id: int) -> str:
    return f"{chat_id}:{thread_id}"


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _context_from_state(state: _ContextState | None) -> RunContext | None:
    if state is None:
        return None
    project = _normalize_text(state.project)
    branch = _normalize_text(state.branch)
    if project is None and branch is None:
        return None
    return RunContext(project=project, branch=branch)


def _context_to_state(context: RunContext | None) -> _ContextState | None:
    if context is None:
        return None
    project = _normalize_text(context.project)
    branch = _normalize_text(context.branch)
    if project is None and branch is None:
        return None
    return _ContextState(project=project, branch=branch)


def _new_state() -> _TopicState:
    return _TopicState(version=STATE_VERSION, threads={})


class TopicStateStore(JsonStateStore[_TopicState]):
    def __init__(self, path: Path) -> None:
        super().__init__(
            path,
            version=STATE_VERSION,
            state_type=_TopicState,
            state_factory=_new_state,
            log_prefix="telegram.topic_state",
            logger=logger,
        )

    async def get_thread(
        self, chat_id: int, thread_id: int
    ) -> TopicThreadSnapshot | None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return None
            return self._snapshot_locked(thread, chat_id, thread_id)

    async def get_context(self, chat_id: int, thread_id: int) -> RunContext | None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return None
            return _context_from_state(thread.context)

    async def set_context(
        self,
        chat_id: int,
        thread_id: int,
        context: RunContext,
        *,
        topic_title: str | None = None,
    ) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._ensure_thread_locked(chat_id, thread_id)
            thread.context = _context_to_state(context)
            if topic_title is not None:
                thread.topic_title = topic_title
            self._save_locked()

    async def clear_context(self, chat_id: int, thread_id: int) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return
            thread.context = None
            self._save_locked()

    async def get_session_resume(
        self, chat_id: int, thread_id: int, engine: str
    ) -> ResumeToken | None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return None
            entry = thread.sessions.get(engine)
            if entry is None or not entry.resume:
                return None
            return ResumeToken(engine=engine, value=entry.resume)

    async def get_default_engine(self, chat_id: int, thread_id: int) -> str | None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return None
            return _normalize_text(thread.default_engine)

    async def set_default_engine(
        self, chat_id: int, thread_id: int, engine: str | None
    ) -> None:
        normalized = _normalize_text(engine)
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._ensure_thread_locked(chat_id, thread_id)
            thread.default_engine = normalized
            self._save_locked()

    async def clear_default_engine(self, chat_id: int, thread_id: int) -> None:
        await self.set_default_engine(chat_id, thread_id, None)

    async def set_session_resume(
        self, chat_id: int, thread_id: int, token: ResumeToken
    ) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._ensure_thread_locked(chat_id, thread_id)
            thread.sessions[token.engine] = _SessionState(resume=token.value)
            self._save_locked()

    async def clear_sessions(self, chat_id: int, thread_id: int) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return
            thread.sessions = {}
            self._save_locked()

    async def delete_thread(self, chat_id: int, thread_id: int) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            key = _thread_key(chat_id, thread_id)
            if key not in self._state.threads:
                return
            self._state.threads.pop(key, None)
            self._save_locked()

    async def find_thread_for_context(
        self, chat_id: int, context: RunContext
    ) -> int | None:
        async with self._lock:
            self._reload_locked_if_needed()
            target_project = _normalize_text(context.project)
            target_branch = _normalize_text(context.branch)
            for raw_key, thread in self._state.threads.items():
                if not raw_key.startswith(f"{chat_id}:"):
                    continue
                parsed = _context_from_state(thread.context)
                if parsed is None:
                    continue
                if parsed.project != target_project or parsed.branch != target_branch:
                    continue
                try:
                    _, thread_str = raw_key.split(":", 1)
                    return int(thread_str)
                except ValueError:
                    continue
            return None

    def _snapshot_locked(
        self, thread: _ThreadState, chat_id: int, thread_id: int
    ) -> TopicThreadSnapshot:
        sessions = {
            engine: entry.resume
            for engine, entry in thread.sessions.items()
            if entry.resume
        }
        return TopicThreadSnapshot(
            chat_id=chat_id,
            thread_id=thread_id,
            context=_context_from_state(thread.context),
            sessions=sessions,
            topic_title=thread.topic_title,
            default_engine=_normalize_text(thread.default_engine),
        )

    def _get_thread_locked(self, chat_id: int, thread_id: int) -> _ThreadState | None:
        return self._state.threads.get(_thread_key(chat_id, thread_id))

    def _ensure_thread_locked(self, chat_id: int, thread_id: int) -> _ThreadState:
        key = _thread_key(chat_id, thread_id)
        entry = self._state.threads.get(key)
        if entry is not None:
            return entry
        entry = _ThreadState()
        self._state.threads[key] = entry
        return entry
