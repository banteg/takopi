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
    for sep in (os.sep, "/"):
        prefix = base_str if base_str.endswith(sep) else f"{base_str}{sep}"
        if value.startswith(prefix):
            suffix = value[len(prefix) :]
            return suffix or "."
    return value


def relativize_command(value: str, *, base_dir: Path | None = None) -> str:
    base = Path.cwd() if base_dir is None else base_dir
    base_with_sep = f"{base}{os.sep}"
    return value.replace(base_with_sep, "")
