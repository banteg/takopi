from __future__ import annotations

import tomllib
from pathlib import Path

from .constants import HOME_CONFIG_PATH, LOCAL_CONFIG_NAME


class ConfigError(RuntimeError):
    pass


_EXAMPLE_CONFIG = (
    'bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"\n'
    "chat_id = 123456789\n"
)


def _display_path(path: Path) -> str:
    try:
        cwd = Path.cwd()
        if path.is_relative_to(cwd):
            return f"./{path.relative_to(cwd).as_posix()}"
        home = Path.home()
        if path.is_relative_to(home):
            return f"~/{path.relative_to(home).as_posix()}"
    except Exception:
        return str(path)
    return str(path)


def _missing_config_message(primary: Path, alternate: Path | None = None) -> str:
    example = "Example config:\n```\n" + _EXAMPLE_CONFIG + "```\n"
    if alternate is None:
        return (
            f"Missing config file `{_display_path(primary)}`.\n"
            f"{example}"
        )
    return (
        "Missing takopi config.\n"
        "Create one of these files:\n"
        f"  {_display_path(alternate)}\n"
        f"  {_display_path(primary)}\n"
        "\n"
        f"{example}"
    )


def _config_candidates(base_dir: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if base_dir is not None:
        candidates.append(base_dir / LOCAL_CONFIG_NAME)

    cwd = Path.cwd()
    if base_dir is None or base_dir != cwd:
        candidates.append(cwd / LOCAL_CONFIG_NAME)

    candidates.append(HOME_CONFIG_PATH)
    return candidates


def _read_config(cfg_path: Path) -> dict:
    try:
        raw = cfg_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(_missing_config_message(cfg_path)) from None
    except OSError as e:
        raise ConfigError(f"Failed to read config file {cfg_path}: {e}") from e
    try:
        return tomllib.loads(raw)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Malformed TOML in {cfg_path}: {e}") from None


def load_telegram_config(
    path: str | Path | None = None, *, base_dir: str | Path | None = None
) -> tuple[dict, Path]:
    if path:
        cfg_path = Path(path).expanduser()
        return _read_config(cfg_path), cfg_path

    base = Path(base_dir).expanduser() if base_dir is not None else None
    candidates = _config_candidates(base)
    for candidate in candidates:
        if candidate.is_file():
            return _read_config(candidate), candidate

    raise ConfigError(_missing_config_message(HOME_CONFIG_PATH, candidates[0]))
