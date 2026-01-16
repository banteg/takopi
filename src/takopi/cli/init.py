from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer

from ..config import ConfigError, write_config
from ..config_migrations import migrate_config
from ..ids import RESERVED_CHAT_COMMANDS
from ..settings import TakopiSettings, validate_settings_data
from .config import _config_path_display


def _prompt_alias(value: str | None, *, default_alias: str | None = None) -> str:
    if value is not None:
        alias = value
    elif default_alias:
        alias = typer.prompt("project alias", default=default_alias)
    else:
        alias = typer.prompt("project alias")
    alias = alias.strip()
    if not alias:
        typer.echo("error: project alias cannot be empty", err=True)
        raise typer.Exit(code=1)
    return alias


def _default_alias_from_path(path: Path) -> str | None:
    name = path.name
    if not name:
        return None
    name = name.removesuffix(".git")
    return name or None


def _ensure_projects_table(config: dict, config_path: Path) -> dict:
    projects = config.setdefault("projects", {})
    if not isinstance(projects, dict):
        raise ConfigError(f"Invalid `projects` in {config_path}; expected a table.")
    return projects


def run_init(
    *,
    alias: str | None,
    default: bool,
    load_or_init_config_fn: Callable[[], tuple[dict, Path]],
    resolve_main_worktree_root_fn: Callable[[Path], Path | None],
    resolve_default_base_fn: Callable[[Path], str | None],
    list_backend_ids_fn: Callable[..., list[str]],
    resolve_plugins_allowlist_fn: Callable[[TakopiSettings], list[str] | None],
) -> None:
    config, config_path = load_or_init_config_fn()
    if config_path.exists():
        applied = migrate_config(config, config_path=config_path)
        if applied:
            write_config(config, config_path)

    cwd = Path.cwd()
    project_path = resolve_main_worktree_root_fn(cwd) or cwd
    default_alias = _default_alias_from_path(project_path)
    alias = _prompt_alias(alias, default_alias=default_alias)

    settings = validate_settings_data(config, config_path=config_path)
    allowlist = resolve_plugins_allowlist_fn(settings)
    engine_ids = list_backend_ids_fn(allowlist=allowlist)
    projects_cfg = settings.to_projects_config(
        config_path=config_path,
        engine_ids=engine_ids,
        reserved=RESERVED_CHAT_COMMANDS,
    )

    alias_key = alias.lower()
    if alias_key in {engine.lower() for engine in engine_ids}:
        raise ConfigError(
            f"Invalid project alias {alias!r}; aliases must not match engine ids."
        )
    if alias_key in RESERVED_CHAT_COMMANDS:
        raise ConfigError(
            f"Invalid project alias {alias!r}; aliases must not match reserved commands."
        )

    existing = projects_cfg.projects.get(alias_key)
    if existing is not None:
        overwrite = typer.confirm(
            f"project {existing.alias!r} already exists, overwrite?",
            default=False,
        )
        if not overwrite:
            raise typer.Exit(code=1)

    projects = _ensure_projects_table(config, config_path)
    if existing is not None and existing.alias in projects:
        projects.pop(existing.alias, None)

    default_engine = settings.default_engine
    worktree_base = resolve_default_base_fn(project_path)

    entry: dict[str, object] = {
        "path": str(project_path),
        "worktrees_dir": ".worktrees",
        "default_engine": default_engine,
    }
    if worktree_base:
        entry["worktree_base"] = worktree_base

    projects[alias] = entry
    if default:
        config["default_project"] = alias

    write_config(config, config_path)
    typer.echo(f"saved project {alias!r} to {_config_path_display(config_path)}")
