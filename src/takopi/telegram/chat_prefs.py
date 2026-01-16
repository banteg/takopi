from __future__ import annotations

from pathlib import Path

import msgspec

from ..context import RunContext
from ..logging import get_logger
from .engine_overrides import EngineOverrides, normalize_overrides
from .state_store import JsonStateStore

logger = get_logger(__name__)

STATE_VERSION = 1
STATE_FILENAME = "telegram_chat_prefs_state.json"


class _ChatPrefs(msgspec.Struct, forbid_unknown_fields=False):
    default_engine: str | None = None
    trigger_mode: str | None = None
    context_project: str | None = None
    context_branch: str | None = None
    engine_overrides: dict[str, EngineOverrides] = msgspec.field(default_factory=dict)


class _ChatPrefsState(msgspec.Struct, forbid_unknown_fields=False):
    version: int
    chats: dict[str, _ChatPrefs] = msgspec.field(default_factory=dict)


def resolve_prefs_path(config_path: Path) -> Path:
    return config_path.with_name(STATE_FILENAME)


def _chat_key(chat_id: int) -> str:
    return str(chat_id)


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


def _new_state() -> _ChatPrefsState:
    return _ChatPrefsState(version=STATE_VERSION, chats={})


class ChatPrefsStore(JsonStateStore[_ChatPrefsState]):
    def __init__(self, path: Path) -> None:
        super().__init__(
            path,
            version=STATE_VERSION,
            state_type=_ChatPrefsState,
            state_factory=_new_state,
            log_prefix="telegram.chat_prefs",
            logger=logger,
        )

    async def get_default_engine(self, chat_id: int) -> str | None:
        async with self._lock:
            self._reload_locked_if_needed()
            chat = self._get_chat_locked(chat_id)
            if chat is None:
                return None
            return _normalize_text(chat.default_engine)

    async def set_default_engine(self, chat_id: int, engine: str | None) -> None:
        normalized = _normalize_text(engine)
        async with self._lock:
            self._reload_locked_if_needed()
            chat = self._get_chat_locked(chat_id)
            if normalized is None:
                if chat is None:
                    return
                chat.default_engine = None
                if self._chat_is_empty(chat):
                    self._remove_chat_locked(chat_id)
                self._save_locked()
                return
            chat = self._ensure_chat_locked(chat_id)
            chat.default_engine = normalized
            self._save_locked()

    async def clear_default_engine(self, chat_id: int) -> None:
        await self.set_default_engine(chat_id, None)

    async def get_trigger_mode(self, chat_id: int) -> str | None:
        async with self._lock:
            self._reload_locked_if_needed()
            chat = self._get_chat_locked(chat_id)
            if chat is None:
                return None
            return _normalize_trigger_mode(chat.trigger_mode)

    async def set_trigger_mode(self, chat_id: int, mode: str | None) -> None:
        normalized = _normalize_trigger_mode(mode)
        async with self._lock:
            self._reload_locked_if_needed()
            chat = self._get_chat_locked(chat_id)
            if normalized is None:
                if chat is None:
                    return
                chat.trigger_mode = None
                if self._chat_is_empty(chat):
                    self._remove_chat_locked(chat_id)
                self._save_locked()
                return
            chat = self._ensure_chat_locked(chat_id)
            chat.trigger_mode = normalized
            self._save_locked()

    async def clear_trigger_mode(self, chat_id: int) -> None:
        await self.set_trigger_mode(chat_id, None)

    async def get_context(self, chat_id: int) -> RunContext | None:
        async with self._lock:
            self._reload_locked_if_needed()
            chat = self._get_chat_locked(chat_id)
            if chat is None:
                return None
            project = _normalize_text(chat.context_project)
            if project is None:
                return None
            branch = _normalize_text(chat.context_branch)
            return RunContext(project=project, branch=branch)

    async def set_context(self, chat_id: int, context: RunContext | None) -> None:
        project = _normalize_text(context.project) if context is not None else None
        branch = _normalize_text(context.branch) if context is not None else None
        async with self._lock:
            self._reload_locked_if_needed()
            chat = self._get_chat_locked(chat_id)
            if project is None:
                if chat is None:
                    return
                chat.context_project = None
                chat.context_branch = None
                if self._chat_is_empty(chat):
                    self._remove_chat_locked(chat_id)
                self._save_locked()
                return
            chat = self._ensure_chat_locked(chat_id)
            chat.context_project = project
            chat.context_branch = branch
            self._save_locked()

    async def clear_context(self, chat_id: int) -> None:
        await self.set_context(chat_id, None)

    async def get_engine_override(
        self, chat_id: int, engine: str
    ) -> EngineOverrides | None:
        engine_key = _normalize_engine_id(engine)
        if engine_key is None:
            return None
        async with self._lock:
            self._reload_locked_if_needed()
            chat = self._get_chat_locked(chat_id)
            if chat is None:
                return None
            override = chat.engine_overrides.get(engine_key)
            return normalize_overrides(override)

    async def set_engine_override(
        self, chat_id: int, engine: str, override: EngineOverrides | None
    ) -> None:
        engine_key = _normalize_engine_id(engine)
        if engine_key is None:
            return
        normalized = normalize_overrides(override)
        async with self._lock:
            self._reload_locked_if_needed()
            chat = self._get_chat_locked(chat_id)
            if normalized is None:
                if chat is None:
                    return
                chat.engine_overrides.pop(engine_key, None)
                if self._chat_is_empty(chat):
                    self._remove_chat_locked(chat_id)
                self._save_locked()
                return
            chat = self._ensure_chat_locked(chat_id)
            chat.engine_overrides[engine_key] = normalized
            self._save_locked()

    async def clear_engine_override(self, chat_id: int, engine: str) -> None:
        await self.set_engine_override(chat_id, engine, None)

    def _get_chat_locked(self, chat_id: int) -> _ChatPrefs | None:
        return self._state.chats.get(_chat_key(chat_id))

    def _ensure_chat_locked(self, chat_id: int) -> _ChatPrefs:
        key = _chat_key(chat_id)
        entry = self._state.chats.get(key)
        if entry is not None:
            return entry
        entry = _ChatPrefs()
        self._state.chats[key] = entry
        return entry

    def _chat_is_empty(self, chat: _ChatPrefs) -> bool:
        return (
            _normalize_text(chat.default_engine) is None
            and _normalize_trigger_mode(chat.trigger_mode) is None
            and _normalize_text(chat.context_project) is None
            and _normalize_text(chat.context_branch) is None
            and not self._has_engine_overrides(chat.engine_overrides)
        )

    @staticmethod
    def _has_engine_overrides(overrides: dict[str, EngineOverrides]) -> bool:
        for override in overrides.values():
            if normalize_overrides(override) is not None:
                return True
        return False

    def _remove_chat_locked(self, chat_id: int) -> bool:
        key = _chat_key(chat_id)
        if key not in self._state.chats:
            return False
        del self._state.chats[key]
        return True
