from __future__ import annotations

import tomllib
from pathlib import Path

from .model import Workspace

HOME_CONFIG_PATH = Path.home() / ".takopi" / "takopi.toml"


class ConfigError(RuntimeError):
    pass


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


def parse_workspaces(
    config: dict, config_path: Path, *, validate_paths: bool = True
) -> list[Workspace]:
    workspaces_section = config.get("workspaces")
    if workspaces_section is None:
        return []
    if not isinstance(workspaces_section, dict):
        raise ConfigError(f"Invalid `workspaces` in {config_path}; expected a table.")

    workspaces: list[Workspace] = []
    for name, path_str in workspaces_section.items():
        if not isinstance(path_str, str) or not path_str.strip():
            raise ConfigError(
                f"Invalid workspace path for {name!r} in {config_path}; "
                "expected a non-empty string."
            )
        path = Path(path_str).expanduser().resolve()
        if validate_paths and not path.is_dir():
            raise ConfigError(
                f"Workspace {name!r} path does not exist or is not a directory: {path}"
            )
        workspaces.append(Workspace(name=name, path=path))

    return workspaces


def get_default_workspace(
    config: dict, config_path: Path, workspaces: list[Workspace]
) -> str | None:
    default = config.get("default_workspace")
    if default is None:
        return None
    if not isinstance(default, str) or not default.strip():
        raise ConfigError(
            f"Invalid `default_workspace` in {config_path}; expected a non-empty string."
        )
    default = default.strip()
    workspace_names = {ws.name for ws in workspaces}
    if default not in workspace_names:
        available = ", ".join(sorted(workspace_names)) or "none"
        raise ConfigError(
            f"Unknown default workspace {default!r}. Available: {available}."
        )
    return default
