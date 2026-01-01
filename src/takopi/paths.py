from __future__ import annotations

from pathlib import Path


def relativize_path(path: str, *, base_dir: Path | None = None) -> str:
    raw = path.strip()
    if raw.startswith("./"):
        raw = raw[2:]

    base = Path.cwd() if base_dir is None else base_dir
    try:
        raw_path = Path(raw)
    except Exception:
        return raw

    if raw_path.is_absolute():
        try:
            raw_path = raw_path.relative_to(base)
            raw = raw_path.as_posix()
        except Exception:
            pass

    return raw


def relativize_command(command: str, *, base_dir: Path | None = None) -> str:
    if not command:
        return command
    base = Path.cwd() if base_dir is None else base_dir
    base_str = str(base)
    if not base_str:
        return command
    return command.replace(base_str, ".")
