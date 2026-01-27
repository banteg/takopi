from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import msgspec

from ..context import RunContext
from ..logging import get_logger
from ..model import ResumeToken
from .engine_overrides import EngineOverrides, normalize_overrides
from .state_store import JsonStateStore

logger = get_logger(__name__)

STATE_VERSION = 1
STATE_FILENAME = "telegram_topics_state.json"


@dataclass(frozen=True, slots=True)
class TopicThreadSnapshot:
    chat_id: int
    thread_id: int
    contexts: tuple[RunContext, ...]
    active_project: str | None
    context: RunContext | None
    sessions: dict[str, str]
    topic_title: str | None
    default_engine: str | None


@dataclass(frozen=True, slots=True)
class TopicBinding:
    contexts: tuple[RunContext, ...]
    active_project: str | None

    def context_for_project(self, project: str | None) -> RunContext | None:
        if project is None:
            return None
        for context in self.contexts:
            if context.project == project:
                return context
        return None

    def active_context(self) -> RunContext | None:
        if self.active_project is not None:
            return self.context_for_project(self.active_project)
        if len(self.contexts) == 1:
            return self.contexts[0]
        return None


class _ContextState(msgspec.Struct, forbid_unknown_fields=False):
    project: str | None = None
    branch: str | None = None


class _SessionState(msgspec.Struct, forbid_unknown_fields=False):
    resume: str


class _ThreadState(msgspec.Struct, forbid_unknown_fields=False):
    context: _ContextState | None = None
    contexts: list[_ContextState] = msgspec.field(default_factory=list)
    active_project: str | None = None
    sessions: dict[str, _SessionState] = msgspec.field(default_factory=dict)
    topic_title: str | None = None
    default_engine: str | None = None
    trigger_mode: str | None = None
    engine_overrides: dict[str, EngineOverrides] = msgspec.field(default_factory=dict)


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


def _normalize_trigger_mode(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if value == "mentions":
        return "mentions"
    if value == "all":
        return None
    return None


def _normalize_engine_id(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    return value or None


def _normalize_project(value: str | None) -> str | None:
    return _normalize_text(value)


def _normalize_context(context: RunContext | None) -> RunContext | None:
    if context is None:
        return None
    project = _normalize_project(context.project)
    if project is None:
        return None
    branch = _normalize_text(context.branch)
    return RunContext(project=project, branch=branch)


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


def _contexts_from_thread(thread: _ThreadState) -> list[RunContext]:
    contexts: list[RunContext] = []
    for entry in thread.contexts:
        normalized = _normalize_context(_context_from_state(entry))
        if normalized is None:
            continue
        contexts.append(normalized)
    if not contexts:
        legacy = _normalize_context(_context_from_state(thread.context))
        if legacy is not None:
            contexts.append(legacy)
    seen: set[str] = set()
    deduped: list[RunContext] = []
    for ctx in contexts:
        project = ctx.project
        if project is None or project in seen:
            continue
        seen.add(project)
        deduped.append(ctx)
    return deduped


def _contexts_to_state(contexts: list[RunContext]) -> list[_ContextState]:
    entries: list[_ContextState] = []
    for ctx in contexts:
        normalized = _normalize_context(ctx)
        if normalized is None:
            continue
        state = _context_to_state(normalized)
        if state is not None:
            entries.append(state)
    return entries


def _resolve_active_project(
    contexts: list[RunContext], active_project: str | None
) -> str | None:
    normalized = _normalize_project(active_project)
    if normalized is not None and any(
        ctx.project == normalized for ctx in contexts if ctx.project is not None
    ):
        return normalized
    if len(contexts) == 1 and contexts[0].project is not None:
        return contexts[0].project
    return None


def _resolve_active_context(
    contexts: list[RunContext], active_project: str | None
) -> RunContext | None:
    if active_project is not None:
        for ctx in contexts:
            if ctx.project == active_project:
                return ctx
    if len(contexts) == 1:
        return contexts[0]
    return None


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

    async def get_binding(
        self, chat_id: int, thread_id: int
    ) -> TopicBinding | None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return None
            contexts = _contexts_from_thread(thread)
            active_project = _resolve_active_project(contexts, thread.active_project)
            return TopicBinding(
                contexts=tuple(contexts),
                active_project=active_project,
            )

    async def get_context(self, chat_id: int, thread_id: int) -> RunContext | None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return None
            contexts = _contexts_from_thread(thread)
            active_project = _resolve_active_project(contexts, thread.active_project)
            return _resolve_active_context(contexts, active_project)

    async def get_contexts(
        self, chat_id: int, thread_id: int
    ) -> tuple[RunContext, ...]:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return ()
            contexts = _contexts_from_thread(thread)
            return tuple(contexts)

    async def set_contexts(
        self,
        chat_id: int,
        thread_id: int,
        contexts: list[RunContext],
        *,
        active_project: str | None = None,
        topic_title: str | None = None,
    ) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._ensure_thread_locked(chat_id, thread_id)
            normalized: list[RunContext] = []
            for ctx in contexts:
                normalized_ctx = _normalize_context(ctx)
                if normalized_ctx is not None:
                    normalized.append(normalized_ctx)
            unique: list[RunContext] = []
            seen: set[str] = set()
            for ctx in normalized:
                if ctx.project is None or ctx.project in seen:
                    continue
                seen.add(ctx.project)
                unique.append(ctx)
            resolved_active = _resolve_active_project(unique, active_project)
            active_context = _resolve_active_context(unique, resolved_active)
            thread.contexts = _contexts_to_state(unique)
            thread.active_project = resolved_active
            thread.context = _context_to_state(active_context)
            if topic_title is not None:
                thread.topic_title = topic_title
            self._save_locked()

    async def add_context(
        self, chat_id: int, thread_id: int, context: RunContext
    ) -> None:
        normalized = _normalize_context(context)
        if normalized is None:
            return
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._ensure_thread_locked(chat_id, thread_id)
            contexts = _contexts_from_thread(thread)
            updated = False
            for idx, ctx in enumerate(contexts):
                if ctx.project == normalized.project:
                    contexts[idx] = normalized
                    updated = True
                    break
            if not updated:
                contexts.append(normalized)
            resolved_active = _resolve_active_project(contexts, thread.active_project)
            if resolved_active is None:
                resolved_active = normalized.project
            active_context = _resolve_active_context(contexts, resolved_active)
            thread.contexts = _contexts_to_state(contexts)
            thread.active_project = resolved_active
            thread.context = _context_to_state(active_context)
            self._save_locked()

    async def remove_context(
        self, chat_id: int, thread_id: int, project: str
    ) -> None:
        project_key = _normalize_project(project)
        if project_key is None:
            return
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return
            contexts = [
                ctx
                for ctx in _contexts_from_thread(thread)
                if ctx.project != project_key
            ]
            resolved_active = _resolve_active_project(contexts, thread.active_project)
            if thread.active_project == project_key:
                resolved_active = _resolve_active_project(contexts, None)
            active_context = _resolve_active_context(contexts, resolved_active)
            thread.contexts = _contexts_to_state(contexts)
            thread.active_project = resolved_active
            thread.context = _context_to_state(active_context)
            self._save_locked()

    async def set_active_project(
        self, chat_id: int, thread_id: int, project: str
    ) -> bool:
        project_key = _normalize_project(project)
        if project_key is None:
            return False
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return False
            contexts = _contexts_from_thread(thread)
            if not any(ctx.project == project_key for ctx in contexts):
                return False
            active_context = _resolve_active_context(contexts, project_key)
            thread.active_project = project_key
            thread.context = _context_to_state(active_context)
            self._save_locked()
            return True

    async def set_context(
        self,
        chat_id: int,
        thread_id: int,
        context: RunContext,
        *,
        topic_title: str | None = None,
    ) -> None:
        normalized = _normalize_context(context)
        if normalized is None:
            return
        await self.set_contexts(
            chat_id,
            thread_id,
            [normalized],
            active_project=normalized.project,
            topic_title=topic_title,
        )

    async def clear_context(self, chat_id: int, thread_id: int) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return
            thread.context = None
            thread.contexts = []
            thread.active_project = None
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

    async def get_trigger_mode(self, chat_id: int, thread_id: int) -> str | None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return None
            return _normalize_trigger_mode(thread.trigger_mode)

    async def get_engine_override(
        self, chat_id: int, thread_id: int, engine: str
    ) -> EngineOverrides | None:
        engine_key = _normalize_engine_id(engine)
        if engine_key is None:
            return None
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return None
            override = thread.engine_overrides.get(engine_key)
            return normalize_overrides(override)

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

    async def set_trigger_mode(
        self, chat_id: int, thread_id: int, mode: str | None
    ) -> None:
        normalized = _normalize_trigger_mode(mode)
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._ensure_thread_locked(chat_id, thread_id)
            thread.trigger_mode = normalized
            self._save_locked()

    async def clear_trigger_mode(self, chat_id: int, thread_id: int) -> None:
        await self.set_trigger_mode(chat_id, thread_id, None)

    async def set_engine_override(
        self,
        chat_id: int,
        thread_id: int,
        engine: str,
        override: EngineOverrides | None,
    ) -> None:
        engine_key = _normalize_engine_id(engine)
        if engine_key is None:
            return
        normalized = normalize_overrides(override)
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._ensure_thread_locked(chat_id, thread_id)
            if normalized is None:
                thread.engine_overrides.pop(engine_key, None)
            else:
                thread.engine_overrides[engine_key] = normalized
            self._save_locked()

    async def clear_engine_override(
        self, chat_id: int, thread_id: int, engine: str
    ) -> None:
        await self.set_engine_override(chat_id, thread_id, engine, None)

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
                contexts = _contexts_from_thread(thread)
                if not contexts:
                    continue
                matched = any(
                    ctx.project == target_project and ctx.branch == target_branch
                    for ctx in contexts
                )
                if not matched:
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
        contexts = _contexts_from_thread(thread)
        active_project = _resolve_active_project(contexts, thread.active_project)
        active_context = _resolve_active_context(contexts, active_project)
        return TopicThreadSnapshot(
            chat_id=chat_id,
            thread_id=thread_id,
            contexts=tuple(contexts),
            active_project=active_project,
            context=active_context,
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
