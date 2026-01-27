from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from ..config import ConfigError
from ..context import RunContext
from ..transport_runtime import TransportRuntime
from ..utils.git import git_is_dirty
from .topic_state import TopicThreadSnapshot
from .topics import _topics_scope_label

if TYPE_CHECKING:
    from .bridge import TelegramBridgeConfig

__all__ = [
    "_format_context",
    "_format_contexts",
    "_format_ctx_status",
    "_build_composite_prelude",
    "_merge_topic_context",
    "_parse_project_branch_args",
    "_usage_ctx_set",
    "_usage_topic",
]


def _format_context(runtime: TransportRuntime, context: RunContext | None) -> str:
    if context is None or context.project is None:
        return "none"
    project = runtime.project_alias_for_key(context.project)
    if context.branch:
        return f"{project} @{context.branch}"
    return project


def _format_contexts(
    runtime: TransportRuntime,
    contexts: tuple[RunContext, ...],
    *,
    active_project: str | None,
) -> str:
    if not contexts:
        return "none"
    labels: list[str] = []
    for context in contexts:
        label = _format_context(runtime, context)
        if active_project is not None and context.project == active_project:
            label = f"{label} (active)"
        labels.append(label)
    return ", ".join(labels)


def _usage_ctx_set(*, chat_project: str | None) -> str:
    if chat_project is not None:
        return "usage: `/ctx set [@branch]`"
    return "usage: `/ctx set <project> [@branch]`"


def _usage_topic(*, chat_project: str | None) -> str:
    if chat_project is not None:
        return "usage: `/topic @branch`"
    return "usage: `/topic <project> @branch`"


def _parse_project_branch_args(
    args_text: str,
    *,
    runtime: TransportRuntime,
    require_branch: bool,
    chat_project: str | None,
) -> tuple[RunContext | None, str | None]:
    from .files import split_command_args

    tokens = split_command_args(args_text)
    if not tokens:
        return (
            None,
            _usage_topic(chat_project=chat_project)
            if require_branch
            else _usage_ctx_set(chat_project=chat_project),
        )
    if len(tokens) > 2:
        return None, "too many arguments"
    project_token: str | None = None
    branch: str | None = None
    first = tokens[0]
    if first.startswith("@"):
        branch = first[1:] or None
    else:
        project_token = first
        if len(tokens) == 2:
            second = tokens[1]
            if not second.startswith("@"):
                return None, "branch must be prefixed with @"
            branch = second[1:] or None

    project_key: str | None = None
    if chat_project is not None:
        if project_token is None:
            project_key = chat_project
        else:
            normalized = runtime.normalize_project_key(project_token)
            if normalized is None:
                return None, f"unknown project {project_token!r}"
            if normalized != chat_project:
                expected = runtime.project_alias_for_key(chat_project)
                return None, (f"project mismatch for this chat; expected {expected!r}.")
            project_key = normalized
    else:
        if project_token is None:
            return None, "project is required"
        project_key = runtime.normalize_project_key(project_token)
        if project_key is None:
            return None, f"unknown project {project_token!r}"

    if require_branch and not branch:
        return None, "branch is required"

    return RunContext(project=project_key, branch=branch), None


def _format_ctx_status(
    *,
    cfg: TelegramBridgeConfig,
    runtime: TransportRuntime,
    contexts: tuple[RunContext, ...],
    active_project: str | None,
    resolved: RunContext | None,
    context_source: str,
    snapshot: TopicThreadSnapshot | None,
    chat_project: str | None,
) -> str:
    active_ctx = None
    if active_project is not None:
        for ctx in contexts:
            if ctx.project == active_project:
                active_ctx = ctx
                break
    if active_ctx is None and len(contexts) == 1:
        active_ctx = contexts[0]
    lines = [
        f"topics: enabled (scope={_topics_scope_label(cfg)})",
        f"bound ctxs: {_format_contexts(runtime, contexts, active_project=active_project)}",
        f"active ctx: {_format_context(runtime, active_ctx)}",
        f"resolved ctx: {_format_context(runtime, resolved)} (source: {context_source})",
    ]
    if chat_project is None and not contexts:
        topic_usage = (
            _usage_topic(chat_project=chat_project).removeprefix("usage: ").strip()
        )
        ctx_usage = (
            _usage_ctx_set(chat_project=chat_project).removeprefix("usage: ").strip()
        )
        lines.append(f"note: unbound topic — bind with {topic_usage} or {ctx_usage}")
    sessions = None
    if snapshot is not None and snapshot.sessions:
        sessions = ", ".join(sorted(snapshot.sessions))
    lines.append(f"sessions: {sessions or 'none'}")
    return "\n".join(lines)


def _merge_topic_context(
    *, chat_project: str | None, bound: RunContext | None
) -> RunContext | None:
    if chat_project is None:
        return bound
    if bound is None:
        return RunContext(project=chat_project, branch=None)
    if bound.project is None:
        return RunContext(project=chat_project, branch=bound.branch)
    return bound


@dataclass(frozen=True, slots=True)
class _RepoSummary:
    context: RunContext
    alias: str
    path: Path
    git_dirty: bool | None


def _resolve_repo_summaries(
    runtime: TransportRuntime,
    contexts: Sequence[RunContext],
) -> tuple[list[_RepoSummary], str | None]:
    summaries: list[_RepoSummary] = []
    for ctx in contexts:
        if ctx.project is None:
            continue
        try:
            path = runtime.resolve_run_cwd(ctx)
        except ConfigError as exc:
            return [], f"warning: failed to resolve repo path ({exc})."
        if path is None:
            return [], "warning: missing project path for context."
        summaries.append(
            _RepoSummary(
                context=ctx,
                alias=runtime.project_alias_for_key(ctx.project),
                path=path,
                git_dirty=git_is_dirty(path),
            )
        )
    return summaries, None


def _detect_overlapping_paths(paths: Sequence[Path]) -> list[tuple[Path, Path]]:
    resolved = [path.resolve(strict=False) for path in paths]
    overlaps: list[tuple[Path, Path]] = []
    for i, base in enumerate(resolved):
        for j, other in enumerate(resolved):
            if i == j:
                continue
            if other.is_relative_to(base):
                overlaps.append((base, other))
    return overlaps


def _build_composite_prelude(
    *,
    runtime: TransportRuntime,
    contexts: Sequence[RunContext],
    active_project: str | None,
) -> tuple[str | None, str | None]:
    summaries, error = _resolve_repo_summaries(runtime, contexts)
    if error is not None:
        return None, error
    if len(summaries) <= 1:
        return None, None
    lines: list[str] = ["[workspace]"]
    if active_project is not None:
        active_alias = runtime.project_alias_for_key(active_project)
        lines.append(f"active: {active_alias}")
    for summary in summaries:
        branch = f" @{summary.context.branch}" if summary.context.branch else ""
        git_state = (
            "dirty"
            if summary.git_dirty
            else "clean"
            if summary.git_dirty is False
            else "unknown"
        )
        lines.append(
            f"- {summary.alias}{branch} | {summary.path} | git: {git_state}"
        )
    overlaps = _detect_overlapping_paths([summary.path for summary in summaries])
    warning = None
    if overlaps:
        first = overlaps[0]
        warning = (
            "warning: repo paths overlap "
            f"({first[0]} contains {first[1]})."
        )
        lines.append(warning)
    lines.append("[/workspace]")
    return "\n".join(lines), warning
