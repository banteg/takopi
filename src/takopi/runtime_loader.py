from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Iterable, Mapping

from .backends import EngineBackend
from .config import ConfigError, ProjectsConfig
from .engines import get_backend, list_backend_ids
from .ids import RESERVED_CHAT_COMMANDS
from .logging import get_logger
from .router import AutoRouter, EngineStatus, RunnerEntry
from .settings import TakopiSettings
from .transport_runtime import TransportRuntime

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeSpec:
    router: AutoRouter
    projects: ProjectsConfig
    allowlist: list[str] | None
    plugin_configs: Mapping[str, Any] | None
    watch_config: bool = False

    def to_runtime(self, *, config_path: Path | None) -> TransportRuntime:
        return TransportRuntime(
            router=self.router,
            projects=self.projects,
            allowlist=self.allowlist,
            config_path=config_path,
            plugin_configs=self.plugin_configs,
            watch_config=self.watch_config,
        )

    def apply(self, runtime: TransportRuntime, *, config_path: Path | None) -> None:
        runtime.update(
            router=self.router,
            projects=self.projects,
            allowlist=self.allowlist,
            config_path=config_path,
            plugin_configs=self.plugin_configs,
            watch_config=self.watch_config,
        )


def resolve_plugins_allowlist(
    settings: TakopiSettings | None,
) -> list[str] | None:
    if settings is None:
        return None
    enabled = list(settings.plugins.enabled)
    return enabled or None


def resolve_default_engine(
    *,
    override: str | None,
    settings: TakopiSettings,
    config_path: Path,
    engine_ids: list[str],
) -> str:
    default_engine = override or settings.default_engine or "codex"
    if default_engine not in engine_ids:
        available = ", ".join(sorted(engine_ids))
        raise ConfigError(
            f"Unknown default engine {default_engine!r}. Available: {available}."
        )
    return default_engine


def build_router(
    *,
    settings: TakopiSettings,
    config_path: Path,
    backends: list[EngineBackend],
    default_engine: str,
) -> AutoRouter:
    entries: list[RunnerEntry] = []
    warnings: list[str] = []

    for backend in backends:
        engine_id = backend.id
        issue: str | None = None
        status: EngineStatus = "ok"
        engine_cfg: dict
        try:
            engine_cfg = settings.engine_config(engine_id, config_path=config_path)
        except ConfigError as exc:
            if engine_id == default_engine:
                raise
            issue = str(exc)
            status = "bad_config"
            engine_cfg = {}

        try:
            runner = backend.build_runner(engine_cfg, config_path)
        except Exception as exc:
            if engine_id == default_engine:
                raise
            issue = issue or str(exc)
            if engine_cfg:
                try:
                    runner = backend.build_runner({}, config_path)
                except Exception as fallback_exc:  # noqa: BLE001
                    warnings.append(f"{engine_id}: {issue or str(fallback_exc)}")
                    continue
                status = "bad_config"
            else:
                status = "load_error"
                warnings.append(f"{engine_id}: {issue}")
                continue

        cmd = backend.cli_cmd or backend.id
        if shutil.which(cmd) is None:
            status = "missing_cli"
            if issue:
                issue = f"{issue}; {cmd} not found on PATH"
            else:
                issue = f"{cmd} not found on PATH"

        if status != "ok" and engine_id == default_engine:
            raise ConfigError(f"Default engine {engine_id!r} unavailable: {issue}")

        if status != "ok" and engine_id != default_engine:
            warnings.append(f"{engine_id}: {issue}")

        entries.append(
            RunnerEntry(
                engine=engine_id,
                runner=runner,
                status=status,
                issue=issue,
            )
        )

    for warning in warnings:
        logger.warning("setup.warning", issue=warning)

    return AutoRouter(entries=entries, default_engine=default_engine)


def load_backends(
    *,
    engine_ids: list[str],
    allowlist: list[str] | None,
    default_engine: str,
) -> list[EngineBackend]:
    backends: list[EngineBackend] = []
    load_issues: list[str] = []
    for engine_id in engine_ids:
        try:
            backend = get_backend(engine_id, allowlist=allowlist)
        except ConfigError as exc:
            if engine_id == default_engine:
                raise
            load_issues.append(f"{engine_id}: {exc}")
            continue
        backends.append(backend)
    if not backends:
        raise ConfigError("No engine backends are available.")
    for issue in load_issues:
        logger.warning("setup.warning", issue=issue)
    return backends


def build_runtime_spec(
    *,
    settings: TakopiSettings,
    config_path: Path,
    default_engine_override: str | None = None,
    reserved: Iterable[str] = RESERVED_CHAT_COMMANDS,
) -> RuntimeSpec:
    allowlist = resolve_plugins_allowlist(settings)
    engine_ids = list_backend_ids(allowlist=allowlist)
    projects = settings.to_projects_config(
        config_path=config_path,
        engine_ids=engine_ids,
        reserved=reserved,
    )
    default_engine = resolve_default_engine(
        override=default_engine_override,
        settings=settings,
        config_path=config_path,
        engine_ids=engine_ids,
    )
    backends = load_backends(
        engine_ids=engine_ids,
        allowlist=allowlist,
        default_engine=default_engine,
    )
    router = build_router(
        settings=settings,
        config_path=config_path,
        backends=backends,
        default_engine=default_engine,
    )
    return RuntimeSpec(
        router=router,
        projects=projects,
        allowlist=allowlist,
        plugin_configs=settings.plugins.model_extra,
        watch_config=settings.watch_config,
    )
