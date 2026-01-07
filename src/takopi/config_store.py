from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .config import ConfigError, dump_toml


def read_raw_toml(path: Path) -> dict[str, Any]:
    if path.exists() and not path.is_file():
        raise ConfigError(f"Config path {path} exists but is not a file.") from None
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"Missing config file {path}.") from None
    except OSError as exc:
        raise ConfigError(f"Failed to read config file {path}: {exc}") from exc
    try:
        return tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Malformed TOML in {path}: {exc}") from None


def write_raw_toml(config: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_toml(config), encoding="utf-8")
