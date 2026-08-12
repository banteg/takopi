from __future__ import annotations

import hashlib
import json
import os
import sys
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .logging import get_logger

logger = get_logger(__name__)

# The advisory lock is taken on a byte far past anything we write. On Windows a
# locked range is mandatory, so a lock covering the JSON would stop a second
# instance from reading the file to report who is holding it.
_LOCK_OFFSET = 1 << 30

if sys.platform == "win32":  # pragma: no cover - platform specific
    import msvcrt

    def _try_acquire_os_lock(fh: IO[str]) -> bool:
        try:
            fh.seek(_LOCK_OFFSET)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _release_os_lock(fh: IO[str]) -> None:
        with contextlib.suppress(OSError):
            fh.seek(_LOCK_OFFSET)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - platform specific
    import fcntl

    def _try_acquire_os_lock(fh: IO[str]) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _release_os_lock(fh: IO[str]) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


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
        holder_pid: int | None = None,
    ) -> None:
        self.path = path
        self.state = state
        self.holder_pid = holder_pid
        super().__init__(_format_lock_message(path, state, holder_pid))


@dataclass(slots=True)
class LockHandle:
    path: Path
    file: IO[str] | None = None

    def release(self) -> None:
        fh, self.file = self.file, None
        if fh is not None:
            _release_os_lock(fh)
            with contextlib.suppress(OSError):
                fh.close()
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
    """Take the single-instance lock, or raise LockError if someone holds it.

    Exclusivity comes from an advisory lock the OS holds on an open file
    descriptor, not from the pid recorded in the file. The kernel drops it the
    moment the owning process exits — however it exits — so the lock cannot go
    stale, cannot be defeated by pid reuse, and does not depend on one process
    being allowed to inspect another. The pid and token fingerprint are still
    written, purely so a second instance can name the holder.
    """
    cfg_path = config_path.expanduser().resolve()
    lock_path = lock_path_for_config(cfg_path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.touch(exist_ok=True)
        # "r+", not "a+": on Windows an append handle ignores seek() on write,
        # so a stale payload from a previous owner could never be overwritten.
        fh = open(lock_path, "r+", encoding="utf-8")  # noqa: SIM115
    except OSError as exc:
        raise LockError(path=lock_path, state=str(exc)) from exc

    if not _try_acquire_os_lock(fh):
        # Read the holder for the message only. The lock deliberately sits past
        # the payload so this read still works.
        holder = _read_lock_info(lock_path)
        with contextlib.suppress(OSError):
            fh.close()
        raise LockError(
            path=lock_path,
            state="running",
            holder_pid=holder.pid if holder else None,
        )

    try:
        _write_lock_info(fh, pid=os.getpid(), token_fingerprint=token_fingerprint)
    except OSError as exc:
        _release_os_lock(fh)
        with contextlib.suppress(OSError):
            fh.close()
        raise LockError(path=lock_path, state=str(exc)) from exc

    return LockHandle(path=lock_path, file=fh)


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


def _write_lock_info(fh: IO[str], *, pid: int, token_fingerprint: str | None) -> None:
    payload = {"pid": pid, "token_fingerprint": token_fingerprint}
    fh.seek(0)
    fh.truncate(0)
    fh.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    fh.flush()


def _pid_running(pid: int | None) -> bool:
    """Best-effort liveness check, used for diagnostics only.

    Exclusivity no longer rests on this: on Windows a non-elevated process
    cannot open an elevated one, and a recycled pid looks alive when it is not.
    """
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


def _format_lock_message(path: Path, state: str, holder_pid: int | None = None) -> str:
    if state != "running":
        return f"error: lock failed: {state}"
    who = f" (pid {holder_pid})" if holder_pid else ""
    display_path = _display_lock_path(path)
    return "\n".join(
        [
            f"error: takopi is already running{who}",
            "stop that instance before starting another one — two bridges on the "
            "same token fight over getUpdates and every message is handled twice",
            f"the lock is held by the OS and released when that process exits, so "
            f"deleting {display_path} will not help",
        ]
    )


def _display_lock_path(path: Path) -> str:
    home = Path.home()
    try:
        resolved = path.expanduser().resolve()
        rel = resolved.relative_to(home)
        return f"~/{rel}"
    except ValueError, OSError:
        return str(path)
