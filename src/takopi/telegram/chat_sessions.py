from __future__ import annotations

import json
import os
from pathlib import Path

import anyio
import msgspec

from ..logging import get_logger
from ..model import ResumeToken

logger = get_logger(__name__)

STATE_VERSION = 1
STATE_FILENAME = "telegram_chat_sessions_state.json"


class _SessionState(msgspec.Struct, forbid_unknown_fields=False):
    resume: str


class _ChatState(msgspec.Struct, forbid_unknown_fields=False):
    sessions: dict[str, _SessionState] = msgspec.field(default_factory=dict)


class _ChatSessionsState(msgspec.Struct, forbid_unknown_fields=False):
    version: int
    chats: dict[str, _ChatState] = msgspec.field(default_factory=dict)


def resolve_sessions_path(config_path: Path) -> Path:
    return config_path.with_name(STATE_FILENAME)


def _chat_key(chat_id: int, owner_id: int | None) -> str:
    owner = "chat" if owner_id is None else str(owner_id)
    return f"{chat_id}:{owner}"


class ChatSessionStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = anyio.Lock()
        self._loaded = False
        self._mtime_ns: int | None = None
        self._state = _ChatSessionsState(version=STATE_VERSION, chats={})

    async def get_session_resume(
        self, chat_id: int, owner_id: int | None, engine: str
    ) -> ResumeToken | None:
        async with self._lock:
            self._reload_locked_if_needed()
            chat = self._get_chat_locked(chat_id, owner_id)
            if chat is None:
                return None
            entry = chat.sessions.get(engine)
            if entry is None or not entry.resume:
                return None
            return ResumeToken(engine=engine, value=entry.resume)

    async def set_session_resume(
        self, chat_id: int, owner_id: int | None, token: ResumeToken
    ) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            chat = self._ensure_chat_locked(chat_id, owner_id)
            chat.sessions[token.engine] = _SessionState(resume=token.value)
            self._save_locked()

    async def clear_sessions(self, chat_id: int, owner_id: int | None) -> None:
        async with self._lock:
            self._reload_locked_if_needed()
            chat = self._get_chat_locked(chat_id, owner_id)
            if chat is None:
                return
            chat.sessions = {}
            self._save_locked()

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
            self._state = _ChatSessionsState(version=STATE_VERSION, chats={})
            return
        try:
            payload = msgspec.json.decode(
                self._path.read_bytes(), type=_ChatSessionsState
            )
        except Exception as exc:
            logger.warning(
                "telegram.chat_sessions.load_failed",
                path=str(self._path),
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            self._state = _ChatSessionsState(version=STATE_VERSION, chats={})
            return
        if payload.version != STATE_VERSION:
            logger.warning(
                "telegram.chat_sessions.version_mismatch",
                path=str(self._path),
                version=payload.version,
                expected=STATE_VERSION,
            )
            self._state = _ChatSessionsState(version=STATE_VERSION, chats={})
            return
        self._state = payload

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = msgspec.to_builtins(self._state)
        tmp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, self._path)
        self._mtime_ns = self._stat_mtime_ns()

    def _get_chat_locked(self, chat_id: int, owner_id: int | None) -> _ChatState | None:
        return self._state.chats.get(_chat_key(chat_id, owner_id))

    def _ensure_chat_locked(self, chat_id: int, owner_id: int | None) -> _ChatState:
        key = _chat_key(chat_id, owner_id)
        entry = self._state.chats.get(key)
        if entry is not None:
            return entry
        entry = _ChatState()
        self._state.chats[key] = entry
        return entry
