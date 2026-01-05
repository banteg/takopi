from __future__ import annotations

import tomllib
from pathlib import Path

from ..config import ConfigError

HOME_CONFIG_PATH = Path.home() / ".takopi" / "takopi.toml"


def _read_config(cfg_path: Path) -> dict:
    try:
        raw = cfg_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"Missing config file {cfg_path}.") from None
    except OSError as e:
        raise ConfigError(f"Failed to read config file {cfg_path}: {e}") from e
    try:
        return tomllib.loads(raw)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Malformed TOML in {cfg_path}: {e}") from None


def load_telegram_config(path: str | Path | None = None) -> tuple[dict, Path]:
    if path:
        cfg_path = Path(path).expanduser()
        return _read_config(cfg_path), cfg_path
    cfg_path = HOME_CONFIG_PATH
    if cfg_path.exists() and not cfg_path.is_file():
        raise ConfigError(f"Config path {cfg_path} exists but is not a file.") from None
    return _read_config(cfg_path), cfg_path
