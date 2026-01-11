from __future__ import annotations

from typing import TYPE_CHECKING

from ..context import RunContext
from ..transport_runtime import TransportRuntime
from .topic_state import TopicThreadSnapshot
from .topics import _topics_scope_label

if TYPE_CHECKING:
    from .bridge import TelegramBridgeConfig

__all__ = [
    "_format_context",
    "_format_ctx_status",
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
    bound: RunContext | None,
    resolved: RunContext | None,
    context_source: str,
    snapshot: TopicThreadSnapshot | None,
    chat_project: str | None,
) -> str:
    lines = [
        f"topics: enabled (scope={_topics_scope_label(cfg)})",
        f"bound ctx: {_format_context(runtime, bound)}",
        f"resolved ctx: {_format_context(runtime, resolved)} (source: {context_source})",
    ]
    if chat_project is None and bound is None:
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
