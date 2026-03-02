from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .agent_modes import AgentModeCapabilities, ModeDiscoveryResult
from .backends import AgentModeProbe
from .config import ConfigError, ProjectsConfig
from .context import RunContext
from .directives import (
    ParsedDirectives,
    format_context_line,
    parse_context_line,
    parse_directives,
)
from .model import EngineId, ResumeToken
from .logging import get_logger
from .plugins import normalize_allowlist
from .router import AutoRouter, EngineStatus
from .runner import Runner
from .worktrees import WorktreeError, resolve_run_cwd

logger = get_logger(__name__)

type ContextSource = Literal[
    "reply_ctx",
    "directives",
    "ambient",
    "default_project",
    "none",
]


@dataclass(frozen=True, slots=True)
class ResolvedMessage:
    prompt: str
    resume_token: ResumeToken | None
    engine_override: EngineId | None
    context: RunContext | None
    context_source: ContextSource = "none"


@dataclass(frozen=True, slots=True)
class ResolvedRunner:
    engine: EngineId
    runner: Runner
    available: bool
    issue: str | None = None


class TransportRuntime:
    __slots__ = (
        "_router",
        "_projects",
        "_allowlist",
        "_config_path",
        "_plugin_configs",
        "_engine_mode_probers",
        "_watch_config",
    )

    def __init__(
        self,
        *,
        router: AutoRouter,
        projects: ProjectsConfig,
        allowlist: Iterable[str] | None = None,
        config_path: Path | None = None,
        plugin_configs: Mapping[str, Any] | None = None,
        engine_mode_probers: Mapping[EngineId, AgentModeProbe] | None = None,
        watch_config: bool = False,
    ) -> None:
        self._apply(
            router=router,
            projects=projects,
            allowlist=allowlist,
            config_path=config_path,
            plugin_configs=plugin_configs,
            engine_mode_probers=engine_mode_probers,
            watch_config=watch_config,
        )

    def update(
        self,
        *,
        router: AutoRouter,
        projects: ProjectsConfig,
        allowlist: Iterable[str] | None = None,
        config_path: Path | None = None,
        plugin_configs: Mapping[str, Any] | None = None,
        engine_mode_probers: Mapping[EngineId, AgentModeProbe] | None = None,
        watch_config: bool = False,
    ) -> None:
        self._apply(
            router=router,
            projects=projects,
            allowlist=allowlist,
            config_path=config_path,
            plugin_configs=plugin_configs,
            engine_mode_probers=engine_mode_probers,
            watch_config=watch_config,
        )

    def _apply(
        self,
        *,
        router: AutoRouter,
        projects: ProjectsConfig,
        allowlist: Iterable[str] | None,
        config_path: Path | None,
        plugin_configs: Mapping[str, Any] | None,
        engine_mode_probers: Mapping[EngineId, AgentModeProbe] | None,
        watch_config: bool,
    ) -> None:
        self._router = router
        self._projects = projects
        self._allowlist = normalize_allowlist(allowlist)
        self._config_path = config_path
        self._plugin_configs = dict(plugin_configs or {})
        self._engine_mode_probers = dict(engine_mode_probers or {})
        self._watch_config = watch_config

    @staticmethod
    def _normalize_modes(raw: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw:
            mode = str(item).strip().lower()
            if not mode:
                continue
            if mode in seen:
                continue
            seen.add(mode)
            normalized.append(mode)
        return tuple(normalized)

    def probe_engine_agent_modes(
        self,
        engine: EngineId,
        *,
        timeout_s: float,
    ) -> AgentModeCapabilities:
        probe = self._engine_mode_probers.get(engine)
        if probe is None:
            return AgentModeCapabilities()
        try:
            discovered = probe(timeout_s)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - safety net
            logger.info(
                "agent_modes.probe.failed",
                engine=engine,
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return AgentModeCapabilities()
        return AgentModeCapabilities(
            supports_agent=bool(discovered.supports_agent),
            known_modes=self._normalize_modes(discovered.known_modes),
            shortcut_modes=self._normalize_modes(discovered.shortcut_modes),
        )

    def discover_agent_modes(self, *, timeout_s: float) -> ModeDiscoveryResult:
        supports: set[str] = set()
        known_modes: dict[str, tuple[str, ...]] = {}
        shortcut_modes: list[str] = []
        seen_shortcuts: set[str] = set()

        for engine in self.available_engine_ids():
            discovered = self.probe_engine_agent_modes(engine, timeout_s=timeout_s)
            if not discovered.supports_agent:
                continue
            key = engine.lower()
            supports.add(key)
            if discovered.known_modes:
                known_modes[key] = discovered.known_modes
            for mode in discovered.shortcut_modes:
                if mode in seen_shortcuts:
                    continue
                seen_shortcuts.add(mode)
                shortcut_modes.append(mode)

        return ModeDiscoveryResult(
            supports_agent=frozenset(supports),
            known_modes=known_modes,
            shortcut_modes=tuple(shortcut_modes),
        )

    @property
    def default_engine(self) -> EngineId:
        return self._router.default_engine

    def resolve_engine(
        self,
        *,
        engine_override: EngineId | None,
        context: RunContext | None,
    ) -> EngineId:
        if engine_override is not None:
            return engine_override
        if context is None or context.project is None:
            return self._router.default_engine
        project = self._projects.projects.get(context.project)
        if project is None:
            return self._router.default_engine
        return project.default_engine or self._router.default_engine

    @property
    def engine_ids(self) -> tuple[EngineId, ...]:
        return self._router.engine_ids

    def available_engine_ids(self) -> tuple[EngineId, ...]:
        return tuple(entry.engine for entry in self._router.available_entries)

    def engine_ids_with_status(self, status: EngineStatus) -> tuple[EngineId, ...]:
        return tuple(
            entry.engine for entry in self._router.entries if entry.status == status
        )

    def missing_engine_ids(self) -> tuple[EngineId, ...]:
        return self.engine_ids_with_status("missing_cli")

    def project_aliases(self) -> tuple[str, ...]:
        return tuple(project.alias for project in self._projects.projects.values())

    @property
    def allowlist(self) -> set[str] | None:
        return self._allowlist

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    @property
    def watch_config(self) -> bool:
        return self._watch_config

    def plugin_config(self, plugin_id: str) -> dict[str, Any]:
        if not self._plugin_configs:
            return {}
        raw = self._plugin_configs.get(plugin_id)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            path = self._config_path or Path("<config>")
            raise ConfigError(
                f"Invalid `plugins.{plugin_id}` in {path}; expected a table."
            )
        return dict(raw)

    def resolve_message(
        self,
        *,
        text: str,
        reply_text: str | None,
        ambient_context: RunContext | None = None,
        chat_id: int | None = None,
    ) -> ResolvedMessage:
        directives = parse_directives(
            text,
            engine_ids=self._router.engine_ids,
            projects=self._projects,
        )
        reply_ctx = parse_context_line(reply_text, projects=self._projects)
        resume_token = self._router.resolve_resume(directives.prompt, reply_text)
        chat_project = self._projects.project_for_chat(chat_id)
        default_project = chat_project or self._projects.default_project

        context, context_source = self._resolve_context(
            directives=directives,
            reply_ctx=reply_ctx,
            ambient_context=ambient_context,
            default_project=default_project,
        )
        engine_override = self._resolve_engine_override(
            directives_engine=directives.engine,
        )

        return ResolvedMessage(
            prompt=directives.prompt,
            resume_token=resume_token,
            engine_override=engine_override,
            context=context,
            context_source=context_source,
        )

    def project_default_engine(self, context: RunContext | None) -> EngineId | None:
        if context is None or context.project is None:
            return None
        project = self._projects.projects.get(context.project)
        if project is None:
            return None
        return project.default_engine

    def _resolve_context(
        self,
        *,
        directives: ParsedDirectives,
        reply_ctx: RunContext | None,
        ambient_context: RunContext | None,
        default_project: str | None,
    ) -> tuple[RunContext | None, ContextSource]:
        if reply_ctx is not None:
            return reply_ctx, "reply_ctx"

        project_key = directives.project
        branch = directives.branch
        if project_key is None:
            if ambient_context is not None and ambient_context.project is not None:
                project_key = ambient_context.project
            else:
                project_key = default_project
        if (
            branch is None
            and ambient_context is not None
            and ambient_context.branch is not None
            and project_key == ambient_context.project
        ):
            branch = ambient_context.branch
        context: RunContext | None = None
        if project_key is not None or branch is not None:
            context = RunContext(project=project_key, branch=branch)

        if directives.project is not None or directives.branch is not None:
            context_source: ContextSource = "directives"
        elif ambient_context is not None and ambient_context.project is not None:
            context_source = "ambient"
        elif default_project is not None:
            context_source = "default_project"
        else:
            context_source = "none"

        return context, context_source

    def _resolve_engine_override(
        self,
        *,
        directives_engine: EngineId | None,
    ) -> EngineId | None:
        if directives_engine is not None:
            return directives_engine
        return None

    @property
    def default_project(self) -> str | None:
        return self._projects.default_project

    def normalize_project_key(self, value: str) -> str | None:
        key = value.strip().lower()
        if key in self._projects.projects:
            return key
        return None

    def project_alias_for_key(self, key: str) -> str:
        project = self._projects.projects.get(key)
        return project.alias if project is not None else key

    def default_context_for_chat(self, chat_id: int | None) -> RunContext | None:
        project_key = self._projects.project_for_chat(chat_id)
        if project_key is None:
            return None
        return RunContext(project=project_key, branch=None)

    def project_chat_ids(self) -> tuple[int, ...]:
        return self._projects.project_chat_ids()

    def resolve_runner(
        self,
        *,
        resume_token: ResumeToken | None,
        engine_override: EngineId | None,
    ) -> ResolvedRunner:
        entry = (
            self._router.entry_for_engine(engine_override)
            if resume_token is None
            else self._router.entry_for(resume_token)
        )
        return ResolvedRunner(
            engine=entry.engine,
            runner=entry.runner,
            available=entry.available,
            issue=entry.issue,
        )

    def is_resume_line(self, line: str) -> bool:
        return self._router.is_resume_line(line)

    def resolve_run_cwd(self, context: RunContext | None) -> Path | None:
        try:
            return resolve_run_cwd(context, projects=self._projects)
        except WorktreeError as exc:
            raise ConfigError(str(exc)) from exc

    def format_context_line(self, context: RunContext | None) -> str | None:
        return format_context_line(context, projects=self._projects)
