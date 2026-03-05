from pathlib import Path

import pytest

from takopi.context import RunContext
from takopi.github import GitHubIssue
from takopi.telegram.issue_topics import (
    CreatedIssueTopic,
    create_topics_from_issues,
    issue_topic_title,
)
from takopi.telegram.topic_state import TopicStateStore
from takopi.telegram.api_models import ForumTopic
from tests.telegram_fakes import FakeBot


class _SequentialFakeBot(FakeBot):
    """FakeBot that returns sequential thread IDs for create_forum_topic."""

    def __init__(self) -> None:
        super().__init__()
        self._next_thread_id = 100

    async def create_forum_topic(self, chat_id: int, name: str) -> ForumTopic | None:
        tid = self._next_thread_id
        self._next_thread_id += 1
        return ForumTopic(message_thread_id=tid)


def _issue(number: int, title: str = "fix bug") -> GitHubIssue:
    return GitHubIssue(
        number=number,
        title=title,
        labels=(),
        state="open",
        html_url=f"https://github.com/a/b/issues/{number}",
    )


class TestIssueTopicTitle:
    def test_with_project(self) -> None:
        issue = _issue(42, "fix the widget")
        assert issue_topic_title(issue, "myproj") == "myproj @issue/42"

    def test_without_project(self) -> None:
        issue = _issue(7, "add tests")
        assert issue_topic_title(issue) == "@issue/7"

    def test_uses_branch_name(self) -> None:
        issue = _issue(123, "some long title that should not appear")
        title = issue_topic_title(issue)
        assert title == "@issue/123"
        assert issue.title not in title


@pytest.mark.anyio
async def test_create_topics_basic(tmp_path: Path) -> None:
    bot = _SequentialFakeBot()
    store = TopicStateStore(tmp_path / "topics.json")
    issues = [_issue(1, "first"), _issue(2, "second")]

    results = await create_topics_from_issues(
        issues=issues,
        project="alpha",
        chat_id=100,
        bot=bot,
        store=store,
    )

    assert len(results) == 2
    assert all(not r.already_existed for r in results)
    # Verify contexts were stored
    ctx1 = await store.get_context(100, results[0].thread_id)
    assert ctx1 == RunContext(project="alpha", branch="issue/1")
    ctx2 = await store.get_context(100, results[1].thread_id)
    assert ctx2 == RunContext(project="alpha", branch="issue/2")


@pytest.mark.anyio
async def test_create_topics_skips_existing(tmp_path: Path) -> None:
    bot = FakeBot()
    store = TopicStateStore(tmp_path / "topics.json")
    issues = [_issue(1, "first")]

    # Pre-create a topic for issue/1
    context = RunContext(project="alpha", branch="issue/1")
    await store.set_context(100, 999, context, topic_title="alpha #1 first")

    results = await create_topics_from_issues(
        issues=issues,
        project="alpha",
        chat_id=100,
        bot=bot,
        store=store,
    )

    assert len(results) == 1
    assert results[0].already_existed is True
    assert results[0].thread_id == 999


@pytest.mark.anyio
async def test_create_topics_with_project_alias(tmp_path: Path) -> None:
    bot = FakeBot()
    store = TopicStateStore(tmp_path / "topics.json")
    issues = [_issue(5, "my issue")]

    results = await create_topics_from_issues(
        issues=issues,
        project="alpha",
        chat_id=100,
        bot=bot,
        store=store,
        project_alias="Alpha",
    )

    assert len(results) == 1
    snapshot = await store.get_thread(100, results[0].thread_id)
    assert snapshot is not None
    assert "Alpha" in (snapshot.topic_title or "")


class _DeletedTopicBot(_SequentialFakeBot):
    """Bot where edit_forum_topic returns False (topic was deleted)."""

    async def edit_forum_topic(
        self, chat_id: int, message_thread_id: int, name: str
    ) -> bool:
        return False


@pytest.mark.anyio
async def test_auto_cleans_stale_binding(tmp_path: Path) -> None:
    """When a topic was deleted from Telegram, the stale binding is cleaned up."""
    bot = _DeletedTopicBot()
    store = TopicStateStore(tmp_path / "topics.json")

    # Pre-create a stale binding for issue/1
    context = RunContext(project="alpha", branch="issue/1")
    await store.set_context(100, 999, context, topic_title="@issue/1")

    results = await create_topics_from_issues(
        issues=[_issue(1, "first")],
        project="alpha",
        chat_id=100,
        bot=bot,
        store=store,
    )

    assert len(results) == 1
    assert results[0].already_existed is False
    assert results[0].thread_id != 999
    ctx = await store.get_context(100, results[0].thread_id)
    assert ctx == context


@pytest.mark.anyio
async def test_second_sync_skips_already_created(tmp_path: Path) -> None:
    """Running sync twice with the same issues should not create duplicates."""
    bot = _SequentialFakeBot()
    store = TopicStateStore(tmp_path / "topics.json")
    issues = [_issue(1, "first"), _issue(2, "second")]

    # First sync — creates topics
    results1 = await create_topics_from_issues(
        issues=issues,
        project="alpha",
        chat_id=100,
        bot=bot,
        store=store,
    )
    assert len(results1) == 2
    assert all(not r.already_existed for r in results1)

    # Second sync — should skip all
    results2 = await create_topics_from_issues(
        issues=issues,
        project="alpha",
        chat_id=100,
        bot=bot,
        store=store,
    )
    assert len(results2) == 2
    assert all(r.already_existed for r in results2)
    # Thread IDs should match the first sync
    assert results2[0].thread_id == results1[0].thread_id
    assert results2[1].thread_id == results1[1].thread_id
