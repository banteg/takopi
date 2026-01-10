from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import anyio

from ..context import RunContext
from ..logging import get_logger
from ..model import ResumeToken

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
    created_by_bot: bool | None
    updated_at: float | None


def resolve_state_path(config_path: Path) -> Path:
    return config_path.with_name(STATE_FILENAME)


def _thread_key(chat_id: int, thread_id: int) -> str:
    return f"{chat_id}:{thread_id}"


def _parse_context(raw: object) -> RunContext | None:
    if not isinstance(raw, dict):
        return None
    payload = cast(dict[str, object], raw)
    project = payload.get("project")
    branch = payload.get("branch")
    if project is not None and not isinstance(project, str):
        project = None
    if isinstance(project, str):
        project = project.strip() or None
    if branch is not None and not isinstance(branch, str):
        branch = None
    if isinstance(branch, str):
        branch = branch.strip() or None
    if project is None and branch is None:
        return None
    return RunContext(project=project, branch=branch)


def _dump_context(context: RunContext | None) -> dict[str, str] | None:
    if context is None or (context.project is None and context.branch is None):
        return None
    payload: dict[str, str] = {}
    if context.project is not None:
        payload["project"] = context.project
    if context.branch is not None:
        payload["branch"] = context.branch
    return payload or None


class TopicStateStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = anyio.Lock()
        self._loaded = False
        self._mtime_ns: int | None = None
        self._data: dict[str, Any] = {
            "version": STATE_VERSION,
            "threads": {},
        }

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
            return _parse_context(thread.get("context"))

    async def set_context(
        self,
        chat_id: int,
        thread_id: int,
        context: RunContext,
        *,
        topic_title: str | None = None,
        created_by_bot: bool | None = None,
    ) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._ensure_thread_locked(chat_id, thread_id)
            thread["context"] = _dump_context(context)
            if topic_title is not None:
                thread["topic_title"] = topic_title
            if created_by_bot is not None:
                thread["created_by_bot"] = created_by_bot
            thread["updated_at"] = time.time()
            self._save_locked()

    async def clear_context(self, chat_id: int, thread_id: int) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return
            thread.pop("context", None)
            thread["updated_at"] = time.time()
            self._save_locked()

    async def get_session_resume(
        self, chat_id: int, thread_id: int, engine: str
    ) -> ResumeToken | None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return None
            sessions = thread.get("sessions")
            if not isinstance(sessions, dict):
                return None
            entry = sessions.get(engine)
            if not isinstance(entry, dict):
                return None
            value = entry.get("resume")
            if not isinstance(value, str) or not value:
                return None
            return ResumeToken(engine=engine, value=value)

    async def set_session_resume(
        self, chat_id: int, thread_id: int, token: ResumeToken
    ) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._ensure_thread_locked(chat_id, thread_id)
            sessions = thread.get("sessions")
            if not isinstance(sessions, dict):
                sessions = {}
                thread["sessions"] = sessions
            sessions[token.engine] = {
                "resume": token.value,
                "updated_at": time.time(),
            }
            thread["updated_at"] = time.time()
            self._save_locked()

    async def clear_sessions(self, chat_id: int, thread_id: int) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            thread = self._get_thread_locked(chat_id, thread_id)
            if thread is None:
                return
            thread.pop("sessions", None)
            thread["updated_at"] = time.time()
            self._save_locked()

    async def find_thread_for_context(
        self, chat_id: int, context: RunContext
    ) -> int | None:
        async with self._lock:
            self._reload_locked_if_needed()
            threads = self._data.get("threads")
            if not isinstance(threads, dict):
                return None
            for raw_key, payload in threads.items():
                if not isinstance(raw_key, str) or not isinstance(payload, dict):
                    continue
                parsed = _parse_context(payload.get("context"))
                if parsed is None:
                    continue
                if parsed.project != context.project or parsed.branch != context.branch:
                    continue
                if not raw_key.startswith(f"{chat_id}:"):
                    continue
                try:
                    _, thread_str = raw_key.split(":", 1)
                    return int(thread_str)
                except (ValueError, TypeError):
                    continue
            return None

    def _snapshot_locked(
        self, thread: dict[str, Any], chat_id: int, thread_id: int
    ) -> TopicThreadSnapshot:
        sessions: dict[str, str] = {}
        raw_sessions = thread.get("sessions")
        if isinstance(raw_sessions, dict):
            for engine, entry in raw_sessions.items():
                if not isinstance(engine, str) or not isinstance(entry, dict):
                    continue
                value = entry.get("resume")
                if isinstance(value, str) and value:
                    sessions[engine] = value
        updated_at = thread.get("updated_at")
        if not isinstance(updated_at, (int, float)):
            updated_at = None
        topic_title = thread.get("topic_title")
        if not isinstance(topic_title, str):
            topic_title = None
        created_by_bot = thread.get("created_by_bot")
        if not isinstance(created_by_bot, bool):
            created_by_bot = None
        return TopicThreadSnapshot(
            chat_id=chat_id,
            thread_id=thread_id,
            context=_parse_context(thread.get("context")),
            sessions=sessions,
            topic_title=topic_title,
            created_by_bot=created_by_bot,
            updated_at=updated_at,
        )

    def _stat_mtime_ns(self) -> int | None:
        try:
            return self._path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _reload_locked_if_needed(self) -> None:
        current = self._stat_mtime_ns()
        if self._loaded and current == self._mtime_ns:
            return
        self._load_locked()

    def _load_locked(self) -> None:
        self._loaded = True
        self._mtime_ns = self._stat_mtime_ns()
        if self._mtime_ns is None:
            self._data = {"version": STATE_VERSION, "threads": {}}
            return
        try:
            payload = json.loads(self._path.read_text())
        except Exception as exc:
            logger.warning(
                "telegram.topic_state.load_failed",
                path=str(self._path),
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            self._data = {"version": STATE_VERSION, "threads": {}}
            return
        if not isinstance(payload, dict):
            self._data = {"version": STATE_VERSION, "threads": {}}
            return
        version = payload.get("version")
        if version != STATE_VERSION:
            logger.warning(
                "telegram.topic_state.version_mismatch",
                path=str(self._path),
                version=version,
                expected=STATE_VERSION,
            )
            self._data = {"version": STATE_VERSION, "threads": {}}
            return
        threads = payload.get("threads")
        if not isinstance(threads, dict):
            threads = {}
        self._data = {"version": STATE_VERSION, "threads": threads}

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": STATE_VERSION, "threads": self._data.get("threads", {})}
        tmp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, self._path)
        self._mtime_ns = self._stat_mtime_ns()

    def _get_thread_locked(self, chat_id: int, thread_id: int) -> dict[str, Any] | None:
        threads = self._data.get("threads")
        if not isinstance(threads, dict):
            return None
        entry = threads.get(_thread_key(chat_id, thread_id))
        return entry if isinstance(entry, dict) else None

    def _ensure_thread_locked(self, chat_id: int, thread_id: int) -> dict[str, Any]:
        threads = self._data.get("threads")
        if not isinstance(threads, dict):
            threads = {}
            self._data["threads"] = threads
        key = _thread_key(chat_id, thread_id)
        entry = threads.get(key)
        if isinstance(entry, dict):
            return entry
        entry = {"chat_id": chat_id, "thread_id": thread_id}
        threads[key] = entry
        return entry
