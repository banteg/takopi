from pathlib import Path

import pytest

from takopi.config import ProjectConfig, ProjectsConfig
from takopi.context import RunContext
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.telegram.chat_prefs import ChatPrefsStore
from takopi.telegram.engine_defaults import resolve_engine_for_message
from takopi.telegram.topic_state import TopicStateStore
from takopi.transport_runtime import TransportRuntime


@pytest.mark.anyio
async def test_resolve_engine_for_message_sources(tmp_path) -> None:
    codex = ScriptRunner([Return(answer="ok")], engine="codex")
    pi = ScriptRunner([Return(answer="ok")], engine="pi")
    router = AutoRouter(
        entries=[
            RunnerEntry(engine=codex.engine, runner=codex),
            RunnerEntry(engine=pi.engine, runner=pi),
        ],
        default_engine=codex.engine,
    )
    project = ProjectConfig(
        alias="proj",
        path=tmp_path,
        worktrees_dir=Path(".worktrees"),
        default_engine=pi.engine,
    )
    runtime = TransportRuntime(
        router=router,
        projects=ProjectsConfig(projects={"proj": project}, default_project=None),
    )
    chat_prefs = ChatPrefsStore(tmp_path / "telegram_chat_prefs_state.json")
    topic_store = TopicStateStore(tmp_path / "telegram_topics_state.json")
    await chat_prefs.set_default_engine(1, "pi")
    await topic_store.set_default_engine(1, 10, "codex")

    resolved = await resolve_engine_for_message(
        runtime=runtime,
        context=RunContext(project="proj"),
        explicit_engine="codex",
        chat_id=1,
        topic_key=(1, 10),
        topic_store=topic_store,
        chat_prefs=chat_prefs,
    )
    assert resolved.source == "directive"
    assert resolved.engine == "codex"

    await topic_store.clear_default_engine(1, 10)
    resolved = await resolve_engine_for_message(
        runtime=runtime,
        context=RunContext(project="proj"),
        explicit_engine=None,
        chat_id=1,
        topic_key=(1, 10),
        topic_store=topic_store,
        chat_prefs=chat_prefs,
    )
    assert resolved.source == "chat_default"
    assert resolved.engine == "pi"

    await chat_prefs.clear_default_engine(1)
    resolved = await resolve_engine_for_message(
        runtime=runtime,
        context=RunContext(project="proj"),
        explicit_engine=None,
        chat_id=1,
        topic_key=(1, 10),
        topic_store=topic_store,
        chat_prefs=chat_prefs,
    )
    assert resolved.source == "project_default"
    assert resolved.engine == "pi"
