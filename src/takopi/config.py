from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOME_CONFIG_PATH = Path.home() / ".takopi" / "takopi.toml"


class ConfigError(RuntimeError):
    pass


def ensure_table(
    config: dict[str, Any],
    key: str,
    *,
    config_path: Path,
    label: str | None = None,
) -> dict[str, Any]:
    value = config.get(key)
    if value is None:
        table: dict[str, Any] = {}
        config[key] = table
        return table
    if not isinstance(value, dict):
        name = label or key
        raise ConfigError(f"Invalid `{name}` in {config_path}; expected a table.")
    return value


def read_config(cfg_path: Path) -> dict:
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


def load_or_init_config(path: str | Path | None = None) -> tuple[dict, Path]:
    cfg_path = Path(path).expanduser() if path else HOME_CONFIG_PATH
    if cfg_path.exists() and not cfg_path.is_file():
        raise ConfigError(f"Config path {cfg_path} exists but is not a file.") from None
    if not cfg_path.exists():
        return {}, cfg_path
    return read_config(cfg_path), cfg_path


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    alias: str
    path: Path
    worktrees_dir: Path
    default_engine: str | None = None
    worktree_base: str | None = None
    chat_id: int | None = None

    @property
    def worktrees_root(self) -> Path:
        if self.worktrees_dir.is_absolute():
            return self.worktrees_dir
        return self.path / self.worktrees_dir


@dataclass(frozen=True, slots=True)
class ProjectsConfig:
    projects: dict[str, ProjectConfig]
    default_project: str | None = None
    chat_map: dict[int, str] = field(default_factory=dict)

    def resolve(self, alias: str | None) -> ProjectConfig | None:
        if alias is None:
            if self.default_project is None:
                return None
            return self.projects.get(self.default_project)
        return self.projects.get(alias.lower())

    def project_for_chat(self, chat_id: int | None) -> str | None:
        if chat_id is None:
            return None
        return self.chat_map.get(chat_id)

    def project_chat_ids(self) -> tuple[int, ...]:
        return tuple(self.chat_map.keys())


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, Path):
        return f'"{_toml_escape(str(value))}"'
    if isinstance(value, str):
        return f'"{_toml_escape(value)}"'
    if isinstance(value, (list, tuple)):
        inner = ", ".join(_format_toml_value(item) for item in value)
        return f"[{inner}]"
    raise ConfigError(f"Unsupported config value {value!r}")


def _table_has_scalars(table: dict[str, Any]) -> bool:
    return any(not isinstance(value, dict) for value in table.values())


def dump_toml(config: dict[str, Any]) -> str:
    lines: list[str] = []

    def write_kv(key: str, value: Any) -> None:
        lines.append(f"{key} = {_format_toml_value(value)}")

    def write_table(name: str, table: dict[str, Any]) -> None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"[{name}]")
        for key, value in table.items():
            if isinstance(value, dict):
                continue
            write_kv(key, value)
        for key, value in table.items():
            if isinstance(value, dict):
                write_table(f"{name}.{key}", value)

    for key, value in config.items():
        if isinstance(value, dict):
            continue
        write_kv(key, value)

    for key, value in config.items():
        if not isinstance(value, dict):
            continue
        if _table_has_scalars(value):
            write_table(key, value)
            continue
        for subkey, subvalue in value.items():
            if isinstance(subvalue, dict):
                write_table(f"{key}.{subkey}", subvalue)
            else:
                write_table(key, value)
                break

    return "\n".join(lines) + "\n"


def write_config(config: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_toml(config), encoding="utf-8")
