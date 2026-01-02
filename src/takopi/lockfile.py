from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_VERSION = 1


@dataclass(frozen=True)
class LockInfo:
    version: int
    instance_id: str | None
    pid: int | None
    started_at: str | None
    hostname: str | None
    config_path: str | None
    token_fingerprint: str | None
    argv: list[str] | None


class LockError(RuntimeError):
    def __init__(
        self,
        *,
        path: Path,
        existing: LockInfo | None,
        state: str,
    ) -> None:
        self.path = path
        self.existing = existing
        self.state = state
        super().__init__(_format_lock_message(path, existing, state))


@dataclass
class LockHandle:
    path: Path
    instance_id: str

    def release(self) -> None:
        try:
            existing = _read_lock_info(self.path)
            if existing is None or existing.instance_id == self.instance_id:
                self.path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("[lock] failed to remove lock file %s: %s", self.path, exc)

    def __enter__(self) -> "LockHandle":
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
    instance_id = uuid.uuid4().hex
    info = LockInfo(
        version=LOCK_VERSION,
        instance_id=instance_id,
        pid=os.getpid(),
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        hostname=socket.gethostname(),
        config_path=str(cfg_path),
        token_fingerprint=token_fingerprint,
        argv=list(sys.argv),
    )
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        existing = _read_lock_info(lock_path)
        state = _lock_state(existing)
        raise LockError(path=lock_path, existing=existing, state=state) from None
    except OSError as exc:
        raise LockError(path=lock_path, existing=None, state=str(exc)) from exc

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(info), handle, indent=2, sort_keys=True)
        handle.write("\n")

    return LockHandle(path=lock_path, instance_id=instance_id)


def _read_lock_info(path: Path) -> LockInfo | None:
    try:
        raw = path.read_text(encoding="utf-8")
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
    instance_id = data.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id.strip():
        instance_id = None
    started_at = data.get("started_at")
    if not isinstance(started_at, str):
        started_at = None
    hostname = data.get("hostname")
    if not isinstance(hostname, str):
        hostname = None
    config_path = data.get("config_path")
    if not isinstance(config_path, str):
        config_path = None
    token_hint = data.get("token_fingerprint")
    if not isinstance(token_hint, str):
        token_hint = None
    argv = data.get("argv")
    if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
        argv = None
    version = data.get("version")
    if not isinstance(version, int):
        version = 0
    return LockInfo(
        version=version,
        instance_id=instance_id,
        pid=pid,
        started_at=started_at,
        hostname=hostname,
        config_path=config_path,
        token_fingerprint=token_hint,
        argv=argv,
    )


def _pid_state(pid: int | None) -> str:
    if pid is None or pid <= 0:
        return "unknown"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "not_running"
    except PermissionError:
        return "running"
    except OSError:
        return "unknown"
    return "running"


def _lock_state(existing: LockInfo | None) -> str:
    if existing is None:
        return "unknown"
    hostname = existing.hostname
    if hostname and hostname != socket.gethostname():
        return "unknown"
    state = _pid_state(existing.pid)
    if state == "not_running":
        return "stale"
    if state == "running":
        return "running"
    return "unknown"


def _format_lock_message(path: Path, existing: LockInfo | None, state: str) -> str:
    if state not in {"stale", "running", "unknown"}:
        return f"failed to create lock: {state}"
    header = "another takopi instance may already be running for this bot."
    if state == "running":
        header = "another takopi instance is already running for this bot."
    display_path = _display_lock_path(path)
    lines = [
        header,
        f"if you are sure that's not the case, delete {display_path}",
    ]
    return "\n".join(lines)


def _display_lock_path(path: Path) -> str:
    home = Path.home()
    try:
        resolved = path.expanduser().resolve()
        rel = resolved.relative_to(home)
        return f"~/{rel}"
    except (ValueError, OSError):
        return str(path)
