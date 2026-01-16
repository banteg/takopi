from dataclasses import replace
from pathlib import Path

from takopi.config import ProjectConfig, ProjectsConfig
from takopi.context import RunContext
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.telegram import context as tg_context
from takopi.telegram.topic_state import TopicThreadSnapshot
from takopi.transport_runtime import TransportRuntime
from tests.telegram_fakes import DEFAULT_ENGINE_ID, FakeTransport, make_cfg


def _runtime(tmp_path: Path) -> TransportRuntime:
    runner = ScriptRunner([Return(answer="ok")], engine=DEFAULT_ENGINE_ID)
    router = AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )
    projects = ProjectsConfig(
        projects={
            "alpha": ProjectConfig(
                alias="Alpha",
                path=tmp_path,
                worktrees_dir=Path(".worktrees"),
            ),
            "beta": ProjectConfig(
                alias="Beta",
                path=tmp_path / "beta",
                worktrees_dir=Path(".worktrees"),
            ),
        },
        default_project="alpha",
        chat_map={123: "alpha"},
    )
    return TransportRuntime(router=router, projects=projects)


def _cfg(tmp_path: Path):
    transport = FakeTransport()
    return replace(make_cfg(transport), runtime=_runtime(tmp_path))


def test_format_context_variants(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    assert tg_context._format_context(runtime, None) == "none"
    assert tg_context._format_context(runtime, RunContext(project="alpha")) == "Alpha"
    assert (
        tg_context._format_context(runtime, RunContext(project="alpha", branch="dev"))
        == "Alpha @dev"
    )


def test_usage_helpers() -> None:
    assert (
        tg_context._usage_ctx_set(chat_project=None)
        == "usage: `/ctx set <project> [@branch]`"
    )
    assert (
        tg_context._usage_ctx_set(chat_project="alpha") == "usage: `/ctx set [@branch]`"
    )
    assert (
        tg_context._usage_topic(chat_project=None)
        == "usage: `/topic <project> @branch`"
    )
    assert tg_context._usage_topic(chat_project="alpha") == "usage: `/topic @branch`"


def test_parse_project_branch_args_missing_project(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    context, error = tg_context._parse_project_branch_args(
        "",
        runtime=runtime,
        require_branch=False,
        chat_project=None,
    )
    assert context is None
    assert error == "usage: `/ctx set <project> [@branch]`"


def test_parse_project_branch_args_requires_branch(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    context, error = tg_context._parse_project_branch_args(
        "alpha",
        runtime=runtime,
        require_branch=True,
        chat_project=None,
    )
    assert context is None
    assert error == "branch is required"


def test_parse_project_branch_args_chat_project_mismatch(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    context, error = tg_context._parse_project_branch_args(
        "beta @dev",
        runtime=runtime,
        require_branch=True,
        chat_project="alpha",
    )
    assert context is None
    assert error is not None
    assert "project mismatch" in error
    assert "Alpha" in error


def test_parse_project_branch_args_missing_at_prefix(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    context, error = tg_context._parse_project_branch_args(
        "alpha dev",
        runtime=runtime,
        require_branch=False,
        chat_project=None,
    )
    assert context is None
    assert error == "branch must be prefixed with @"


def test_parse_project_branch_args_chat_project_branch_only(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    context, error = tg_context._parse_project_branch_args(
        "@feature",
        runtime=runtime,
        require_branch=True,
        chat_project="alpha",
    )
    assert error is None
    assert context == RunContext(project="alpha", branch="feature")


def test_format_ctx_status_includes_sessions(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    runtime = cfg.runtime
    snapshot = TopicThreadSnapshot(
        chat_id=cfg.chat_id,
        thread_id=1,
        context=None,
        sessions={"b": "token", "a": "token2"},
        topic_title=None,
        default_engine=None,
    )
    text = tg_context._format_ctx_status(
        cfg=cfg,
        runtime=runtime,
        bound=None,
        resolved=RunContext(project="alpha", branch="main"),
        context_source="directives",
        snapshot=snapshot,
        chat_project=None,
    )
    assert "topics: enabled" in text
    assert "bound ctx: none" in text
    assert "resolved ctx: Alpha @main" in text
    assert "note: unbound topic" in text
    assert "sessions: a, b" in text


def test_merge_topic_context() -> None:
    assert tg_context._merge_topic_context(chat_project=None, bound=None) is None
    assert tg_context._merge_topic_context(
        chat_project="alpha",
        bound=None,
    ) == RunContext(project="alpha", branch=None)
    assert tg_context._merge_topic_context(
        chat_project="alpha",
        bound=RunContext(project=None, branch="dev"),
    ) == RunContext(project="alpha", branch="dev")
