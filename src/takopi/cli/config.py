from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel

from ..config import (
    ConfigError,
    HOME_CONFIG_PATH,
    dump_toml,
    read_config,
    write_config,
)
from ..config_migrations import migrate_config
from ..settings import TakopiSettings, validate_settings_data

_KEY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MISSING = object()
_CONFIG_PATH_OPTION = typer.Option(
    None,
    "--config-path",
    help="Override the default config path.",
)


def _config_path_display(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def _fail_missing_config(path: Path) -> None:
    display = _config_path_display(path)
    if path.exists():
        typer.echo(f"error: invalid takopi config at {display}", err=True)
    else:
        typer.echo(f"error: missing takopi config at {display}", err=True)


def _resolve_config_path_override(value: Path | None) -> Path:
    if value is None:
        return _resolve_home_config_path()
    return value.expanduser()


def _resolve_home_config_path() -> Path:
    cli_module = sys.modules.get("takopi.cli")
    if cli_module is not None:
        override = getattr(cli_module, "HOME_CONFIG_PATH", None)
        if override is not None:
            return Path(override)
    return HOME_CONFIG_PATH


def _exit_config_error(exc: ConfigError, *, code: int = 2) -> None:
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(code=code) from exc


def _parse_key_path(raw: str) -> list[str]:
    value = raw.strip()
    if not value:
        raise ConfigError("Invalid key path; expected a non-empty value.")
    segments = value.split(".")
    for segment in segments:
        if not segment:
            raise ConfigError(f"Invalid key path {raw!r}; empty segment.")
        if not _KEY_SEGMENT_RE.fullmatch(segment):
            raise ConfigError(
                f"Invalid key segment {segment!r} in {raw!r}; "
                "use only letters, numbers, '_' or '-'."
            )
    return segments


def _parse_value(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    try:
        return tomllib.loads(f"__v__ = {value}")["__v__"]
    except tomllib.TOMLDecodeError:
        return value


def _toml_literal(value: Any) -> str:
    dumped = dump_toml({"__v__": value})
    prefix = "__v__ = "
    if dumped.startswith(prefix):
        return dumped[len(prefix) :].rstrip("\n")
    raise ConfigError("Unsupported config value; unable to render TOML literal.")


def _normalized_value_from_settings(
    settings: TakopiSettings, segments: list[str]
) -> Any:
    node: Any = settings
    for segment in segments:
        if isinstance(node, BaseModel):
            if segment in node.__class__.model_fields:
                node = getattr(node, segment)
            else:
                extra = node.model_extra or {}
                node = extra.get(segment, _MISSING)
        elif isinstance(node, dict):
            node = node.get(segment, _MISSING)
        else:
            return _MISSING
        if node is _MISSING:
            return _MISSING
    if isinstance(node, BaseModel):
        return node.model_dump(exclude_unset=True)
    return node


def _flatten_config(config: dict[str, Any]) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []

    def _walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for key in sorted(node):
                value = node[key]
                path = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    _walk(value, path)
                else:
                    items.append((path, value))
        elif prefix:
            items.append((prefix, node))

    _walk(config, "")
    return items


def _load_config_or_exit(path: Path, *, missing_code: int) -> dict[str, Any]:
    if not path.exists():
        _fail_missing_config(path)
        raise typer.Exit(code=missing_code)
    try:
        return read_config(path)
    except ConfigError as exc:
        _exit_config_error(exc)
    return {}


def config_path_cmd(
    config_path: Path | None = _CONFIG_PATH_OPTION,
) -> None:
    """Print the resolved config path."""
    path = _resolve_config_path_override(config_path)
    typer.echo(_config_path_display(path))


def config_list(
    config_path: Path | None = _CONFIG_PATH_OPTION,
) -> None:
    """List config keys as flattened dot-paths."""
    path = _resolve_config_path_override(config_path)
    config = _load_config_or_exit(path, missing_code=1)
    try:
        for key, value in _flatten_config(config):
            literal = _toml_literal(value)
            typer.echo(f"{key} = {literal}")
    except ConfigError as exc:
        _exit_config_error(exc)


def config_get(
    key: str = typer.Argument(..., help="Dot-path key to fetch."),
    config_path: Path | None = _CONFIG_PATH_OPTION,
) -> None:
    """Fetch a single config key."""
    path = _resolve_config_path_override(config_path)
    config = _load_config_or_exit(path, missing_code=2)
    try:
        segments = _parse_key_path(key)
    except ConfigError as exc:
        _exit_config_error(exc)

    node: Any = config
    for index, segment in enumerate(segments):
        if not isinstance(node, dict):
            prefix = ".".join(segments[:index])
            _exit_config_error(
                ConfigError(f"Invalid `{prefix}` in {path}; expected a table.")
            )
        if segment not in node:
            raise typer.Exit(code=1)
        node = node[segment]

    if isinstance(node, dict):
        typer.echo(
            f"error: {'.'.join(segments)!r} is a table; pick a leaf node.",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        typer.echo(_toml_literal(node))
    except ConfigError as exc:
        _exit_config_error(exc)


def config_set(
    key: str = typer.Argument(..., help="Dot-path key to set."),
    value: str = typer.Argument(..., help="Value to assign (auto-parsed)."),
    config_path: Path | None = _CONFIG_PATH_OPTION,
) -> None:
    """Set a config value."""
    path = _resolve_config_path_override(config_path)
    config = _load_config_or_exit(path, missing_code=2)
    try:
        segments = _parse_key_path(key)
    except ConfigError as exc:
        _exit_config_error(exc)

    try:
        migrate_config(config, config_path=path)
    except ConfigError as exc:
        _exit_config_error(exc)

    parsed = _parse_value(value)
    node: Any = config
    for index, segment in enumerate(segments[:-1]):
        next_node = node.get(segment)
        if next_node is None:
            created: dict[str, Any] = {}
            node[segment] = created
            node = created
            continue
        if not isinstance(next_node, dict):
            prefix = ".".join(segments[: index + 1])
            _exit_config_error(
                ConfigError(f"Invalid `{prefix}` in {path}; expected a table.")
            )
        node = next_node
    node[segments[-1]] = parsed

    try:
        settings = validate_settings_data(config, config_path=path)
    except ConfigError as exc:
        _exit_config_error(exc)

    normalized = _normalized_value_from_settings(settings, segments)
    if normalized is not _MISSING:
        node[segments[-1]] = normalized
        parsed = normalized

    try:
        write_config(config, path)
    except ConfigError as exc:
        _exit_config_error(exc)

    rendered = _toml_literal(parsed)
    typer.echo(f"updated {'.'.join(segments)} = {rendered}")


def config_unset(
    key: str = typer.Argument(..., help="Dot-path key to remove."),
    config_path: Path | None = _CONFIG_PATH_OPTION,
) -> None:
    """Remove a config key."""
    path = _resolve_config_path_override(config_path)
    config = _load_config_or_exit(path, missing_code=2)
    try:
        segments = _parse_key_path(key)
    except ConfigError as exc:
        _exit_config_error(exc)

    try:
        migrate_config(config, config_path=path)
    except ConfigError as exc:
        _exit_config_error(exc)

    node: Any = config
    stack: list[tuple[dict[str, Any], str]] = []
    for index, segment in enumerate(segments[:-1]):
        if not isinstance(node, dict):
            prefix = ".".join(segments[:index])
            _exit_config_error(
                ConfigError(f"Invalid `{prefix}` in {path}; expected a table.")
            )
        next_node = node.get(segment)
        if next_node is None:
            raise typer.Exit(code=1)
        if not isinstance(next_node, dict):
            prefix = ".".join(segments[: index + 1])
            _exit_config_error(
                ConfigError(f"Invalid `{prefix}` in {path}; expected a table.")
            )
        stack.append((node, segment))
        node = next_node

    if not isinstance(node, dict):
        prefix = ".".join(segments[:-1])
        _exit_config_error(
            ConfigError(f"Invalid `{prefix}` in {path}; expected a table.")
        )
    leaf = segments[-1]
    if leaf not in node:
        raise typer.Exit(code=1)
    node.pop(leaf, None)

    while stack and not node:
        parent, key_name = stack.pop()
        parent.pop(key_name, None)
        node = parent

    try:
        validate_settings_data(config, config_path=path)
        write_config(config, path)
    except ConfigError as exc:
        _exit_config_error(exc)
