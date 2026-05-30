from __future__ import annotations

from typing import TYPE_CHECKING

from ...config import ConfigError
from ...context import RunContext
from ...github import GitHubError, fetch_issues, issue_branch_name, parse_github_remote
from ...logging import get_logger
from ..files import split_command_args
from ..issue_topics import create_topics_from_issues
from ..topic_state import TopicStateStore
from ..topics import _topics_chat_project, _topics_command_error
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig
    from ..types import TelegramIncomingMessage

logger = get_logger(__name__)


async def _handle_issues_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    store: TopicStateStore,
    *,
    resolved_scope: str | None = None,
    scope_chat_ids: frozenset[int] | None = None,
) -> None:
    reply = make_reply(cfg, msg)
    error = _topics_command_error(
        cfg,
        msg.chat_id,
        resolved_scope=resolved_scope,
        scope_chat_ids=scope_chat_ids,
    )
    if error is not None:
        await reply(text=error)
        return

    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "help"

    if action == "help":
        await reply(
            text=(
                "usage:\n"
                "`/issues sync [project] [-label=X] [-limit=N]`\n"
                "fetches github issues and creates a topic per issue."
            )
        )
        return

    if action != "sync":
        await reply(text=f"unknown `/issues` action {action!r}. use `/issues sync`.")
        return

    # parse optional args: project, -label=X, -limit=N
    rest = tokens[1:]
    project_token: str | None = None
    labels: list[str] = []
    limit = 10

    for token in rest:
        if token.startswith("-label="):
            labels.append(token.removeprefix("-label="))
        elif token.startswith("-limit="):
            try:
                limit = int(token.removeprefix("-limit="))
            except ValueError:
                await reply(text="invalid -limit value")
                return
        elif project_token is None:
            project_token = token
        else:
            await reply(text=f"unexpected argument: {token!r}")
            return

    # resolve project: explicit arg > topic context > chat project > default
    chat_project = _topics_chat_project(cfg, msg.chat_id)
    topic_project: str | None = None
    if msg.thread_id is not None:
        topic_ctx = await store.get_context(msg.chat_id, msg.thread_id)
        if topic_ctx is not None:
            topic_project = topic_ctx.project

    if project_token is not None:
        project_key = cfg.runtime.normalize_project_key(project_token)
        if project_key is None:
            await reply(text=f"unknown project {project_token!r}")
            return
    elif topic_project is not None:
        project_key = topic_project
    elif chat_project is not None:
        project_key = chat_project
    elif cfg.runtime.default_project is not None:
        project_key = cfg.runtime.default_project
    else:
        await reply(text="no project specified and no default project configured.")
        return

    try:
        project_path = cfg.runtime.resolve_run_cwd(
            RunContext(project=project_key)
        )
    except ConfigError:
        project_path = None
    if project_path is None:
        await reply(text=f"cannot resolve path for project {project_key!r}")
        return

    await reply(text="fetching github issues...")

    try:
        owner, repo = parse_github_remote(project_path)
    except GitHubError as exc:
        await reply(text=f"error: {exc}")
        return

    try:
        issues = await fetch_issues(
            owner,
            repo,
            labels=labels or None,
        )
    except GitHubError as exc:
        await reply(text=f"error: {exc}")
        return

    if not issues:
        await reply(text="no open issues found.")
        return

    results = await create_topics_from_issues(
        issues=issues,
        project=project_key,
        chat_id=msg.chat_id,
        bot=cfg.bot,
        store=store,
        project_alias=cfg.runtime.project_alias_for_key(project_key),
        project_config=cfg.runtime.project_config_for_key(project_key),
        transport=cfg.exec_cfg.transport,
        limit=limit,
    )

    created = [r for r in results if not r.already_existed]
    skipped = [r for r in results if r.already_existed]

    parts: list[str] = []
    if created:
        header = f"**created {len(created)} topic(s):**"
        items = "\n".join(
            f"- @{issue_branch_name(r.issue.number)} — {r.issue.title}"
            for r in created
        )
        parts.append(f"{header}\n\n{items}")
    if skipped:
        parts.append(f"skipped {len(skipped)} existing topic(s)")
    if not created and not skipped:
        parts.append("no topics created.")

    await reply(text="\n\n".join(parts))
