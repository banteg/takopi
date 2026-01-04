import pytest

from takopi.model import ResumeToken
from takopi.router import AutoRouter, RunnerEntry, RunnerUnavailableError
from takopi.runners.claude import ClaudeRunner
from takopi.runners.codex import CodexRunner


def _router() -> tuple[AutoRouter, ClaudeRunner, CodexRunner]:
    codex = CodexRunner(codex_cmd="codex", extra_args=[])
    claude = ClaudeRunner(claude_cmd="claude")
    router = AutoRouter(
        entries=[
            RunnerEntry(engine=claude.engine, runner=claude),
            RunnerEntry(engine=codex.engine, runner=codex),
        ],
        default_engine=codex.engine,
    )
    return router, claude, codex


def test_router_resolves_text_before_reply() -> None:
    router, _claude, _codex = _router()
    token = router.resolve_resume("`codex resume abc`", "`claude --resume def`")

    assert token == ResumeToken(engine="codex", value="abc")


def test_router_poll_order_selects_first_matching_runner() -> None:
    router, _claude, _codex = _router()
    text = "`codex resume abc`\n`claude --resume def`"

    token = router.resolve_resume(text, None)

    assert token == ResumeToken(engine="claude", value="def")


def test_router_resolves_reply_text_when_text_missing() -> None:
    router, _claude, _codex = _router()

    token = router.resolve_resume(None, "`codex resume xyz`")

    assert token == ResumeToken(engine="codex", value="xyz")


def test_router_is_resume_line_union() -> None:
    router, _claude, _codex = _router()

    assert router.is_resume_line("`codex resume abc`")
    assert router.is_resume_line("claude --resume def")


class TestRunnerUnavailableError:
    def test_without_issue(self) -> None:
        err = RunnerUnavailableError("codex")
        assert "codex" in str(err)
        assert err.engine == "codex"
        assert err.issue is None

    def test_with_issue(self) -> None:
        err = RunnerUnavailableError("codex", "not installed")
        assert "codex" in str(err)
        assert "not installed" in str(err)
        assert err.engine == "codex"
        assert err.issue == "not installed"


class TestAutoRouterInit:
    def test_empty_entries_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one runner"):
            AutoRouter(entries=[], default_engine="codex")

    def test_duplicate_engine_raises(self) -> None:
        codex = CodexRunner(codex_cmd="codex", extra_args=[])
        with pytest.raises(ValueError, match="duplicate runner"):
            AutoRouter(
                entries=[
                    RunnerEntry(engine="codex", runner=codex),
                    RunnerEntry(engine="codex", runner=codex),
                ],
                default_engine="codex",
            )

    def test_unknown_default_raises(self) -> None:
        codex = CodexRunner(codex_cmd="codex", extra_args=[])
        with pytest.raises(ValueError, match="default engine"):
            AutoRouter(
                entries=[RunnerEntry(engine="codex", runner=codex)],
                default_engine="unknown",
            )


class TestAutoRouterProperties:
    def test_entries(self) -> None:
        router, _claude, _codex = _router()
        assert len(router.entries) == 2

    def test_available_entries(self) -> None:
        codex = CodexRunner(codex_cmd="codex", extra_args=[])
        claude = ClaudeRunner(claude_cmd="claude")
        router = AutoRouter(
            entries=[
                RunnerEntry(engine="claude", runner=claude, available=False),
                RunnerEntry(engine="codex", runner=codex, available=True),
            ],
            default_engine="codex",
        )
        available = router.available_entries
        assert len(available) == 1
        assert available[0].engine == "codex"

    def test_engine_ids(self) -> None:
        router, _claude, _codex = _router()
        assert "claude" in router.engine_ids
        assert "codex" in router.engine_ids

    def test_default_entry(self) -> None:
        router, _claude, _codex = _router()
        assert router.default_entry.engine == "codex"


class TestAutoRouterMethods:
    def test_entry_for_engine_none_returns_default(self) -> None:
        router, _claude, _codex = _router()
        entry = router.entry_for_engine(None)
        assert entry.engine == "codex"

    def test_entry_for_engine_unknown_raises(self) -> None:
        router, _claude, _codex = _router()
        with pytest.raises(RunnerUnavailableError, match="not configured"):
            router.entry_for_engine("unknown")

    def test_entry_for_with_token(self) -> None:
        router, _claude, _codex = _router()
        token = ResumeToken(engine="claude", value="abc")
        entry = router.entry_for(token)
        assert entry.engine == "claude"

    def test_entry_for_none(self) -> None:
        router, _claude, _codex = _router()
        entry = router.entry_for(None)
        assert entry.engine == "codex"

    def test_runner_for_unavailable_raises(self) -> None:
        codex = CodexRunner(codex_cmd="codex", extra_args=[])
        claude = ClaudeRunner(claude_cmd="claude")
        router = AutoRouter(
            entries=[
                RunnerEntry(
                    engine="claude", runner=claude, available=False, issue="not found"
                ),
                RunnerEntry(engine="codex", runner=codex),
            ],
            default_engine="codex",
        )
        token = ResumeToken(engine="claude", value="abc")
        with pytest.raises(RunnerUnavailableError, match="not found"):
            router.runner_for(token)

    def test_runner_for_available(self) -> None:
        router, _claude, _codex = _router()
        runner = router.runner_for(None)
        assert runner.engine == "codex"

    def test_format_resume(self) -> None:
        router, _claude, _codex = _router()
        token = ResumeToken(engine="codex", value="abc123")
        formatted = router.format_resume(token)
        assert "abc123" in formatted

    def test_extract_resume_empty(self) -> None:
        router, _claude, _codex = _router()
        assert router.extract_resume(None) is None
        assert router.extract_resume("") is None

    def test_extract_resume_no_match(self) -> None:
        router, _claude, _codex = _router()
        assert router.extract_resume("just some text") is None
