from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

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


def load_or_init_config(path: str | Path | None = None) -> tuple[dict, Path]:
    cfg_path = Path(path).expanduser() if path else HOME_CONFIG_PATH
    if cfg_path.exists() and not cfg_path.is_file():
        raise ConfigError(f"Config path {cfg_path} exists but is not a file.") from None
    if not cfg_path.exists():
        return {}, cfg_path
    return _read_config(cfg_path), cfg_path


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


def empty_projects_config() -> ProjectsConfig:
    return ProjectsConfig(projects={}, default_project=None)


def _normalize_engine_id(
    value: str,
    *,
    engine_ids: Iterable[str],
    config_path: Path,
    label: str,
) -> str:
    engine_map = {engine.lower(): engine for engine in engine_ids}
    cleaned = value.strip()
    if not cleaned:
        raise ConfigError(f"Invalid `{label}` in {config_path}; expected a string.")
    engine = engine_map.get(cleaned.lower())
    if engine is None:
        available = ", ".join(sorted(engine_map.values()))
        raise ConfigError(
            f"Unknown `{label}` {cleaned!r} in {config_path}. Available: {available}."
        )
    return engine


def _normalize_project_path(value: str, *, config_path: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path


def parse_projects_config(
    config: dict[str, Any],
    *,
    config_path: Path,
    engine_ids: Iterable[str],
    reserved: Iterable[str] = ("cancel",),
    default_chat_id: int | None = None,
) -> ProjectsConfig:
    default_project_raw = config.get("default_project")
    default_project = None
    if default_project_raw is not None:
        if not isinstance(default_project_raw, str) or not default_project_raw.strip():
            raise ConfigError(
                f"Invalid `default_project` in {config_path}; expected a non-empty string."
            )
        default_project = default_project_raw.strip()

    projects_raw = config.get("projects") or {}
    if not isinstance(projects_raw, dict):
        raise ConfigError(f"Invalid `projects` in {config_path}; expected a table.")

    reserved_lower = {value.lower() for value in reserved}
    engine_lower = {value.lower() for value in engine_ids}
    projects: dict[str, ProjectConfig] = {}
    chat_map: dict[int, str] = {}

    for raw_alias, raw_entry in projects_raw.items():
        if not isinstance(raw_alias, str) or not raw_alias.strip():
            raise ConfigError(
                f"Invalid project alias in {config_path}; expected a non-empty string."
            )
        alias = raw_alias.strip()
        alias_key = alias.lower()
        if alias_key in engine_lower or alias_key in reserved_lower:
            raise ConfigError(
                f"Invalid project alias {alias!r} in {config_path}; "
                "aliases must not match engine ids or reserved commands."
            )
        if alias_key in projects:
            raise ConfigError(f"Duplicate project alias {alias!r} in {config_path}.")
        if not isinstance(raw_entry, dict):
            raise ConfigError(
                f"Invalid project entry for {alias!r} in {config_path}; expected a table."
            )

        path_value = raw_entry.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise ConfigError(f"Missing `path` for project {alias!r} in {config_path}.")
        path = _normalize_project_path(path_value.strip(), config_path=config_path)

        worktrees_dir_raw = raw_entry.get("worktrees_dir", ".worktrees")
        if not isinstance(worktrees_dir_raw, str) or not worktrees_dir_raw.strip():
            raise ConfigError(
                f"Invalid `worktrees_dir` for project {alias!r} in {config_path}."
            )
        worktrees_dir = Path(worktrees_dir_raw.strip())

        default_engine_raw = raw_entry.get("default_engine")
        default_engine = None
        if default_engine_raw is not None:
            if not isinstance(default_engine_raw, str):
                raise ConfigError(
                    f"Invalid `projects.{alias}.default_engine` in {config_path}; "
                    "expected a string."
                )
            default_engine = _normalize_engine_id(
                default_engine_raw,
                engine_ids=engine_ids,
                config_path=config_path,
                label=f"projects.{alias}.default_engine",
            )

        worktree_base_raw = raw_entry.get("worktree_base")
        worktree_base = None
        if worktree_base_raw is not None:
            if not isinstance(worktree_base_raw, str) or not worktree_base_raw.strip():
                raise ConfigError(
                    f"Invalid `projects.{alias}.worktree_base` in {config_path}; "
                    "expected a string."
                )
            worktree_base = worktree_base_raw.strip()

        chat_id_raw = raw_entry.get("chat_id")
        chat_id = None
        if chat_id_raw is not None:
            if isinstance(chat_id_raw, bool) or not isinstance(chat_id_raw, int):
                raise ConfigError(
                    f"Invalid `projects.{alias}.chat_id` in {config_path}; "
                    "expected an integer."
                )
            chat_id = chat_id_raw
            if default_chat_id is not None and chat_id == default_chat_id:
                raise ConfigError(
                    f"Invalid `projects.{alias}.chat_id` in {config_path}; "
                    "must not match transports.telegram.chat_id."
                )
            if chat_id in chat_map:
                existing = chat_map[chat_id]
                raise ConfigError(
                    f"Duplicate `projects.*.chat_id` {chat_id} in {config_path}; "
                    f"already used by {existing!r}."
                )
            chat_map[chat_id] = alias_key

        projects[alias_key] = ProjectConfig(
            alias=alias,
            path=path,
            worktrees_dir=worktrees_dir,
            default_engine=default_engine,
            worktree_base=worktree_base,
            chat_id=chat_id,
        )

    if default_project is not None:
        default_key = default_project.lower()
        if default_key not in projects:
            raise ConfigError(
                f"Invalid `default_project` {default_project!r} in {config_path}; "
                "no matching project alias found."
            )
        default_project = default_key

    return ProjectsConfig(
        projects=projects,
        default_project=default_project,
        chat_map=chat_map,
    )


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
