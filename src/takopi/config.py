from __future__ import annotations

import tomllib
from pathlib import Path

LOCAL_CONFIG_NAME = Path(".takopi") / "takopi.toml"
HOME_CONFIG_PATH = Path.home() / ".takopi" / "takopi.toml"


class ConfigError(RuntimeError):
    pass


def _config_candidates() -> list[Path]:
    candidates = [Path.cwd() / LOCAL_CONFIG_NAME, HOME_CONFIG_PATH]
    if candidates[0] == candidates[1]:
        return [candidates[0]]
    return candidates


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

    config_candidates = _config_candidates()
    for candidate in config_candidates:
        if candidate.exists() and not candidate.is_file():
            raise ConfigError(
                f"Config path {candidate} exists but is not a file."
            ) from None
        if candidate.is_file():
            return _read_config(candidate), candidate
    checked_display = ", ".join(str(candidate) for candidate in config_candidates)
    raise ConfigError(f"Missing takopi config. Checked: {checked_display}")
