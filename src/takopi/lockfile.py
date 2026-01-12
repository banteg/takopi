from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LockInfo:
    pid: int | None
    token_fingerprint: str | None


class LockError(RuntimeError):
    def __init__(
        self,
        *,
        path: Path,
        state: str,
    ) -> None:
        self.path = path
        self.state = state
        super().__init__(_format_lock_message(path, state))


@dataclass(slots=True)
class LockHandle:
    path: Path

    def release(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "lock.release.failed",
                path=str(self.path),
                error=str(exc),
                error_type=exc.__class__.__name__,
            )

    def __enter__(self) -> LockHandle:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def token_fingerprint(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return digest[:10]


def lock_path_for_config(config_path: Path) -> Path:
    return config_path.with_suffix(".lock")


def acquire_lock(
    *, config_path: Path, token_fingerprint: str | None = None
) -> LockHandle:
    cfg_path = config_path.expanduser().resolve()
    lock_path = lock_path_for_config(cfg_path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_lock_info(lock_path)
        if existing:
            if (
                token_fingerprint
                and existing.token_fingerprint
                and existing.token_fingerprint != token_fingerprint
            ):
                _write_lock_info(
                    lock_path,
                    pid=os.getpid(),
                    token_fingerprint=token_fingerprint,
                )
                return LockHandle(path=lock_path)
            if _pid_running(existing.pid):
                raise LockError(path=lock_path, state="running") from None
        _write_lock_info(
            lock_path,
            pid=os.getpid(),
            token_fingerprint=token_fingerprint,
        )
    except OSError as exc:
        raise LockError(path=lock_path, state=str(exc)) from exc

    return LockHandle(path=lock_path)


def _read_lock_info(path: Path) -> LockInfo | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int):
        pid = None
    token_hint = data.get("token_fingerprint")
    if not isinstance(token_hint, str):
        token_hint = None
    return LockInfo(
        pid=pid,
        token_fingerprint=token_hint,
    )


def _write_lock_info(path: Path, *, pid: int, token_fingerprint: str | None) -> None:
    payload = {"pid": pid, "token_fingerprint": token_fingerprint}
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _pid_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _format_lock_message(path: Path, state: str) -> str:
    if state != "running":
        return f"error: lock failed: {state}"
    header = "error: already running"
    display_path = _display_lock_path(path)
    lines = [header, f"remove {display_path} if stale"]
    return "\n".join(lines)


def _display_lock_path(path: Path) -> str:
    home = Path.home()
    try:
        resolved = path.expanduser().resolve()
        rel = resolved.relative_to(home)
        return f"~/{rel}"
    except (ValueError, OSError):
        return str(path)
