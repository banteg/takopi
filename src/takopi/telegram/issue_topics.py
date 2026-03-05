from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..config import ProjectConfig
from ..context import RunContext
from ..github import GitHubIssue, issue_branch_name
from ..logging import get_logger
from ..markdown import MarkdownParts
from ..transport import RenderedMessage, SendOptions
from ..worktrees import WorktreeError, ensure_worktree
from .render import prepare_telegram
from .topic_state import TopicStateStore

if TYPE_CHECKING:
    from ..transport import Transport
    from .client_api import BotClient

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CreatedIssueTopic:
    issue: GitHubIssue
    thread_id: int
    already_existed: bool


def issue_topic_title(issue: GitHubIssue, project: str | None = None) -> str:
    """Generate a topic title for a GitHub issue.

    Uses the same ``@branch`` pattern as manually created topics:
    ``@issue/42`` or ``project @issue/42``.
    """
    branch = issue_branch_name(issue.number)
    prefix = f"{project} " if project else ""
    title = f"{prefix}@{branch}"
    # Telegram topic names have a 128-char limit
    if len(title) > 128:
        title = title[:125] + "..."
    return title


async def create_topics_from_issues(
    *,
    issues: list[GitHubIssue],
    project: str,
    chat_id: int,
    bot: BotClient,
    store: TopicStateStore,
    project_alias: str | None = None,
    project_config: ProjectConfig | None = None,
    transport: Transport | None = None,
    limit: int | None = None,
) -> list[CreatedIssueTopic]:
    """Create Telegram forum topics for a list of GitHub issues.

    Skips issues that already have a bound topic.  Stale bindings (topic
    deleted from Telegram) are detected automatically and re-created.
    When *limit* is set, stops after creating that many **new** topics.
    """
    results: list[CreatedIssueTopic] = []
    created_count = 0
    display_project = project_alias or project

    for issue in issues:
        branch = issue_branch_name(issue.number)
        context = RunContext(project=project, branch=branch)
        title = issue_topic_title(issue, display_project)

        existing_thread = await store.find_thread_for_context(chat_id, context)
        if existing_thread is not None:
            # Verify the topic still exists in Telegram.
            # edit_forum_topic returns True both when the name is
            # updated and when it's unchanged ("not modified").
            still_alive = await bot.edit_forum_topic(
                chat_id=chat_id,
                message_thread_id=existing_thread,
                name=title,
            )
            if still_alive:
                logger.info(
                    "issue_topics.already_exists",
                    issue=issue.number,
                    thread_id=existing_thread,
                )
                results.append(
                    CreatedIssueTopic(
                        issue=issue,
                        thread_id=existing_thread,
                        already_existed=True,
                    )
                )
                continue
            # Stale binding — topic was deleted from Telegram.
            logger.info(
                "issue_topics.stale_binding",
                issue=issue.number,
                thread_id=existing_thread,
            )
            await store.delete_thread(chat_id, existing_thread)
        created = await bot.create_forum_topic(chat_id, title)
        if created is None:
            logger.warning(
                "issue_topics.create_failed",
                issue=issue.number,
                chat_id=chat_id,
            )
            continue

        thread_id = created.message_thread_id
        await store.set_context(
            chat_id,
            thread_id,
            context,
            topic_title=title,
        )

        # Send a bound-context message into the new topic (same as manual
        # topic creation) so that Telegram auto-pins it.
        if transport is not None:
            bound_label = (
                f"{display_project} @{branch}" if display_project else f"@{branch}"
            )
            bound_text = f"topic bound to `{bound_label}`"
            rendered_text, entities = prepare_telegram(
                MarkdownParts(header=bound_text)
            )
            await transport.send(
                channel_id=chat_id,
                message=RenderedMessage(
                    text=rendered_text, extra={"entities": entities}
                ),
                options=SendOptions(thread_id=thread_id),
            )

        if project_config is not None:
            try:
                wt_path = ensure_worktree(project_config, branch)
                logger.info(
                    "issue_topics.worktree_created",
                    issue=issue.number,
                    branch=branch,
                    path=str(wt_path),
                )
            except WorktreeError as exc:
                logger.warning(
                    "issue_topics.worktree_failed",
                    issue=issue.number,
                    branch=branch,
                    error=str(exc),
                )

        logger.info(
            "issue_topics.created",
            issue=issue.number,
            thread_id=thread_id,
            title=title,
        )
        results.append(
            CreatedIssueTopic(
                issue=issue,
                thread_id=thread_id,
                already_existed=False,
            )
        )
        created_count += 1
        if limit is not None and created_count >= limit:
            break

    return results
