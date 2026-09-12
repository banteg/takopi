import pytest

from takopi.model import ResumeToken
from takopi.router import AutoRouter, RunnerEntry, aclose_runners
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


class _FakeRunner:
    """Minimal structural Runner; deliberately has no aclose hook."""

    engine = "fake"

    def is_resume_line(self, line: str) -> bool:
        return False

    def format_resume(self, token: ResumeToken) -> str:
        return ""

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        return None

    async def run(self, prompt: str, resume: ResumeToken | None):
        if False:  # pragma: no cover
            yield


class _Closable(_FakeRunner):
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def aclose(self, *, force: bool = False) -> None:
        self.calls.append(force)


class _Boom(_FakeRunner):
    async def aclose(self, *, force: bool = False) -> None:
        raise RuntimeError("boom")


@pytest.mark.anyio
async def test_aclose_runners_skips_absent_hooks_and_isolates_errors() -> None:
    closable = _Closable()
    no_hook = _FakeRunner()
    boom = _Boom()
    router = AutoRouter(
        entries=[
            RunnerEntry(engine="a", runner=closable),
            RunnerEntry(engine="b", runner=no_hook),
            RunnerEntry(engine="c", runner=boom),
        ],
        default_engine="a",
    )

    await aclose_runners(router, force=True)

    # closable runner received force=True; the failing one did not abort others
    assert closable.calls == [True]
