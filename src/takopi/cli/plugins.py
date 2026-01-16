from __future__ import annotations

import sys
from collections.abc import Callable
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import cast

import typer

from ..commands import get_command
from ..config import ConfigError
from ..engines import get_backend
from ..ids import RESERVED_COMMAND_IDS, RESERVED_ENGINE_IDS
from ..plugins import (
    COMMAND_GROUP,
    ENGINE_GROUP,
    PluginLoadError,
    TRANSPORT_GROUP,
    entrypoint_distribution_name,
    get_load_errors,
    is_entrypoint_allowed,
    list_entrypoints,
    normalize_allowlist,
)
from ..runtime_loader import resolve_plugins_allowlist
from ..settings import TakopiSettings, load_settings_if_exists
from ..transports import get_transport


def _load_settings_optional() -> tuple[TakopiSettings | None, Path | None]:
    try:
        loaded = load_settings_if_exists()
    except ConfigError:
        return None, None
    if loaded is None:
        return None, None
    return loaded


def _print_entrypoints(
    label: str,
    entrypoints: list[EntryPoint],
    *,
    allowlist: set[str] | None,
    entrypoint_distribution_name_fn: Callable[[EntryPoint], str | None],
    is_entrypoint_allowed_fn: Callable[[EntryPoint, set[str] | None], bool],
) -> None:
    typer.echo(f"{label}:")
    if not entrypoints:
        typer.echo("  (none)")
        return
    for ep in entrypoints:
        dist = entrypoint_distribution_name_fn(ep) or "unknown"
        status = ""
        if allowlist is not None:
            allowed = is_entrypoint_allowed_fn(ep, allowlist)
            status = " enabled" if allowed else " disabled"
        typer.echo(f"  {ep.name} ({dist}){status}")


def plugins_cmd(
    load: bool = typer.Option(
        False,
        "--load/--no-load",
        help="Load plugins to validate and surface import errors.",
    ),
) -> None:
    """List discovered plugins and optionally validate them."""
    load_settings_optional = cast(
        Callable[[], tuple[TakopiSettings | None, Path | None]],
        _resolve_cli_attr("_load_settings_optional") or _load_settings_optional,
    )
    resolve_plugins_allowlist_fn = cast(
        Callable[[TakopiSettings | None], list[str] | None],
        _resolve_cli_attr("resolve_plugins_allowlist") or resolve_plugins_allowlist,
    )
    list_entrypoints_fn = cast(
        Callable[..., list[EntryPoint]],
        _resolve_cli_attr("list_entrypoints") or list_entrypoints,
    )
    get_backend_fn = cast(
        Callable[..., object],
        _resolve_cli_attr("get_backend") or get_backend,
    )
    get_transport_fn = cast(
        Callable[..., object],
        _resolve_cli_attr("get_transport") or get_transport,
    )
    get_command_fn = cast(
        Callable[..., object],
        _resolve_cli_attr("get_command") or get_command,
    )
    get_load_errors_fn = cast(
        Callable[[], tuple[PluginLoadError, ...]],
        _resolve_cli_attr("get_load_errors") or get_load_errors,
    )
    entrypoint_distribution_name_fn = cast(
        Callable[[EntryPoint], str | None],
        _resolve_cli_attr("entrypoint_distribution_name")
        or entrypoint_distribution_name,
    )
    is_entrypoint_allowed_fn = cast(
        Callable[[EntryPoint, set[str] | None], bool],
        _resolve_cli_attr("is_entrypoint_allowed") or is_entrypoint_allowed,
    )
    normalize_allowlist_fn = cast(
        Callable[[list[str] | None], set[str] | None],
        _resolve_cli_attr("normalize_allowlist") or normalize_allowlist,
    )

    settings_hint, _ = load_settings_optional()
    allowlist = resolve_plugins_allowlist_fn(settings_hint)

    allowlist_set = normalize_allowlist_fn(allowlist)
    engine_eps = list_entrypoints_fn(
        ENGINE_GROUP,
        reserved_ids=RESERVED_ENGINE_IDS,
    )
    transport_eps = list_entrypoints_fn(TRANSPORT_GROUP)
    command_eps = list_entrypoints_fn(
        COMMAND_GROUP,
        reserved_ids=RESERVED_COMMAND_IDS,
    )

    _print_entrypoints(
        "engine backends",
        engine_eps,
        allowlist=allowlist_set,
        entrypoint_distribution_name_fn=entrypoint_distribution_name_fn,
        is_entrypoint_allowed_fn=is_entrypoint_allowed_fn,
    )
    _print_entrypoints(
        "transport backends",
        transport_eps,
        allowlist=allowlist_set,
        entrypoint_distribution_name_fn=entrypoint_distribution_name_fn,
        is_entrypoint_allowed_fn=is_entrypoint_allowed_fn,
    )
    _print_entrypoints(
        "command backends",
        command_eps,
        allowlist=allowlist_set,
        entrypoint_distribution_name_fn=entrypoint_distribution_name_fn,
        is_entrypoint_allowed_fn=is_entrypoint_allowed_fn,
    )

    if load:
        for ep in engine_eps:
            if allowlist_set is not None and not is_entrypoint_allowed_fn(
                ep, allowlist_set
            ):
                continue
            try:
                get_backend_fn(ep.name, allowlist=allowlist)
            except ConfigError:
                continue
        for ep in transport_eps:
            if allowlist_set is not None and not is_entrypoint_allowed_fn(
                ep, allowlist_set
            ):
                continue
            try:
                get_transport_fn(ep.name, allowlist=allowlist)
            except ConfigError:
                continue
        for ep in command_eps:
            if allowlist_set is not None and not is_entrypoint_allowed_fn(
                ep, allowlist_set
            ):
                continue
            try:
                get_command_fn(ep.name, allowlist=allowlist)
            except ConfigError:
                continue

    errors = get_load_errors_fn()
    if errors:
        typer.echo("errors:")
        for err in errors:
            group = err.group
            if group == ENGINE_GROUP:
                group = "engine"
            elif group == TRANSPORT_GROUP:
                group = "transport"
            elif group == COMMAND_GROUP:
                group = "command"
            dist = err.distribution or "unknown"
            typer.echo(f"  {group} {err.name} ({dist}): {err.error}")


def _resolve_cli_attr(name: str) -> object | None:
    cli_module = sys.modules.get("takopi.cli")
    if cli_module is None:
        return None
    return getattr(cli_module, name, None)
