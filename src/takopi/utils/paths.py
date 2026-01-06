from __future__ import annotations

import os
from contextvars import ContextVar, Token
from pathlib import Path


_run_base_dir: ContextVar[Path | None] = ContextVar("takopi_run_base_dir", default=None)


def get_run_base_dir() -> Path | None:
    return _run_base_dir.get()


def set_run_base_dir(base_dir: Path | None) -> Token[Path | None]:
    return _run_base_dir.set(base_dir)


def reset_run_base_dir(token: Token[Path | None]) -> None:
    _run_base_dir.reset(token)


def relativize_path(value: str, *, base_dir: Path | None = None) -> str:
    if not value:
        return value
    base = get_run_base_dir() if base_dir is None else base_dir
    if base is None:
        base = Path.cwd()
    base_str = str(base)
    if not base_str:
        return value
    if value == base_str:
        return "."
    for sep in (os.sep, "/"):
        prefix = base_str if base_str.endswith(sep) else f"{base_str}{sep}"
        if value.startswith(prefix):
            suffix = value[len(prefix) :]
            return suffix or "."
    return value


def relativize_command(value: str, *, base_dir: Path | None = None) -> str:
    base = get_run_base_dir() if base_dir is None else base_dir
    if base is None:
        base = Path.cwd()
    base_with_sep = f"{base}{os.sep}"
    return value.replace(base_with_sep, "")
