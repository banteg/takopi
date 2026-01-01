from takopi.model import ResumeToken
from takopi.router import AutoRouter, RunnerEntry
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
