import uuid
from typing import cast

import anyio
import pytest

from takopi import engines
from takopi.exec_bridge import prepare_telegram, truncate_for_telegram
from takopi.runners.base import ResumeToken, TakopiEvent
from takopi.runners.codex import CodexRunner
from takopi.runners.mock import Advance, Emit, Raise, Return, ScriptRunner, Sleep, Wait


def _patch_config(monkeypatch, config):
    from pathlib import Path

    from takopi import exec_bridge

    monkeypatch.setattr(
        exec_bridge,
        "load_telegram_config",
        lambda: (config, Path("takopi.toml")),
    )


def test_parse_bridge_config_rejects_empty_token(monkeypatch) -> None:
    from takopi import exec_bridge

    _patch_config(monkeypatch, {"bot_token": "   ", "chat_id": 123})

    with pytest.raises(exec_bridge.ConfigError, match="bot_token"):
        exec_bridge._parse_bridge_config(
            final_notify=True,
            backend=engines.get_backend("codex"),
            engine_overrides={},
        )


def test_parse_bridge_config_rejects_string_chat_id(monkeypatch) -> None:
    from takopi import exec_bridge

    _patch_config(monkeypatch, {"bot_token": "token", "chat_id": "123"})

    with pytest.raises(exec_bridge.ConfigError, match="chat_id"):
        exec_bridge._parse_bridge_config(
            final_notify=True,
            backend=engines.get_backend("codex"),
            engine_overrides={},
        )


def test_codex_extract_resume_finds_command() -> None:
    uuid = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    text = f"`codex resume {uuid}`"

    assert runner.extract_resume(text) == ResumeToken(engine="codex", value=uuid)


def test_codex_extract_resume_uses_last_resume_line() -> None:
    uuid_first = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    uuid_last = "123e4567-e89b-12d3-a456-426614174000"
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    text = f"`codex resume {uuid_first}`\n\n`codex resume {uuid_last}`"

    assert runner.extract_resume(text) == ResumeToken(engine="codex", value=uuid_last)


def test_codex_extract_resume_ignores_malformed_resume_line() -> None:
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    text = "codex resume"

    assert runner.extract_resume(text) is None


def test_codex_extract_resume_accepts_plain_line() -> None:
    uuid = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    text = f"codex resume {uuid}"

    assert runner.extract_resume(text) == ResumeToken(engine="codex", value=uuid)


def test_codex_extract_resume_accepts_uuid7() -> None:
    uuid7 = getattr(uuid, "uuid7", None)
    assert uuid7 is not None
    token = str(uuid7())
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    text = f"`codex resume {token}`"

    assert runner.extract_resume(text) == ResumeToken(engine="codex", value=token)


def test_truncate_for_telegram_preserves_resume_line() -> None:
    uuid = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    md = ("x" * 10_000) + f"\n`codex resume {uuid}`"

    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    out = truncate_for_telegram(md, 400, is_resume_line=runner.is_resume_line)

    assert len(out) <= 400
    assert f"codex resume {uuid}" in out
    assert out.rstrip().endswith(f"`codex resume {uuid}`")


def test_truncate_for_telegram_keeps_last_non_empty_line() -> None:
    md = "intro\n\n" + ("x" * 500) + "\nlast line"

    out = truncate_for_telegram(md, 120, is_resume_line=lambda _line: False)

    assert len(out) <= 120
    assert out.rstrip().endswith("last line")


def test_prepare_telegram_drops_entities_on_truncate() -> None:
    md = ("**bold** " * 200).strip()

    rendered, entities = prepare_telegram(md, limit=40)

    assert len(rendered) <= 40
    assert entities is None


class _FakeBot:
    def __init__(self) -> None:
        self._next_id = 1
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


class _SendStream:
    def __init__(self) -> None:
        self.sent: list[tuple[int, int, str, ResumeToken | None]] = []

    async def send(self, item: tuple[int, int, str, ResumeToken | None]) -> None:
        self.sent.append(item)


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
        engine="codex",
        resume_value=resume_value,
    )


@pytest.mark.anyio
async def test_final_notify_sends_loud_final_message() -> None:
    from takopi.exec_bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=2,
    )

    await handle_message(
        cfg,
        chat_id=123,
        user_msg_id=10,
        text="hi",
        resume_token=None,
    )

    assert len(bot.send_calls) == 2
    assert bot.send_calls[0]["disable_notification"] is True
    assert bot.send_calls[1]["disable_notification"] is False


@pytest.mark.anyio
async def test_new_final_message_forces_notification_when_too_long_to_edit() -> None:
    from takopi.exec_bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    runner = _return_runner(answer="x" * 10_000)
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=False,
        startup_msg="",
        max_concurrency=1,
    )

    await handle_message(
        cfg,
        chat_id=123,
        user_msg_id=10,
        text="hi",
        resume_token=None,
    )

    assert len(bot.send_calls) == 2
    assert bot.send_calls[0]["disable_notification"] is True
    assert bot.send_calls[1]["disable_notification"] is False


@pytest.mark.anyio
async def test_progress_edits_are_rate_limited() -> None:
    from takopi.exec_bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    clock = _FakeClock()
    events: list[TakopiEvent] = [
        cast(
            TakopiEvent,
            {
                "type": "action.started",
                "engine": "codex",
                "action": {
                    "id": "item_0",
                    "kind": "command",
                    "title": "echo 1",
                    "detail": {},
                },
            },
        ),
        cast(
            TakopiEvent,
            {
                "type": "action.started",
                "engine": "codex",
                "action": {
                    "id": "item_1",
                    "kind": "command",
                    "title": "echo 2",
                    "detail": {},
                },
            },
        ),
    ]
    runner = ScriptRunner(
        [
            Emit(events[0], at=0.2),
            Emit(events[1], at=0.4),
            Advance(1.0),
            Return(answer="ok"),
        ],
        engine="codex",
        advance=clock.set,
    )
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=1,
    )

    await handle_message(
        cfg,
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
    from takopi.exec_bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    clock = _FakeClock()
    hold = anyio.Event()
    events: list[TakopiEvent] = [
        cast(
            TakopiEvent,
            {
                "type": "action.started",
                "engine": "codex",
                "action": {
                    "id": "item_0",
                    "kind": "command",
                    "title": "echo 1",
                    "detail": {},
                },
            },
        ),
        cast(
            TakopiEvent,
            {
                "type": "action.started",
                "engine": "codex",
                "action": {
                    "id": "item_1",
                    "kind": "command",
                    "title": "echo 2",
                    "detail": {},
                },
            },
        ),
    ]
    runner = ScriptRunner(
        [
            Emit(events[0], at=0.2),
            Emit(events[1], at=0.4),
            Wait(hold),
            Return(answer="ok"),
        ],
        engine="codex",
        advance=clock.set,
    )
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=1,
    )

    async def run_handle_message() -> None:
        await handle_message(
            cfg,
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
    from takopi.exec_bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    clock = _FakeClock()
    events: list[TakopiEvent] = [
        cast(
            TakopiEvent,
            {
                "type": "action.started",
                "engine": "codex",
                "action": {
                    "id": "item_0",
                    "kind": "command",
                    "title": "echo ok",
                    "detail": {},
                },
            },
        ),
        cast(
            TakopiEvent,
            {
                "type": "action.completed",
                "engine": "codex",
                "action": {
                    "id": "item_0",
                    "kind": "command",
                    "title": "echo ok",
                    "detail": {"exit_code": 0},
                    "ok": True,
                },
            },
        ),
    ]
    session_id = "123e4567-e89b-12d3-a456-426614174000"
    runner = ScriptRunner(
        [
            Emit(events[0], at=0.0),
            Emit(events[1], at=2.1),
            Return(answer="done"),
        ],
        engine="codex",
        advance=clock.set,
        resume_value=session_id,
    )
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=1,
    )

    await handle_message(
        cfg,
        chat_id=123,
        user_msg_id=42,
        text="do it",
        resume_token=None,
        clock=clock,
        sleep=clock.sleep,
        progress_edit_every=1.0,
    )

    assert bot.send_calls[0]["reply_to_message_id"] == 42
    assert "working" in bot.send_calls[0]["text"]
    assert len(bot.edit_calls) >= 1
    assert session_id in bot.send_calls[-1]["text"]
    assert "codex resume" in bot.send_calls[-1]["text"].lower()
    assert len(bot.delete_calls) == 1


@pytest.mark.anyio
async def test_handle_cancel_without_reply_prompts_user() -> None:
    from takopi.exec_bridge import BridgeConfig, _handle_cancel

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=1,
    )
    msg = {"chat": {"id": 123}, "message_id": 10}
    running_tasks: dict = {}

    await _handle_cancel(cfg, msg, running_tasks)

    assert len(bot.send_calls) == 1
    assert "reply to the progress message" in bot.send_calls[0]["text"]


@pytest.mark.anyio
async def test_handle_cancel_with_no_progress_message_says_nothing_running() -> None:
    from takopi.exec_bridge import BridgeConfig, _handle_cancel

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=1,
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
    from takopi.exec_bridge import BridgeConfig, _handle_cancel

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=1,
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
    from takopi.exec_bridge import BridgeConfig, _handle_cancel

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=1,
    )
    progress_id = 42
    msg = {
        "chat": {"id": 123},
        "message_id": 10,
        "reply_to_message": {"message_id": progress_id},
    }

    from takopi.exec_bridge import RunningTask

    running_task = RunningTask()
    running_tasks = {progress_id: running_task}
    await _handle_cancel(cfg, msg, running_tasks)

    assert running_task.cancel_requested.is_set() is True
    assert len(bot.send_calls) == 0  # No error message sent


@pytest.mark.anyio
async def test_handle_cancel_only_cancels_matching_progress_message() -> None:
    from takopi.exec_bridge import BridgeConfig, _handle_cancel

    bot = _FakeBot()
    runner = _return_runner(answer="ok")
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=1,
    )
    from takopi.exec_bridge import RunningTask

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


@pytest.mark.anyio
async def test_handle_message_cancelled_renders_cancelled_state() -> None:
    from takopi.exec_bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    session_id = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    hold = anyio.Event()
    runner = ScriptRunner(
        [Wait(hold)],
        engine="codex",
        resume_value=session_id,
    )
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=1,
    )
    running_tasks: dict = {}

    async def run_handle_message() -> None:
        await handle_message(
            cfg,
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
    from takopi.exec_bridge import BridgeConfig, handle_message

    bot = _FakeBot()
    session_id = "019b66fc-64c2-7a71-81cd-081c504cfeb2"
    runner = ScriptRunner(
        [Raise(RuntimeError("boom"))],
        engine="codex",
        resume_value=session_id,
    )
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=1,
    )

    await handle_message(
        cfg,
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
    from takopi.exec_bridge import RunningTask, _send_with_resume

    bot = _FakeBot()
    send_stream = _SendStream()
    running_task = RunningTask()

    async def trigger_resume() -> None:
        await anyio.sleep(0)
        running_task.resume = ResumeToken(engine="codex", value="abc123")
        running_task.resume_ready.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(trigger_resume)
        await _send_with_resume(
            bot,
            send_stream,
            running_task,
            123,
            10,
            "hello",
        )

    assert send_stream.sent == [
        (123, 10, "hello", ResumeToken(engine="codex", value="abc123"))
    ]


@pytest.mark.anyio
async def test_send_with_resume_reports_when_missing() -> None:
    from takopi.exec_bridge import RunningTask, _send_with_resume

    bot = _FakeBot()
    send_stream = _SendStream()
    running_task = RunningTask()
    running_task.done.set()

    await _send_with_resume(
        bot,
        send_stream,
        running_task,
        123,
        10,
        "hello",
    )

    assert send_stream.sent == []
    assert bot.send_calls
    assert "resume token" in bot.send_calls[-1]["text"].lower()


@pytest.mark.anyio
async def test_run_main_loop_routes_reply_to_running_resume() -> None:
    from takopi.exec_bridge import BridgeConfig, _run_main_loop

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
        engine="codex",
        resume_value=resume_value,
    )
    cfg = BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=123,
        final_notify=True,
        startup_msg="",
        max_concurrency=1,
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
        tg.start_soon(_run_main_loop, cfg, poller)
        try:
            with anyio.fail_after(2):
                await reply_ready.wait()
            await anyio.sleep(0)
            hold.set()
            with anyio.fail_after(2):
                while len(runner.calls) < 2:
                    await anyio.sleep(0)
            assert runner.calls[1][1] == ResumeToken(engine="codex", value=resume_value)
        finally:
            hold.set()
            stop_polling.set()
            tg.cancel_scope.cancel()
