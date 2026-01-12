from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import anyio
import msgspec

from ..utils.json_state import atomic_write_json


class _Logger(Protocol):
    def warning(self, event: str, **fields: Any) -> None: ...


class _VersionedState(Protocol):
    version: int


class JsonStateStore[T: _VersionedState]:
    def __init__(
        self,
        path: Path,
        *,
        version: int,
        state_type: type[T],
        state_factory: Callable[[], T],
        log_prefix: str,
        logger: _Logger,
    ) -> None:
        self._path = path
        self._lock = anyio.Lock()
        self._loaded = False
        self._mtime_ns: int | None = None
        self._state_type = state_type
        self._state_factory = state_factory
        self._version = version
        self._log_prefix = log_prefix
        self._logger = logger
        self._state = state_factory()

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
            self._state = self._state_factory()
            return
        try:
            payload = msgspec.json.decode(
                self._path.read_bytes(), type=self._state_type
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                f"{self._log_prefix}.load_failed",
                path=str(self._path),
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            self._state = self._state_factory()
            return
        if payload.version != self._version:
            self._logger.warning(
                f"{self._log_prefix}.version_mismatch",
                path=str(self._path),
                version=payload.version,
                expected=self._version,
            )
            self._state = self._state_factory()
            return
        self._state = payload

    def _save_locked(self) -> None:
        payload = msgspec.to_builtins(self._state)
        atomic_write_json(self._path, payload)
        self._mtime_ns = self._stat_mtime_ns()
