from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable, Iterable

from watchfiles import awatch

from .config import ConfigError
from .ids import RESERVED_CHAT_COMMANDS
from .logging import get_logger
from .runtime_loader import RuntimeSpec, build_runtime_spec
from .settings import TakopiSettings, load_settings
from .transport_runtime import TransportRuntime

logger = get_logger(__name__)

__all__ = [
    "ConfigReload",
    "config_status",
    "watch_config",
]


@dataclass(frozen=True, slots=True)
class ConfigReload:
    settings: TakopiSettings
    runtime_spec: RuntimeSpec
    config_path: Path


def config_status(path: Path) -> tuple[str, tuple[int, int] | None]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "missing", None
    if not path.is_file():
        return "invalid", None
    return "ok", (stat.st_mtime_ns, stat.st_size)


def _matches_config_path(candidate: str, config_path: Path) -> bool:
    try:
        return Path(candidate).resolve(strict=False) == config_path
    except OSError:
        return False


def _reload_config(
    config_path: Path,
    default_engine_override: str | None,
    reserved: tuple[str, ...],
) -> ConfigReload:
    settings, resolved_path = load_settings(config_path)
    spec = build_runtime_spec(
        settings=settings,
        config_path=resolved_path,
        default_engine_override=default_engine_override,
        reserved=reserved,
    )
    return ConfigReload(
        settings=settings,
        runtime_spec=spec,
        config_path=resolved_path,
    )


async def watch_config(
    *,
    config_path: Path,
    runtime: TransportRuntime,
    default_engine_override: str | None = None,
    reserved: Iterable[str] = RESERVED_CHAT_COMMANDS,
    on_reload: Callable[[ConfigReload], Awaitable[None]] | None = None,
) -> None:
    # Mutmut sets MUTANT_UNDER_TEST; disable watchers to avoid native crashes.
    if os.environ.get("MUTANT_UNDER_TEST"):
        return
    reserved_tuple = tuple(reserved)
    config_path = config_path.expanduser().resolve()
    watch_root = config_path.parent
    status, signature = config_status(config_path)
    last_status = status
    if status != "ok":
        logger.warning("config.watch.unavailable", path=str(config_path), status=status)

    async for changes in awatch(watch_root):
        if not any(_matches_config_path(path, config_path) for _, path in changes):
            continue

        status, current = config_status(config_path)
        if status != "ok":
            if status != last_status:
                logger.warning(
                    "config.watch.unavailable",
                    path=str(config_path),
                    status=status,
                )
            last_status = status
            signature = None
            continue

        if last_status != "ok":
            logger.info("config.watch.available", path=str(config_path))
        last_status = status

        if current == signature:
            continue

        try:
            reload = _reload_config(
                config_path,
                default_engine_override,
                reserved_tuple,
            )
        except ConfigError as exc:
            logger.warning("config.reload.failed", error=str(exc))
            signature = current
            continue
        except Exception as exc:  # pragma: no cover - safety net
            logger.exception(
                "config.reload.crashed",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            signature = current
            continue

        reload.runtime_spec.apply(runtime, config_path=reload.config_path)
        logger.info("config.reload.applied", path=str(reload.config_path))
        if on_reload is not None:
            try:
                await on_reload(reload)
            except Exception as exc:  # pragma: no cover - safety net
                logger.exception(
                    "config.reload.callback_failed",
                    error=str(exc),
                    error_type=exc.__class__.__name__,
                )

        _, signature = config_status(config_path)
        if signature is None:
            signature = current
