from __future__ import annotations

import os
from pathlib import Path


def relativize_path(value: str, *, base_dir: Path | None = None) -> str:
    if not value:
        return value
    base = Path.cwd() if base_dir is None else base_dir
    base_str = str(base)
    if not base_str:
        return value
    if value == base_str:
        return "."
    if value.startswith(base_str):
        suffix = value[len(base_str) :]
        if suffix.startswith((os.sep, "/")):
            suffix = suffix[1:]
        return suffix or "."
    return value


def relativize_command(value: str, *, base_dir: Path | None = None) -> str:
    if not value:
        return value
    base = Path.cwd() if base_dir is None else base_dir
    base_str = str(base)
    if not base_str:
        return value
    base_with_sep = f"{base_str}{os.sep}"
    if base_with_sep in value:
        return value.replace(base_with_sep, "")
    if value == base_str:
        return "."
    return value
