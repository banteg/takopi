import uuid

import anyio
import pytest

from takopi.bridge import _build_bot_commands, _strip_engine_command
from takopi.model import EngineId, ResumeToken, TakopiEvent
from takopi.render import MarkdownParts, prepare_telegram
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.codex import CodexRunner
from takopi.runners.mock import Advance, Emit, Raise, Return, ScriptRunner, Sleep, Wait
from tests.factories import action_completed, action_started

CODEX_ENGINE = EngineId("codex")


def _make_router(runner) -> AutoRouter:
    return AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )


def _patch_config(monkeypatch, config):
    from pathlib import Path

    from takopi import cli

    monkeypatch.setattr(
        cli,
        "load_telegram_config",
        lambda: (config, Path("takopi.toml")),
    )


def test_parse_bridge_config_rejects_empty_token(monkeypatch) -> None:
    from takopi import cli

    _patch_config(monkeypatch, {"bot_token": "   ", "chat_id": 123})

    with pytest.raises(cli.ConfigError, match="bot_token"):
        cli._parse_bridge_config(
            final_notify=True,
            default_engine_override=None,
        )


def test_parse_bridge_config_rejects_string_chat_id(monkeypatch) -> None:
    from takopi import cli

    _patch_config(monkeypatch, {"bot_token": "token", "chat_id": "123"})

    with pytest.raises(cli.ConfigError, match="chat_id"):
        cli._parse_bridge_config(
            final_notify=True,
            default_engine_override=None,
        )


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


def test_strip_engine_command_inline() -> None:
    text, engine = _strip_engine_command(
        "/claude do it", engine_ids=("codex", "claude")
    )
    assert engine == "claude"
    assert text == "do it"


def test_strip_engine_command_newline() -> None:
    text, engine = _strip_engine_command(
        "/codex\nhello", engine_ids=("codex", "claude")
    )
    assert engine == "codex"
    assert text == "hello"


def test_strip_engine_command_ignores_unknown() -> None:
    text, engine = _strip_engine_command("/unknown hi", engine_ids=("codex", "claude"))
    assert engine is None
    assert text == "/unknown hi"


def test_strip_engine_command_bot_suffix() -> None:
    text, engine = _strip_engine_command(
        "/claude@bunny_agent_bot hi", engine_ids=("claude",)
    )
    assert engine == "claude"
    assert text == "hi"


def test_strip_engine_command_only_first_non_empty_line() -> None:
    text, engine = _strip_engine_command(
        "hello\n/claude hi", engine_ids=("codex", "claude")
    )
    assert engine is None
    assert text == "hello\n/claude hi"


def test_build_bot_commands_includes_cancel_and_engine() -> None:
    runner = ScriptRunner(
        [Return(answer="ok")], engine=CODEX_ENGINE, resume_value="sid"
    )
    router = _make_router(runner)
    commands = _build_bot_commands(router)

    assert {"command": "cancel", "description": "cancel run"} in commands
    assert any(cmd["command"] == "codex" for cmd in commands)


class _FakeBot:
    def __init__(self) -> None:
        self._next_id = 1
        self.command_calls: list[dict] = []
        self.send_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
    ) -> dict:
        self.send_calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "disable_notification": disable_notification,
                "entities": entities,
                "parse_mode": parse_mode,
            }
        )
        msg_id = self._next_id
        self._next_id += 1
        return {"message_id": msg_id}

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
    ) -> dict:
        self.edit_calls.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "entities": entities,
                "parse_mode": parse_mode,
            }
        )
        return {"message_id": message_id}

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        self.delete_calls.append({"chat_id": chat_id, "message_id": message_id})
        return True

    async def set_my_commands(
        self,
        commands: list[dict],
        *,
        scope: dict | None = None,
        language_code: str | None = None,
    ) -> bool:
        self.command_calls.append(
            {
                "commands": commands,
                "scope": scope,
                "language_code": language_code,
            }
        )
        return True

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[dict] | None:
        _ = offset
        _ = timeout_s
        _ = allowed_updates
        return []

    async def close(self) -> None:
        return None


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._sleep_until: float | None = None
        self._sleep_event: anyio.Event | None = None
        self.sleep_calls = 0

    def __call__(self) -> float:
        return self._now

    def set(self, value: float) -> None:
        self._now = value
        if self._sleep_until is None or self._sleep_event is None:
            return
        if self._sleep_until <= self._now:
            self._sleep_event.set()
            self._sleep_until = None
            self._sleep_event = None

    async def sleep(self, delay: float) -> None:
        self.sleep_calls += 1
        if delay <= 0:
            await anyio.sleep(0)
            return
        self._sleep_until = self._now + delay
        self._sleep_event = anyio.Event()
        await self._sleep_event.wait()


def _return_runner(
    *, answer: str = "ok", resume_value: str | None = None
) -> ScriptRunner:
    return ScriptRunner(
        [Return(answer=answer)],
        engine=CODEX_ENGINE,
        resume_value=resume_value,
    )


@pytest.mark.anyio
async def test_final_notify_sends_loud_final_message() -> None:
    from takopi.bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )

    await handle_message(
        cfg,
        runner=runner,
        chat_id=123,
        user_msg_id=10,
        text="hi",
        resume_token=None,
    )

    assert len(bot.send_calls) == 2
    assert bot.send_calls[0]["disable_notification"] is True
    assert bot.send_calls[1]["disable_notification"] is False


@pytest.mark.anyio
async def test_handle_message_strips_resume_line_from_prompt() -> None:
    from takopi.bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )
    resume = ResumeToken(engine=CODEX_ENGINE, value="sid")
    text = "do this\n`codex resume sid`\nand that"

    await handle_message(
        cfg,
        runner=runner,
        chat_id=123,
        user_msg_id=10,
        text=text,
        resume_token=resume,
    )

    assert runner.calls
    prompt, passed_resume = runner.calls[0]
    assert prompt == "do this\nand that"
    assert passed_resume == resume


@pytest.mark.anyio
async def test_long_final_message_edits_progress_message() -> None:
    from takopi.bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    runner = _return_runner(answer="x" * 10_000)
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=False,
        startup_msg="",
    )

    await handle_message(
        cfg,
        runner=runner,
        chat_id=123,
        user_msg_id=10,
        text="hi",
        resume_token=None,
    )

    assert len(bot.send_calls) == 1
    assert bot.send_calls[0]["disable_notification"] is True
    assert len(bot.edit_calls) == 1


@pytest.mark.anyio
async def test_progress_edits_are_rate_limited() -> None:
    from takopi.bridge import BridgeConfig, handle_message

    bot = _FakeBot()
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
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )

    await handle_message(
        cfg,
        runner=runner,
        chat_id=123,
        user_msg_id=10,
        text="hi",
        resume_token=None,
        clock=clock,
        sleep=clock.sleep,
        progress_edit_every=1.0,
    )

    assert len(bot.edit_calls) == 1
    assert "echo 2" in bot.edit_calls[0]["text"]


@pytest.mark.anyio
async def test_progress_edits_do_not_sleep_again_without_new_events() -> None:
    from takopi.bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    clock = _FakeClock()
    hold = anyio.Event()
    events: list[TakopiEvent] = [
        action_started("item_0", "command", "echo 1"),
        action_started("item_1", "command", "echo 2"),
    ]
    runner = ScriptRunner(
        [
            Emit(events[0], at=0.2),
            Emit(events[1], at=0.4),
            Wait(hold),
            Return(answer="ok"),
        ],
        engine=CODEX_ENGINE,
        advance=clock.set,
    )
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )

    async def run_handle_message() -> None:
        await handle_message(
            cfg,
            runner=runner,
            chat_id=123,
            user_msg_id=10,
            text="hi",
            resume_token=None,
            clock=clock,
            sleep=clock.sleep,
            progress_edit_every=1.0,
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_handle_message)

        for _ in range(100):
            if clock._sleep_until is not None:
                break
            await anyio.sleep(0)

        assert clock._sleep_until == pytest.approx(1.0)

        clock.set(1.0)

        for _ in range(100):
            if bot.edit_calls:
                break
            await anyio.sleep(0)

        assert len(bot.edit_calls) == 1

        for _ in range(5):
            await anyio.sleep(0)

        assert clock.sleep_calls == 1
        assert clock._sleep_until is None

        hold.set()


@pytest.mark.anyio
async def test_bridge_flow_sends_progress_edits_and_final_resume() -> None:
    from takopi.bridge import BridgeConfig, handle_message

    bot = _FakeBot()
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
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )

    await handle_message(
        cfg,
        runner=runner,
        chat_id=123,
        user_msg_id=42,
        text="do it",
        resume_token=None,
        clock=clock,
        sleep=clock.sleep,
        progress_edit_every=1.0,
    )

    assert bot.send_calls[0]["reply_to_message_id"] == 42
    assert "starting" in bot.send_calls[0]["text"]
    assert "codex" in bot.send_calls[0]["text"]
    assert len(bot.edit_calls) >= 1
    assert session_id in bot.send_calls[-1]["text"]
    assert "codex resume" in bot.send_calls[-1]["text"].lower()
    assert len(bot.delete_calls) == 1


@pytest.mark.anyio
async def test_handle_cancel_without_reply_prompts_user() -> None:
    from takopi.bridge import BridgeConfig, _handle_cancel

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )
    msg = {"chat": {"id": 123}, "message_id": 10}
    running_tasks: dict = {}

    await _handle_cancel(cfg, msg, running_tasks)

    assert len(bot.send_calls) == 1
    assert "reply to the progress message" in bot.send_calls[0]["text"]


@pytest.mark.anyio
async def test_handle_cancel_with_no_progress_message_says_nothing_running() -> None:
    from takopi.bridge import BridgeConfig, _handle_cancel

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )
    msg = {
        "chat": {"id": 123},
        "message_id": 10,
        "reply_to_message": {"text": "no message id"},
    }
    running_tasks: dict = {}

    await _handle_cancel(cfg, msg, running_tasks)

    assert len(bot.send_calls) == 1
    assert "nothing is currently running" in bot.send_calls[0]["text"]


@pytest.mark.anyio
async def test_handle_cancel_with_finished_task_says_nothing_running() -> None:
    from takopi.bridge import BridgeConfig, _handle_cancel

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )
    progress_id = 99
    msg = {
        "chat": {"id": 123},
        "message_id": 10,
        "reply_to_message": {"message_id": progress_id},
    }
    running_tasks: dict = {}  # Progress message not in running_tasks

    await _handle_cancel(cfg, msg, running_tasks)

    assert len(bot.send_calls) == 1
    assert "nothing is currently running" in bot.send_calls[0]["text"]


@pytest.mark.anyio
async def test_handle_cancel_cancels_running_task() -> None:
    from takopi.bridge import BridgeConfig, _handle_cancel

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )
    progress_id = 42
    msg = {
        "chat": {"id": 123},
        "message_id": 10,
        "reply_to_message": {"message_id": progress_id},
    }

    from takopi.bridge import RunningTask

    running_task = RunningTask()
    running_tasks = {progress_id: running_task}
    await _handle_cancel(cfg, msg, running_tasks)

    assert running_task.cancel_requested.is_set() is True
    assert len(bot.send_calls) == 0  # No error message sent


@pytest.mark.anyio
async def test_handle_cancel_only_cancels_matching_progress_message() -> None:
    from takopi.bridge import BridgeConfig, _handle_cancel

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )
    from takopi.bridge import RunningTask

    task_first = RunningTask()
    task_second = RunningTask()
    msg = {
        "chat": {"id": 123},
        "message_id": 10,
        "reply_to_message": {"message_id": 1},
    }
    running_tasks = {1: task_first, 2: task_second}

    await _handle_cancel(cfg, msg, running_tasks)

    assert task_first.cancel_requested.is_set() is True
    assert task_second.cancel_requested.is_set() is False
    assert len(bot.send_calls) == 0


def test_cancel_command_accepts_extra_text() -> None:
    from takopi.bridge import _is_cancel_command

    assert _is_cancel_command("/cancel now") is True
    assert _is_cancel_command("/cancel@takopi please") is True
    assert _is_cancel_command("/cancelled") is False


@pytest.mark.anyio
async def test_handle_message_cancelled_renders_cancelled_state() -> None:
    from takopi.bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    session_id = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    hold = anyio.Event()
    runner = ScriptRunner(
        [Wait(hold)],
        engine=CODEX_ENGINE,
        resume_value=session_id,
    )
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )
    running_tasks: dict = {}

    async def run_handle_message() -> None:
        await handle_message(
            cfg,
            runner=runner,
            chat_id=123,
            user_msg_id=10,
            text="do something",
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

    assert len(bot.send_calls) == 1  # Progress message
    assert len(bot.edit_calls) >= 1
    last_edit = bot.edit_calls[-1]["text"]
    assert "cancelled" in last_edit.lower()
    assert session_id in last_edit


@pytest.mark.anyio
async def test_handle_message_error_preserves_resume_token() -> None:
    from takopi.bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    session_id = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    runner = ScriptRunner(
        [Raise(RuntimeError("boom"))],
        engine=CODEX_ENGINE,
        resume_value=session_id,
    )
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )

    await handle_message(
        cfg,
        runner=runner,
        chat_id=123,
        user_msg_id=10,
        text="do something",
        resume_token=None,
    )

    assert bot.edit_calls
    last_edit = bot.edit_calls[-1]["text"]
    assert "error" in last_edit.lower()
    assert session_id in last_edit
    assert "codex resume" in last_edit.lower()


@pytest.mark.anyio
async def test_send_with_resume_waits_for_token() -> None:
    from takopi.bridge import RunningTask, _send_with_resume

    bot = _FakeBot()
    sent: list[tuple[int, int, str, ResumeToken | None]] = []

    async def enqueue(
        chat_id: int, user_msg_id: int, text: str, resume: ResumeToken
    ) -> None:
        sent.append((chat_id, user_msg_id, text, resume))

    running_task = RunningTask()

    async def trigger_resume() -> None:
        await anyio.sleep(0)
        running_task.resume = ResumeToken(engine=CODEX_ENGINE, value="abc123")
        running_task.resume_ready.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(trigger_resume)
        await _send_with_resume(
            bot,
            enqueue,
            running_task,
            123,
            10,
            "hello",
        )

    assert sent == [
        (123, 10, "hello", ResumeToken(engine=CODEX_ENGINE, value="abc123"))
    ]


@pytest.mark.anyio
async def test_send_with_resume_reports_when_missing() -> None:
    from takopi.bridge import RunningTask, _send_with_resume

    bot = _FakeBot()
    sent: list[tuple[int, int, str, ResumeToken | None]] = []

    async def enqueue(
        chat_id: int, user_msg_id: int, text: str, resume: ResumeToken
    ) -> None:
        sent.append((chat_id, user_msg_id, text, resume))

    running_task = RunningTask()
    running_task.done.set()

    await _send_with_resume(
        bot,
        enqueue,
        running_task,
        123,
        10,
        "hello",
    )

    assert sent == []
    assert bot.send_calls
    assert "resume token" in bot.send_calls[-1]["text"].lower()


@pytest.mark.anyio
async def test_run_main_loop_routes_reply_to_running_resume() -> None:
    from takopi.bridge import BridgeConfig, run_main_loop

    progress_ready = anyio.Event()
    stop_polling = anyio.Event()
    reply_ready = anyio.Event()
    hold = anyio.Event()

    class _BotWithProgress(_FakeBot):
        def __init__(self) -> None:
            super().__init__()
            self.progress_id: int | None = None

        async def send_message(
            self,
            chat_id: int,
            text: str,
            reply_to_message_id: int | None = None,
            disable_notification: bool | None = False,
            entities: list[dict] | None = None,
            parse_mode: str | None = None,
        ) -> dict:
            msg = await super().send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
                disable_notification=disable_notification,
                entities=entities,
                parse_mode=parse_mode,
            )
            if self.progress_id is None and reply_to_message_id is not None:
                self.progress_id = int(msg["message_id"])
                progress_ready.set()
            return msg

    bot = _BotWithProgress()
    resume_value = "abc123"
    runner = ScriptRunner(
        [Wait(hold), Sleep(0.05), Return(answer="ok")],
        engine=CODEX_ENGINE,
        resume_value=resume_value,
    )
    cfg = BridgeConfig(
        bot=bot,
        router=_make_router(runner),
        chat_id=123,
        final_notify=True,
        startup_msg="",
    )

    async def poller(_cfg: BridgeConfig):
        yield {
            "message_id": 1,
            "text": "first",
            "chat": {"id": 123},
            "from": {"id": 123},
        }
        await progress_ready.wait()
        assert bot.progress_id is not None
        reply_ready.set()
        yield {
            "message_id": 2,
            "text": "followup",
            "chat": {"id": 123},
            "from": {"id": 123},
            "reply_to_message": {"message_id": bot.progress_id},
        }
        await stop_polling.wait()

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_main_loop, cfg, poller)
        try:
            with anyio.fail_after(2):
                await reply_ready.wait()
            await anyio.sleep(0)
            hold.set()
            with anyio.fail_after(2):
                while len(runner.calls) < 2:
                    await anyio.sleep(0)
            assert runner.calls[1][1] == ResumeToken(
                engine=CODEX_ENGINE, value=resume_value
            )
        finally:
            hold.set()
            stop_polling.set()
            tg.cancel_scope.cancel()
