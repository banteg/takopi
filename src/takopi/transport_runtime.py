from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigError, ProjectsConfig
from .context import RunContext
from .directives import format_context_line, parse_context_line, parse_directives
from .model import EngineId, ResumeToken
from .plugins import normalize_allowlist
from .router import AutoRouter
from .runner import Runner
from .worktrees import WorktreeError, resolve_run_cwd


@dataclass(frozen=True, slots=True)
class ResolvedMessage:
    prompt: str
    resume_token: ResumeToken | None
    engine_override: EngineId | None
    context: RunContext | None


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
    )

    def __init__(
        self,
        *,
        router: AutoRouter,
        projects: ProjectsConfig,
        allowlist: Iterable[str] | None = None,
        config_path: Path | None = None,
        plugin_configs: Mapping[str, Any] | None = None,
    ) -> None:
        self._router = router
        self._projects = projects
        self._allowlist = normalize_allowlist(allowlist)
        self._config_path = config_path
        self._plugin_configs = dict(plugin_configs or {})

    def update(
        self,
        *,
        router: AutoRouter,
        projects: ProjectsConfig,
        allowlist: Iterable[str] | None = None,
        config_path: Path | None = None,
        plugin_configs: Mapping[str, Any] | None = None,
    ) -> None:
        self._router = router
        self._projects = projects
        self._allowlist = normalize_allowlist(allowlist)
        self._config_path = config_path
        self._plugin_configs = dict(plugin_configs or {})

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

    def missing_engine_ids(self) -> tuple[EngineId, ...]:
        return tuple(
            entry.engine for entry in self._router.entries if not entry.available
        )

    def project_aliases(self) -> tuple[str, ...]:
        return tuple(project.alias for project in self._projects.projects.values())

    @property
    def allowlist(self) -> set[str] | None:
        return self._allowlist

    @property
    def config_path(self) -> Path | None:
        return self._config_path

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

        if resume_token is not None:
            context = reply_ctx
            if context is None and chat_project is not None:
                context = RunContext(project=chat_project, branch=None)
            return ResolvedMessage(
                prompt=directives.prompt,
                resume_token=resume_token,
                engine_override=None,
                context=context,
            )

        if reply_ctx is not None:
            engine_override = None
            if reply_ctx.project is not None:
                project = self._projects.projects.get(reply_ctx.project)
                if project is not None and project.default_engine is not None:
                    engine_override = project.default_engine
            return ResolvedMessage(
                prompt=directives.prompt,
                resume_token=None,
                engine_override=engine_override,
                context=reply_ctx,
            )

        project_key = directives.project
        if project_key is None:
            project_key = chat_project or self._projects.default_project

        context = None
        if project_key is not None or directives.branch is not None:
            context = RunContext(project=project_key, branch=directives.branch)

        engine_override = directives.engine
        if engine_override is None and project_key is not None:
            project = self._projects.projects.get(project_key)
            if project is not None and project.default_engine is not None:
                engine_override = project.default_engine

        return ResolvedMessage(
            prompt=directives.prompt,
            resume_token=None,
            engine_override=engine_override,
            context=context,
        )

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
