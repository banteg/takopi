import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from takopi.config import ProjectConfig, ProjectsConfig
from takopi.github import GitHubIssue
from takopi.runners.mock import Return, ScriptRunner
from takopi.settings import TelegramTopicsSettings
from takopi.telegram.commands.issues import _handle_issues_command
from takopi.telegram.topic_state import TopicStateStore
from takopi.telegram.types import TelegramIncomingMessage
from takopi.transport_runtime import TransportRuntime
from tests.telegram_fakes import (
    DEFAULT_ENGINE_ID,
    FakeTransport,
    _make_router,
    make_cfg,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(path)},
    )


def _msg(
    text: str,
    *,
    chat_id: int = 123,
    message_id: int = 1,
    thread_id: int | None = None,
) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=1,
        thread_id=thread_id,
        chat_type="supergroup",
    )


def _runtime(tmp_path: Path) -> TransportRuntime:
    runner = ScriptRunner([Return(answer="ok")], engine=DEFAULT_ENGINE_ID)
    projects = ProjectsConfig(
        projects={
            "alpha": ProjectConfig(
                alias="Alpha",
                path=tmp_path,
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project="alpha",
    )
    return TransportRuntime(
        router=_make_router(runner),
        projects=projects,
        config_path=tmp_path / "takopi.toml",
    )


def _issues() -> list[GitHubIssue]:
    return [
        GitHubIssue(
            number=1,
            title="fix bug",
            labels=("bug",),
            state="open",
            html_url="https://github.com/a/b/issues/1",
        ),
        GitHubIssue(
            number=2,
            title="add feature",
            labels=(),
            state="open",
            html_url="https://github.com/a/b/issues/2",
        ),
    ]


@pytest.mark.anyio
async def test_issues_help(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        topics=TelegramTopicsSettings(enabled=True, scope="all"),
    )
    store = TopicStateStore(tmp_path / "topics.json")
    msg = _msg("/issues")

    await _handle_issues_command(
        cfg,
        msg,
        args_text="",
        store=store,
        resolved_scope="all",
        scope_chat_ids=frozenset({msg.chat_id}),
    )

    text = transport.send_calls[-1]["message"].text
    assert "/issues sync" in text


@pytest.mark.anyio
async def test_issues_sync_creates_topics(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    transport = FakeTransport()
    runtime = _runtime(tmp_path)
    cfg = replace(
        make_cfg(transport),
        runtime=runtime,
        topics=TelegramTopicsSettings(enabled=True, scope="all"),
    )
    store = TopicStateStore(tmp_path / "topics.json")
    msg = _msg("/issues sync")

    with (
        patch(
            "takopi.telegram.commands.issues.parse_github_remote",
            return_value=("owner", "repo"),
        ),
        patch(
            "takopi.telegram.commands.issues.fetch_issues",
            new_callable=AsyncMock,
            return_value=_issues(),
        ),
    ):
        await _handle_issues_command(
            cfg,
            msg,
            args_text="sync",
            store=store,
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

    # Should have: "fetching..." message + results message
    texts = [c["message"].text for c in transport.send_calls]
    assert any("created 2 topic" in t for t in texts)


@pytest.mark.anyio
async def test_issues_sync_no_issues(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    transport = FakeTransport()
    runtime = _runtime(tmp_path)
    cfg = replace(
        make_cfg(transport),
        runtime=runtime,
        topics=TelegramTopicsSettings(enabled=True, scope="all"),
    )
    store = TopicStateStore(tmp_path / "topics.json")
    msg = _msg("/issues sync")

    with (
        patch(
            "takopi.telegram.commands.issues.parse_github_remote",
            return_value=("owner", "repo"),
        ),
        patch(
            "takopi.telegram.commands.issues.fetch_issues",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        await _handle_issues_command(
            cfg,
            msg,
            args_text="sync",
            store=store,
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

    texts = [c["message"].text for c in transport.send_calls]
    assert any("no open issues" in t for t in texts)
