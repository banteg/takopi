import uuid

import anyio
import pytest

from takopi.runner_bridge import ExecBridgeConfig, IncomingMessage, handle_message
from takopi.markdown import MarkdownParts, MarkdownPresenter
from takopi.model import ResumeToken, TakopiEvent
from takopi.telegram.render import prepare_telegram
from takopi.runners.codex import CodexRunner
from takopi.runners.mock import Advance, Emit, Raise, Return, ScriptRunner, Wait
from takopi.settings import load_settings, require_telegram
from takopi.transport import MessageRef, RenderedMessage, SendOptions
from tests.factories import action_completed, action_started

CODEX_ENGINE = "codex"


class FakeTransport:
    def __init__(self) -> None:
        self._next_id = 1
        self.send_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.delete_calls: list[MessageRef] = []

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef:
        ref = MessageRef(channel_id=channel_id, message_id=self._next_id)
        self._next_id += 1
        self.send_calls.append(
            {
                "ref": ref,
                "channel_id": channel_id,
                "message": message,
                "options": options,
            }
        )
        return ref

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef:
        self.edit_calls.append({"ref": ref, "message": message, "wait": wait})
        return ref

    async def delete(self, *, ref: MessageRef) -> bool:
        self.delete_calls.append(ref)
        return True

    async def close(self) -> None:
        return None


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def set(self, value: float) -> None:
        self._now = value


def _return_runner(
    *, answer: str = "ok", resume_value: str | None = None
) -> ScriptRunner:
    return ScriptRunner(
        [Return(answer=answer)],
        engine=CODEX_ENGINE,
        resume_value=resume_value,
    )


def test_require_telegram_rejects_empty_token(tmp_path) -> None:
    from takopi.config import ConfigError

    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n\n[transports.telegram]\n'
        'bot_token = "   "\nchat_id = 123\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="bot_token"):
        settings, _ = load_settings(config_path)
        require_telegram(settings, config_path)


def test_load_settings_rejects_string_chat_id(tmp_path) -> None:
    from takopi.config import ConfigError

    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n\n[transports.telegram]\n'
        'bot_token = "token"\nchat_id = "123"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="chat_id"):
        load_settings(config_path)


def test_codex_extract_resume_finds_command() -> None:
    uuid = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    text = f"`codex resume {uuid}`"

    assert runner.extract_resume(text) == ResumeToken(engine=CODEX_ENGINE, value=uuid)


def test_codex_extract_resume_uses_last_resume_line() -> None:
    uuid_first = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    uuid_last = "123e4567-e89b-12d3-a456-426614174000"
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    text = f"`codex resume {uuid_first}`\n\n`codex resume {uuid_last}`"

    assert runner.extract_resume(text) == ResumeToken(
        engine=CODEX_ENGINE, value=uuid_last
    )


def test_codex_extract_resume_ignores_malformed_resume_line() -> None:
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    text = "codex resume"

    assert runner.extract_resume(text) is None


def test_codex_extract_resume_accepts_plain_line() -> None:
    uuid = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    text = f"codex resume {uuid}"

    assert runner.extract_resume(text) == ResumeToken(engine=CODEX_ENGINE, value=uuid)


def test_codex_extract_resume_accepts_uuid7() -> None:
    uuid7 = getattr(uuid, "uuid7", None)
    assert uuid7 is not None
    token = str(uuid7())
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    text = f"`codex resume {token}`"

    assert runner.extract_resume(text) == ResumeToken(engine=CODEX_ENGINE, value=token)


def test_prepare_telegram_trims_body_preserves_footer() -> None:
    body_limit = 3500
    parts = MarkdownParts(
        header="header",
        body="x" * (body_limit + 100),
        footer="footer",
    )

    rendered, _ = prepare_telegram(parts)

    chunks = [chunk for chunk in rendered.split("\n\n") if chunk]
    assert chunks[0] == "header"
    assert chunks[-1].rstrip() == "footer"
    assert len(chunks[1]) == body_limit
    assert chunks[1].endswith("…")


def test_prepare_telegram_preserves_entities_on_truncate() -> None:
    body_limit = 3500
    parts = MarkdownParts(
        header="h",
        body="**bold** " + ("x" * (body_limit + 100)),
    )

    _, entities = prepare_telegram(parts)

    assert any(e.get("type") == "bold" for e in entities)


@pytest.mark.anyio
async def test_final_notify_sends_loud_final_message() -> None:
    transport = FakeTransport()
    runner = _return_runner(answer="ok")
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )

    await handle_message(
        cfg,
        runner=runner,
        incoming=IncomingMessage(channel_id=123, message_id=10, text="hi"),
        resume_token=None,
    )

    assert len(transport.send_calls) == 2
    assert transport.send_calls[0]["options"].notify is False
    assert transport.send_calls[1]["options"].notify is True


@pytest.mark.anyio
async def test_handle_message_strips_resume_line_from_prompt() -> None:
    transport = FakeTransport()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    resume = ResumeToken(engine=CODEX_ENGINE, value="sid")
    text = "do this\n`codex resume sid`\nand that"

    await handle_message(
        cfg,
        runner=runner,
        incoming=IncomingMessage(channel_id=123, message_id=10, text=text),
        resume_token=resume,
    )

    assert runner.calls
    prompt, passed_resume = runner.calls[0]
    assert prompt == "do this\nand that"
    assert passed_resume == resume


@pytest.mark.anyio
async def test_long_final_message_edits_progress_message() -> None:
    transport = FakeTransport()
    runner = _return_runner(answer="x" * 10_000)
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=False,
    )

    await handle_message(
        cfg,
        runner=runner,
        incoming=IncomingMessage(channel_id=123, message_id=10, text="hi"),
        resume_token=None,
    )

    assert len(transport.send_calls) == 1
    assert transport.send_calls[0]["options"].notify is False
    assert transport.edit_calls
    assert "done" in transport.edit_calls[-1]["message"].text.lower()


@pytest.mark.anyio
async def test_progress_edits_are_best_effort() -> None:
    transport = FakeTransport()
    clock = _FakeClock()
    events: list[TakopiEvent] = [
        action_started("item_0", "command", "echo 1"),
        action_started("item_1", "command", "echo 2"),
    ]
    runner = ScriptRunner(
        [
            Emit(events[0], at=0.2),
            Emit(events[1], at=0.4),
            Advance(1.0),
            Return(answer="ok"),
        ],
        engine=CODEX_ENGINE,
        advance=clock.set,
    )
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )

    await handle_message(
        cfg,
        runner=runner,
        incoming=IncomingMessage(channel_id=123, message_id=10, text="hi"),
        resume_token=None,
        clock=clock,
    )

    assert transport.edit_calls
    assert all(call["wait"] is False for call in transport.edit_calls)
    assert "working" in transport.edit_calls[-1]["message"].text.lower()


@pytest.mark.anyio
async def test_bridge_flow_sends_progress_edits_and_final_resume() -> None:
    transport = FakeTransport()
    clock = _FakeClock()
    events: list[TakopiEvent] = [
        action_started("item_0", "command", "echo ok"),
        action_completed(
            "item_0",
            "command",
            "echo ok",
            ok=True,
            detail={"exit_code": 0},
        ),
    ]
    session_id = "123e4567-e89b-12d3-a456-426614174000"
    runner = ScriptRunner(
        [
            Emit(events[0], at=0.0),
            Emit(events[1], at=2.1),
            Return(answer="done"),
        ],
        engine=CODEX_ENGINE,
        advance=clock.set,
        resume_value=session_id,
    )
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )

    await handle_message(
        cfg,
        runner=runner,
        incoming=IncomingMessage(channel_id=123, message_id=42, text="do it"),
        resume_token=None,
        clock=clock,
    )

    assert transport.send_calls[0]["options"].reply_to.message_id == 42
    assert "starting" in transport.send_calls[0]["message"].text
    assert "codex" in transport.send_calls[0]["message"].text
    assert len(transport.edit_calls) >= 1
    assert session_id in transport.send_calls[-1]["message"].text
    assert "codex resume" in transport.send_calls[-1]["message"].text.lower()
    assert transport.send_calls[-1]["options"].replace == transport.send_calls[0]["ref"]


@pytest.mark.anyio
async def test_final_message_includes_ctx_line() -> None:
    transport = FakeTransport()
    clock = _FakeClock()
    session_id = "123e4567-e89b-12d3-a456-426614174000"
    runner = ScriptRunner(
        [Return(answer="done")],
        engine=CODEX_ENGINE,
        resume_value=session_id,
    )
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )

    await handle_message(
        cfg,
        runner=runner,
        incoming=IncomingMessage(channel_id=123, message_id=42, text="do it"),
        resume_token=None,
        context_line="`ctx: takopi @feat/api`",
        clock=clock,
    )

    assert transport.send_calls
    final_text = transport.send_calls[-1]["message"].text
    assert "`ctx: takopi @feat/api`" in final_text
    assert "codex resume" in final_text.lower()


@pytest.mark.anyio
async def test_handle_message_cancelled_renders_cancelled_state() -> None:
    transport = FakeTransport()
    session_id = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    hold = anyio.Event()
    runner = ScriptRunner(
        [Wait(hold)],
        engine=CODEX_ENGINE,
        resume_value=session_id,
    )
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    running_tasks: dict = {}

    async def run_handle_message() -> None:
        await handle_message(
            cfg,
            runner=runner,
            incoming=IncomingMessage(
                channel_id=123, message_id=10, text="do something"
            ),
            resume_token=None,
            running_tasks=running_tasks,
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_handle_message)
        for _ in range(100):
            if running_tasks:
                break
            await anyio.sleep(0)
        assert running_tasks
        running_task = running_tasks[next(iter(running_tasks))]
        with anyio.fail_after(1):
            await running_task.resume_ready.wait()
        running_task.cancel_requested.set()

    assert len(transport.send_calls) == 1  # Progress message
    assert len(transport.edit_calls) >= 1
    last_edit = transport.edit_calls[-1]["message"].text
    assert "cancelled" in last_edit.lower()
    assert session_id in last_edit


@pytest.mark.anyio
async def test_handle_message_error_preserves_resume_token() -> None:
    transport = FakeTransport()
    session_id = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    runner = ScriptRunner(
        [Raise(RuntimeError("boom"))],
        engine=CODEX_ENGINE,
        resume_value=session_id,
    )
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )

    await handle_message(
        cfg,
        runner=runner,
        incoming=IncomingMessage(channel_id=123, message_id=10, text="do something"),
        resume_token=None,
    )

    assert transport.edit_calls
    last_edit = transport.edit_calls[-1]["message"].text
    assert "error" in last_edit.lower()
    assert session_id in last_edit
    assert "codex resume" in last_edit.lower()
