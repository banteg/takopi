from pathlib import Path

from takopi.config import ProjectConfig, ProjectsConfig
from takopi.context import RunContext
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.transport_runtime import TransportRuntime


def _make_runtime(*, project_default_engine: str | None = None) -> TransportRuntime:
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
        path=Path("."),
        worktrees_dir=Path(".worktrees"),
        default_engine=project_default_engine,
    )
    projects = ProjectsConfig(projects={"proj": project}, default_project=None)
    return TransportRuntime(router=router, projects=projects)


def test_resolve_engine_uses_project_default() -> None:
    runtime = _make_runtime(project_default_engine="pi")
    engine = runtime.resolve_engine(
        engine_override=None,
        context=RunContext(project="proj"),
    )
    assert engine == "pi"


def test_resolve_engine_prefers_override() -> None:
    runtime = _make_runtime(project_default_engine="pi")
    engine = runtime.resolve_engine(
        engine_override="codex",
        context=RunContext(project="proj"),
    )
    assert engine == "codex"


def test_resolve_message_defaults_to_chat_project() -> None:
    codex = ScriptRunner([Return(answer="ok")], engine="codex")
    router = AutoRouter(
        entries=[RunnerEntry(engine=codex.engine, runner=codex)],
        default_engine=codex.engine,
    )
    project = ProjectConfig(
        alias="proj",
        path=Path("."),
        worktrees_dir=Path(".worktrees"),
        chat_id=-42,
    )
    projects = ProjectsConfig(
        projects={"proj": project},
        default_project=None,
        chat_map={-42: "proj"},
    )
    runtime = TransportRuntime(router=router, projects=projects)

    resolved = runtime.resolve_message(
        text="hello",
        reply_text=None,
        chat_id=-42,
    )

    assert resolved.context == RunContext(project="proj", branch=None)


def test_resolve_message_uses_ambient_context() -> None:
    runtime = _make_runtime()
    ambient = RunContext(project="proj", branch="feat/ambient")

    resolved = runtime.resolve_message(
        text="hello",
        reply_text=None,
        ambient_context=ambient,
    )

    assert resolved.context == ambient
    assert resolved.context_source == "ambient"


def test_resolve_message_reply_ctx_overrides_ambient() -> None:
    runtime = _make_runtime()
    ambient = RunContext(project="proj", branch="feat/ambient")

    resolved = runtime.resolve_message(
        text="hello",
        reply_text="`ctx: proj @reply`",
        ambient_context=ambient,
    )

    assert resolved.context == RunContext(project="proj", branch="reply")
    assert resolved.context_source == "reply_ctx"


def test_resolve_message_directives_override_ambient() -> None:
    runtime = _make_runtime()
    ambient = RunContext(project="proj", branch="feat/ambient")

    resolved = runtime.resolve_message(
        text="/proj @main do it",
        reply_text=None,
        ambient_context=ambient,
    )

    assert resolved.context == RunContext(project="proj", branch="main")
    assert resolved.context_source == "directives"


def test_resolve_message_branch_directive_merges_with_ambient_project() -> None:
    runtime = _make_runtime()
    ambient = RunContext(project="proj", branch="feat/ambient")

    resolved = runtime.resolve_message(
        text="@hotfix do it",
        reply_text=None,
        ambient_context=ambient,
    )

    assert resolved.context == RunContext(project="proj", branch="hotfix")
    assert resolved.context_source == "directives"


def test_resolve_message_project_directive_clears_ambient_branch() -> None:
    codex = ScriptRunner([Return(answer="ok")], engine="codex")
    router = AutoRouter(
        entries=[RunnerEntry(engine=codex.engine, runner=codex)],
        default_engine=codex.engine,
    )
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=Path("."),
                worktrees_dir=Path(".worktrees"),
            ),
            "other": ProjectConfig(
                alias="other",
                path=Path("."),
                worktrees_dir=Path(".worktrees"),
            ),
        },
        default_project=None,
    )
    runtime = TransportRuntime(router=router, projects=projects)
    ambient = RunContext(project="proj", branch="feat/ambient")

    resolved = runtime.resolve_message(
        text="/other do it",
        reply_text=None,
        ambient_context=ambient,
    )

    assert resolved.context == RunContext(project="other", branch=None)
    assert resolved.context_source == "directives"
