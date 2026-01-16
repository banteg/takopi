from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from takopi import commands, plugins
from takopi.telegram.commands.executor import _CaptureTransport, _run_engine
from takopi.telegram.commands.file_transfer import _handle_file_get, _handle_file_put
from takopi.telegram.commands.model import _handle_model_command
from takopi.telegram.commands.reasoning import _handle_reasoning_command
from takopi.telegram.commands.topics import _handle_topic_command
import takopi.telegram.loop as telegram_loop
import takopi.telegram.topics as telegram_topics
from takopi.directives import parse_directives
from takopi.telegram.api_models import Chat, File, ForumTopic, Message, Update, User
from takopi.settings import TelegramFilesSettings, TelegramTopicsSettings
from takopi.telegram.bridge import (
    TelegramBridgeConfig,
    TelegramPresenter,
    TelegramTransport,
    build_bot_commands,
    handle_callback_cancel,
    handle_cancel,
    is_cancel_command,
    run_main_loop,
    send_with_resume,
)
from takopi.telegram.client import BotClient
from takopi.telegram.render import MAX_BODY_CHARS
from takopi.telegram.topic_state import TopicStateStore, resolve_state_path
from takopi.telegram.chat_sessions import ChatSessionStore, resolve_sessions_path
from takopi.telegram.chat_prefs import ChatPrefsStore, resolve_prefs_path
from takopi.telegram.engine_overrides import EngineOverrides
from takopi.context import RunContext
from takopi.config import ProjectConfig, ProjectsConfig
from takopi.runner_bridge import ExecBridgeConfig, RunningTask
from takopi.markdown import MarkdownPresenter
from takopi.model import ResumeToken
from takopi.progress import ProgressTracker
from takopi.router import AutoRouter, RunnerEntry
from takopi.scheduler import ThreadScheduler
from takopi.transport_runtime import TransportRuntime
from takopi.runners.mock import Return, ScriptRunner, Sleep, Wait
from takopi.telegram.types import (
    TelegramCallbackQuery,
    TelegramDocument,
    TelegramIncomingMessage,
    TelegramVoice,
)
from takopi.transport import MessageRef, RenderedMessage, SendOptions
from tests.plugin_fixtures import FakeEntryPoint, install_entrypoints
from tests.telegram_fakes import (
    FakeBot,
    FakeTransport,
    _empty_projects,
    make_cfg,
    _make_router,
)

CODEX_ENGINE = "codex"
FAST_FORWARD_COALESCE_S = 0.0
FAST_MEDIA_GROUP_DEBOUNCE_S = 0.0
BATCH_MEDIA_GROUP_DEBOUNCE_S = 0.05
DEBOUNCE_FORWARD_COALESCE_S = 0.05


class _NoopTaskGroup:
    def start_soon(self, func, *args: Any) -> None:
        _ = func, args
        return None


def test_parse_directives_inline_engine() -> None:
    directives = parse_directives(
        "/claude do it",
        engine_ids=("codex", "claude"),
        projects=_empty_projects(),
    )
    assert directives.engine == "claude"
    assert directives.prompt == "do it"


def test_parse_directives_newline() -> None:
    directives = parse_directives(
        "/codex\nhello",
        engine_ids=("codex", "claude"),
        projects=_empty_projects(),
    )
    assert directives.engine == "codex"
    assert directives.prompt == "hello"


def test_parse_directives_ignores_unknown() -> None:
    directives = parse_directives(
        "/unknown hi",
        engine_ids=("codex", "claude"),
        projects=_empty_projects(),
    )
    assert directives.engine is None
    assert directives.prompt == "/unknown hi"


def test_parse_directives_bot_suffix() -> None:
    directives = parse_directives(
        "/claude@bunny_agent_bot hi",
        engine_ids=("claude",),
        projects=_empty_projects(),
    )
    assert directives.engine == "claude"
    assert directives.prompt == "hi"


def test_parse_directives_only_first_non_empty_line() -> None:
    directives = parse_directives(
        "hello\n/claude hi",
        engine_ids=("codex", "claude"),
        projects=_empty_projects(),
    )
    assert directives.engine is None
    assert directives.prompt == "hello\n/claude hi"


def test_build_bot_commands_includes_cancel_and_engine() -> None:
    runner = ScriptRunner(
        [Return(answer="ok")], engine=CODEX_ENGINE, resume_value="sid"
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )
    commands = build_bot_commands(runtime)

    assert {"command": "cancel", "description": "cancel run"} in commands
    assert {"command": "file", "description": "upload or fetch files"} in commands
    assert {"command": "new", "description": "start a new thread"} in commands
    assert {"command": "agent", "description": "set default agent"} in commands
    assert any(cmd["command"] == "codex" for cmd in commands)


def test_build_bot_commands_includes_projects() -> None:
    runner = ScriptRunner(
        [Return(answer="ok")], engine=CODEX_ENGINE, resume_value="sid"
    )
    router = _make_router(runner)
    projects = ProjectsConfig(
        projects={
            "good": ProjectConfig(
                alias="good",
                path=Path("."),
                worktrees_dir=Path(".worktrees"),
            ),
            "bad-name": ProjectConfig(
                alias="bad-name",
                path=Path("."),
                worktrees_dir=Path(".worktrees"),
            ),
        },
        default_project=None,
    )

    runtime = TransportRuntime(router=router, projects=projects)
    commands = build_bot_commands(runtime)

    assert any(cmd["command"] == "good" for cmd in commands)
    assert not any(cmd["command"] == "bad-name" for cmd in commands)


def test_build_bot_commands_includes_topics_when_enabled() -> None:
    runner = ScriptRunner(
        [Return(answer="ok")], engine=CODEX_ENGINE, resume_value="sid"
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )

    commands = build_bot_commands(runtime, include_topics=True)

    assert {"command": "topic", "description": "create or bind a topic"} in commands
    assert {"command": "ctx", "description": "show or update topic context"} in commands


def test_build_bot_commands_includes_command_plugins(monkeypatch) -> None:
    class _Command:
        id = "pingcmd"
        description = "ping command"

        async def handle(self, ctx):
            _ = ctx
            return None

    entrypoints = [
        FakeEntryPoint(
            "pingcmd",
            "takopi.commands.ping:BACKEND",
            plugins.COMMAND_GROUP,
            loader=_Command,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )

    commands_list = build_bot_commands(runtime)

    assert {"command": "pingcmd", "description": "ping command"} in commands_list


def test_build_bot_commands_caps_total() -> None:
    runner = ScriptRunner(
        [Return(answer="ok")], engine=CODEX_ENGINE, resume_value="sid"
    )
    router = _make_router(runner)
    projects = ProjectsConfig(
        projects={
            f"proj{i}": ProjectConfig(
                alias=f"proj{i}",
                path=Path("."),
                worktrees_dir=Path(".worktrees"),
            )
            for i in range(150)
        },
        default_project=None,
    )

    runtime = TransportRuntime(router=router, projects=projects)
    commands = build_bot_commands(runtime)

    assert len(commands) == 100
    assert any(cmd["command"] == "codex" for cmd in commands)
    assert any(cmd["command"] == "cancel" for cmd in commands)


def test_telegram_presenter_progress_shows_cancel_button() -> None:
    presenter = TelegramPresenter()
    state = ProgressTracker(engine="codex").snapshot()

    rendered = presenter.render_progress(state, elapsed_s=0.0)

    reply_markup = rendered.extra["reply_markup"]
    assert reply_markup["inline_keyboard"][0][0]["text"] == "cancel"
    assert reply_markup["inline_keyboard"][0][0]["callback_data"] == "takopi:cancel"


def test_telegram_presenter_clears_button_on_cancelled() -> None:
    presenter = TelegramPresenter()
    state = ProgressTracker(engine="codex").snapshot()

    rendered = presenter.render_progress(state, elapsed_s=0.0, label="`cancelled`")

    assert rendered.extra["reply_markup"]["inline_keyboard"] == []


def test_telegram_presenter_final_clears_button() -> None:
    presenter = TelegramPresenter()
    state = ProgressTracker(engine="codex").snapshot()

    rendered = presenter.render_final(state, elapsed_s=0.0, status="done", answer="ok")

    assert rendered.extra["reply_markup"]["inline_keyboard"] == []


def test_telegram_presenter_split_overflow_adds_followups() -> None:
    presenter = TelegramPresenter(message_overflow="split")
    state = ProgressTracker(engine="codex").snapshot()

    rendered = presenter.render_final(
        state,
        elapsed_s=0.0,
        status="done",
        answer="x" * (MAX_BODY_CHARS + 10),
    )

    followups = rendered.extra.get("followups")
    assert followups
    assert all(isinstance(item, RenderedMessage) for item in followups)
    assert rendered.extra["reply_markup"]["inline_keyboard"] == []
    assert all(
        item.extra["reply_markup"]["inline_keyboard"] == [] for item in followups
    )


@pytest.mark.anyio
async def test_telegram_transport_passes_replace_and_wait() -> None:
    bot = FakeBot()
    transport = TelegramTransport(bot)
    reply = MessageRef(channel_id=123, message_id=10)
    replace = MessageRef(channel_id=123, message_id=11)

    await transport.send(
        channel_id=123,
        message=RenderedMessage(text="hello"),
        options=SendOptions(reply_to=reply, notify=True, replace=replace),
    )
    assert bot.send_calls
    assert bot.send_calls[0]["replace_message_id"] == 11

    await transport.edit(
        ref=replace,
        message=RenderedMessage(text="edit"),
        wait=False,
    )
    assert bot.edit_calls
    assert bot.edit_calls[0]["wait"] is False


@pytest.mark.anyio
async def test_telegram_transport_passes_reply_markup() -> None:
    bot = FakeBot()
    transport = TelegramTransport(bot)
    markup = {"inline_keyboard": []}

    await transport.send(
        channel_id=123,
        message=RenderedMessage(text="hello", extra={"reply_markup": markup}),
    )
    assert bot.send_calls
    assert bot.send_calls[0]["reply_markup"] == markup

    ref = MessageRef(channel_id=123, message_id=1)
    await transport.edit(
        ref=ref,
        message=RenderedMessage(text="edit", extra={"reply_markup": markup}),
    )
    assert bot.edit_calls
    assert bot.edit_calls[0]["reply_markup"] == markup


@pytest.mark.anyio
async def test_telegram_transport_sends_followups() -> None:
    bot = FakeBot()
    transport = TelegramTransport(bot)
    reply = MessageRef(channel_id=123, message_id=10)
    followup = RenderedMessage(text="part 2")

    await transport.send(
        channel_id=123,
        message=RenderedMessage(text="part 1", extra={"followups": [followup]}),
        options=SendOptions(reply_to=reply, notify=False, thread_id=7),
    )

    assert len(bot.send_calls) == 2
    assert bot.send_calls[1]["text"] == "part 2"
    assert bot.send_calls[1]["reply_to_message_id"] == 10
    assert bot.send_calls[1]["message_thread_id"] == 7
    assert bot.send_calls[1]["replace_message_id"] is None
    assert bot.send_calls[1]["disable_notification"] is True


@pytest.mark.anyio
async def test_telegram_transport_edits_and_sends_followups() -> None:
    bot = FakeBot()
    transport = TelegramTransport(bot)
    followup = RenderedMessage(text="part 2")

    await transport.edit(
        ref=MessageRef(channel_id=123, message_id=42),
        message=RenderedMessage(
            text="part 1",
            extra={
                "followups": [followup],
                "followup_reply_to_message_id": 10,
                "followup_thread_id": 7,
                "followup_notify": False,
            },
        ),
    )

    assert len(bot.edit_calls) == 1
    assert len(bot.send_calls) == 1
    assert bot.send_calls[0]["text"] == "part 2"
    assert bot.send_calls[0]["reply_to_message_id"] == 10
    assert bot.send_calls[0]["message_thread_id"] == 7
    assert bot.send_calls[0]["disable_notification"] is True


@pytest.mark.anyio
async def test_telegram_transport_edit_wait_false_returns_ref() -> None:
    class _OutboxBot(BotClient):
        def __init__(self) -> None:
            self.edit_calls: list[dict[str, Any]] = []

        async def get_updates(
            self,
            offset: int | None,
            timeout_s: int = 50,
            allowed_updates: list[str] | None = None,
        ) -> list[Update] | None:
            return None

        async def get_file(self, file_id: str) -> File | None:
            _ = file_id
            return None

        async def download_file(self, file_path: str) -> bytes | None:
            _ = file_path
            return None

        async def send_message(
            self,
            chat_id: int,
            text: str,
            reply_to_message_id: int | None = None,
            disable_notification: bool | None = False,
            message_thread_id: int | None = None,
            entities: list[dict[str, Any]] | None = None,
            parse_mode: str | None = None,
            reply_markup: dict | None = None,
            *,
            replace_message_id: int | None = None,
        ) -> Message | None:
            _ = reply_markup
            return None

        async def send_document(
            self,
            chat_id: int,
            filename: str,
            content: bytes,
            reply_to_message_id: int | None = None,
            message_thread_id: int | None = None,
            disable_notification: bool | None = False,
            caption: str | None = None,
        ) -> Message | None:
            _ = (
                chat_id,
                filename,
                content,
                reply_to_message_id,
                message_thread_id,
                disable_notification,
                caption,
            )
            return None

        async def edit_message_text(
            self,
            chat_id: int,
            message_id: int,
            text: str,
            entities: list[dict[str, Any]] | None = None,
            parse_mode: str | None = None,
            reply_markup: dict | None = None,
            *,
            wait: bool = True,
        ) -> Message | None:
            self.edit_calls.append(
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "entities": entities,
                    "parse_mode": parse_mode,
                    "reply_markup": reply_markup,
                    "wait": wait,
                }
            )
            if not wait:
                return None
            return Message(message_id=message_id, chat=Chat(id=chat_id, type="private"))

        async def delete_message(
            self,
            chat_id: int,
            message_id: int,
        ) -> bool:
            return False

        async def set_my_commands(
            self,
            commands: list[dict[str, Any]],
            *,
            scope: dict[str, Any] | None = None,
            language_code: str | None = None,
        ) -> bool:
            return False

        async def get_me(self) -> User | None:
            return None

        async def close(self) -> None:
            return None

        async def answer_callback_query(
            self,
            callback_query_id: str,
            text: str | None = None,
            show_alert: bool | None = None,
        ) -> bool:
            _ = callback_query_id, text, show_alert
            return True

    bot = _OutboxBot()
    transport = TelegramTransport(bot)
    ref = MessageRef(channel_id=123, message_id=1)

    result = await transport.edit(
        ref=ref,
        message=RenderedMessage(text="edit"),
        wait=False,
    )

    assert result == ref
    assert bot.edit_calls
    assert bot.edit_calls[0]["wait"] is False


@pytest.mark.anyio
async def test_handle_cancel_without_reply_prompts_user() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="/cancel",
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=123,
    )
    running_tasks: dict = {}

    await handle_cancel(cfg, msg, running_tasks)

    assert len(transport.send_calls) == 1
    assert "reply to the progress message" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_handle_cancel_with_no_progress_message_says_nothing_running() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="/cancel",
        reply_to_message_id=None,
        reply_to_text="no message id",
        sender_id=123,
    )
    running_tasks: dict = {}

    await handle_cancel(cfg, msg, running_tasks)

    assert len(transport.send_calls) == 1
    assert "nothing is currently running" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_handle_cancel_with_finished_task_says_nothing_running() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    progress_id = 99
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="/cancel",
        reply_to_message_id=progress_id,
        reply_to_text=None,
        sender_id=123,
    )
    running_tasks: dict = {}

    await handle_cancel(cfg, msg, running_tasks)

    assert len(transport.send_calls) == 1
    assert "nothing is currently running" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_handle_cancel_cancels_running_task() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    progress_id = 42
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="/cancel",
        reply_to_message_id=progress_id,
        reply_to_text=None,
        sender_id=123,
    )

    running_task = RunningTask()
    running_tasks = {MessageRef(channel_id=123, message_id=progress_id): running_task}
    await handle_cancel(cfg, msg, running_tasks)

    assert running_task.cancel_requested.is_set() is True
    assert len(transport.send_calls) == 0  # No error message sent


@pytest.mark.anyio
async def test_handle_cancel_only_cancels_matching_progress_message() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    task_first = RunningTask()
    task_second = RunningTask()
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="/cancel",
        reply_to_message_id=1,
        reply_to_text=None,
        sender_id=123,
    )
    running_tasks = {
        MessageRef(channel_id=123, message_id=1): task_first,
        MessageRef(channel_id=123, message_id=2): task_second,
    }

    await handle_cancel(cfg, msg, running_tasks)

    assert task_first.cancel_requested.is_set() is True
    assert task_second.cancel_requested.is_set() is False
    assert len(transport.send_calls) == 0


@pytest.mark.anyio
async def test_handle_cancel_cancels_queued_job() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)

    async def _noop_run_job(_) -> None:
        return None

    scheduler = ThreadScheduler(task_group=_NoopTaskGroup(), run_job=_noop_run_job)
    progress_id = 55
    progress_ref = MessageRef(channel_id=123, message_id=progress_id)
    resume = ResumeToken(engine=CODEX_ENGINE, value="sid")
    await scheduler.enqueue_resume(
        chat_id=123,
        user_msg_id=10,
        text="queued",
        resume_token=resume,
        progress_ref=progress_ref,
    )
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="/cancel",
        reply_to_message_id=progress_id,
        reply_to_text=None,
        sender_id=123,
    )

    await handle_cancel(cfg, msg, {}, scheduler)

    assert transport.edit_calls
    assert "cancelled" in transport.edit_calls[0]["message"].text.lower()
    assert await scheduler.cancel_queued(123, progress_ref.message_id) is None


@pytest.mark.anyio
async def test_handle_file_put_writes_file(tmp_path: Path) -> None:
    payload = b"hello"

    class _FileBot(FakeBot):
        async def get_file(self, file_id: str) -> File | None:
            _ = file_id
            return File(file_path="files/hello.txt")

        async def download_file(self, file_path: str) -> bytes | None:
            _ = file_path
            return payload

    transport = FakeTransport()
    bot = _FileBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=tmp_path,
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project=None,
    )
    runtime = TransportRuntime(router=_make_router(runner), projects=projects)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        files=TelegramFilesSettings(enabled=True),
    )
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="",
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=321,
        chat_type="private",
        document=TelegramDocument(
            file_id="doc-id",
            file_name="hello.txt",
            mime_type="text/plain",
            file_size=len(payload),
            raw={"file_id": "doc-id"},
        ),
    )

    await _handle_file_put(cfg, msg, "/proj uploads/hello.txt", None, None)

    target = tmp_path / "uploads" / "hello.txt"
    assert target.read_bytes() == payload
    assert transport.send_calls
    text = transport.send_calls[-1]["message"].text
    assert "saved uploads/hello.txt" in text
    assert "(5 b)" in text


@pytest.mark.anyio
async def test_handle_file_get_sends_document_for_allowed_user(
    tmp_path: Path,
) -> None:
    payload = b"fetch"
    target = tmp_path / "hello.txt"
    target.write_bytes(payload)

    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=tmp_path,
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project=None,
    )
    runtime = TransportRuntime(router=_make_router(runner), projects=projects)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        files=TelegramFilesSettings(
            enabled=True,
            allowed_user_ids=[42],
        ),
    )
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=-100,
        message_id=10,
        text="",
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=42,
        chat_type="supergroup",
    )

    await _handle_file_get(cfg, msg, "/proj hello.txt", None, None)

    assert bot.document_calls
    assert bot.document_calls[0]["filename"] == "hello.txt"
    assert bot.document_calls[0]["content"] == payload


@pytest.mark.anyio
async def test_handle_callback_cancel_cancels_running_task() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    progress_id = 42
    running_task = RunningTask()
    running_tasks = {MessageRef(channel_id=123, message_id=progress_id): running_task}
    query = TelegramCallbackQuery(
        transport="telegram",
        chat_id=123,
        message_id=progress_id,
        callback_query_id="cbq-1",
        data="takopi:cancel",
        sender_id=123,
    )

    await handle_callback_cancel(cfg, query, running_tasks)

    assert running_task.cancel_requested.is_set() is True
    assert len(transport.send_calls) == 0
    bot = cast(FakeBot, cfg.bot)
    assert bot.callback_calls
    assert bot.callback_calls[-1]["text"] == "cancelling..."


@pytest.mark.anyio
async def test_handle_callback_cancel_cancels_queued_job() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)

    async def _noop_run_job(_) -> None:
        return None

    scheduler = ThreadScheduler(task_group=_NoopTaskGroup(), run_job=_noop_run_job)
    progress_id = 77
    progress_ref = MessageRef(channel_id=123, message_id=progress_id)
    resume = ResumeToken(engine=CODEX_ENGINE, value="sid")
    await scheduler.enqueue_resume(
        chat_id=123,
        user_msg_id=10,
        text="queued",
        resume_token=resume,
        progress_ref=progress_ref,
    )
    query = TelegramCallbackQuery(
        transport="telegram",
        chat_id=123,
        message_id=progress_id,
        callback_query_id="cbq-queued",
        data="takopi:cancel",
        sender_id=123,
    )

    await handle_callback_cancel(cfg, query, {}, scheduler)

    assert transport.edit_calls
    assert "cancelled" in transport.edit_calls[0]["message"].text.lower()
    bot = cast(FakeBot, cfg.bot)
    assert bot.callback_calls
    assert bot.callback_calls[-1]["text"] == "dropped from queue."


@pytest.mark.anyio
async def test_handle_callback_cancel_without_task_acknowledges() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    query = TelegramCallbackQuery(
        transport="telegram",
        chat_id=123,
        message_id=99,
        callback_query_id="cbq-2",
        data="takopi:cancel",
        sender_id=123,
    )

    await handle_callback_cancel(cfg, query, {})

    assert len(transport.send_calls) == 0
    bot = cast(FakeBot, cfg.bot)
    assert bot.callback_calls
    assert "nothing is currently running" in bot.callback_calls[-1]["text"].lower()


def test_cancel_command_accepts_extra_text() -> None:
    assert is_cancel_command("/cancel now") is True
    assert is_cancel_command("/cancel@takopi please") is True
    assert is_cancel_command("/cancelled") is False


def test_resolve_message_accepts_backticked_ctx_line() -> None:
    runtime = TransportRuntime(
        router=_make_router(ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)),
        projects=ProjectsConfig(
            projects={
                "takopi": ProjectConfig(
                    alias="takopi",
                    path=Path("."),
                    worktrees_dir=Path(".worktrees"),
                )
            },
            default_project=None,
        ),
    )
    resolved = runtime.resolve_message(
        text="do it",
        reply_text="`ctx: takopi @feat/api`",
    )

    assert resolved.prompt == "do it"
    assert resolved.resume_token is None
    assert resolved.engine_override is None
    assert resolved.context == RunContext(project="takopi", branch="feat/api")


def test_is_forwarded_detects_forward_fields() -> None:
    assert telegram_loop._is_forwarded({"forward_origin": {"type": "user"}})
    assert telegram_loop._is_forwarded({"forward_from": {"id": 1}})
    assert telegram_loop._is_forwarded({"forward_from_chat": {"id": 1}})
    assert telegram_loop._is_forwarded({"forward_from_message_id": 2})
    assert telegram_loop._is_forwarded({"forward_sender_name": "anon"})
    assert telegram_loop._is_forwarded({"forward_signature": "sig"})
    assert telegram_loop._is_forwarded({"forward_date": 123})
    assert telegram_loop._is_forwarded({"is_automatic_forward": True})
    assert not telegram_loop._is_forwarded({"text": "hello"})
    assert not telegram_loop._is_forwarded(None)


def test_topic_title_matches_command_syntax() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)

    title = telegram_topics._topic_title(
        runtime=cfg.runtime,
        context=RunContext(project="takopi", branch="master"),
    )

    assert title == "takopi @master"

    title = telegram_topics._topic_title(
        runtime=cfg.runtime,
        context=RunContext(project="takopi", branch=None),
    )

    assert title == "takopi"

    title = telegram_topics._topic_title(
        runtime=cfg.runtime,
        context=RunContext(project=None, branch="main"),
    )

    assert title == "@main"


def test_topic_title_projects_scope_includes_project() -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        topics=TelegramTopicsSettings(
            enabled=True,
            scope="projects",
        ),
    )

    title = telegram_topics._topic_title(
        runtime=cfg.runtime,
        context=RunContext(project="takopi", branch="master"),
    )

    assert title == "takopi @master"


@pytest.mark.anyio
async def test_maybe_rename_topic_updates_title(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    store = TopicStateStore(tmp_path / "telegram_topics_state.json")

    await store.set_context(
        123,
        77,
        RunContext(project="takopi", branch="old"),
        topic_title="takopi @old",
    )

    await telegram_topics._maybe_rename_topic(
        cfg,
        store,
        chat_id=123,
        thread_id=77,
        context=RunContext(project="takopi", branch="new"),
    )

    bot = cast(FakeBot, cfg.bot)
    assert bot.edit_topic_calls
    assert bot.edit_topic_calls[-1]["name"] == "takopi @new"
    snapshot = await store.get_thread(123, 77)
    assert snapshot is not None
    assert snapshot.topic_title == "takopi @new"


@pytest.mark.anyio
async def test_maybe_rename_topic_skips_when_title_matches(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    store = TopicStateStore(tmp_path / "telegram_topics_state.json")

    await store.set_context(
        123,
        77,
        RunContext(project="takopi", branch="main"),
        topic_title="takopi @main",
    )
    snapshot = await store.get_thread(123, 77)

    await telegram_topics._maybe_rename_topic(
        cfg,
        store,
        chat_id=123,
        thread_id=77,
        context=RunContext(project="takopi", branch="main"),
        snapshot=snapshot,
    )

    bot = cast(FakeBot, cfg.bot)
    assert bot.edit_topic_calls == []


@pytest.mark.anyio
async def test_topic_command_recreates_stale_topic(tmp_path: Path) -> None:
    class _StaleTopicBot(FakeBot):
        def __init__(self) -> None:
            super().__init__()
            self.create_topic_calls: list[dict[str, Any]] = []

        async def create_forum_topic(
            self, chat_id: int, name: str
        ) -> ForumTopic | None:
            self.create_topic_calls.append({"chat_id": chat_id, "name": name})
            return ForumTopic(message_thread_id=55)

        async def edit_forum_topic(
            self, chat_id: int, message_thread_id: int, name: str
        ) -> bool:
            self.edit_topic_calls.append(
                {
                    "chat_id": chat_id,
                    "message_thread_id": message_thread_id,
                    "name": name,
                }
            )
            return False

    transport = FakeTransport()
    bot = _StaleTopicBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    projects = ProjectsConfig(
        projects={
            "takopi": ProjectConfig(
                alias="takopi",
                path=tmp_path,
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project=None,
    )
    runtime = TransportRuntime(router=_make_router(runner), projects=projects)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        topics=TelegramTopicsSettings(enabled=True, scope="main"),
    )
    store = TopicStateStore(tmp_path / "telegram_topics_state.json")
    await store.set_context(
        123,
        77,
        RunContext(project="takopi", branch="master"),
        topic_title="takopi @master",
    )
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="/topic takopi @master",
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=123,
    )

    await _handle_topic_command(
        cfg,
        msg,
        "takopi @master",
        store,
        resolved_scope="main",
        scope_chat_ids=frozenset({123}),
    )

    assert bot.edit_topic_calls
    assert bot.create_topic_calls
    assert await store.get_thread(123, 77) is None
    snapshot = await store.get_thread(123, 55)
    assert snapshot is not None
    assert snapshot.context == RunContext(project="takopi", branch="master")


@pytest.mark.anyio
async def test_model_command_show_reports_overrides(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    cfg = replace(cfg, topics=TelegramTopicsSettings(enabled=True, scope="main"))
    chat_prefs = ChatPrefsStore(tmp_path / "telegram_chat_prefs_state.json")
    topic_store = TopicStateStore(tmp_path / "telegram_topics_state.json")
    await chat_prefs.set_engine_override(
        123,
        CODEX_ENGINE,
        EngineOverrides(model="gpt-4.1-mini", reasoning=None),
    )
    await topic_store.set_engine_override(
        123,
        77,
        CODEX_ENGINE,
        EngineOverrides(model="gpt-4.1", reasoning=None),
    )
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="/model",
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=123,
        thread_id=77,
    )

    await _handle_model_command(
        cfg,
        msg,
        "",
        ambient_context=None,
        topic_store=topic_store,
        chat_prefs=chat_prefs,
        resolved_scope="main",
        scope_chat_ids=frozenset({123}),
    )

    text = transport.send_calls[-1]["message"].text
    assert "engine: codex (global default)" in text
    assert "model: gpt-4.1 (topic override)" in text
    assert "defaults: topic: gpt-4.1, chat: gpt-4.1-mini" in text
    assert "available engines: codex" in text


@pytest.mark.anyio
async def test_model_command_set_and_clear_chat_override(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    chat_prefs = ChatPrefsStore(tmp_path / "telegram_chat_prefs_state.json")
    await chat_prefs.set_engine_override(
        123,
        CODEX_ENGINE,
        EngineOverrides(model=None, reasoning="low"),
    )
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="/model set gpt-4.1-mini",
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=456,
        chat_type="supergroup",
    )

    await _handle_model_command(
        cfg,
        msg,
        "set gpt-4.1-mini",
        ambient_context=None,
        topic_store=None,
        chat_prefs=chat_prefs,
    )

    override = await chat_prefs.get_engine_override(123, CODEX_ENGINE)
    assert override is not None
    assert override.model == "gpt-4.1-mini"
    assert override.reasoning == "low"
    assert (
        "chat model override set to gpt-4.1-mini for codex."
        in transport.send_calls[-1]["message"].text
    )

    msg_clear = replace(
        msg,
        message_id=11,
        text="/model clear codex",
    )
    await _handle_model_command(
        cfg,
        msg_clear,
        "clear codex",
        ambient_context=None,
        topic_store=None,
        chat_prefs=chat_prefs,
    )

    override = await chat_prefs.get_engine_override(123, CODEX_ENGINE)
    assert override is not None
    assert override.model is None
    assert override.reasoning == "low"
    assert "chat model override cleared." in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_reasoning_command_set_and_clear_topic_override(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    cfg = replace(cfg, topics=TelegramTopicsSettings(enabled=True, scope="main"))
    topic_store = TopicStateStore(tmp_path / "telegram_topics_state.json")
    await topic_store.set_engine_override(
        123,
        77,
        CODEX_ENGINE,
        EngineOverrides(model="gpt-4.1-mini", reasoning=None),
    )
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="/reasoning set High",
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=456,
        chat_type="supergroup",
        thread_id=77,
    )

    await _handle_reasoning_command(
        cfg,
        msg,
        "set High",
        ambient_context=None,
        topic_store=topic_store,
        chat_prefs=None,
        resolved_scope="main",
        scope_chat_ids=frozenset({123}),
    )

    override = await topic_store.get_engine_override(123, 77, CODEX_ENGINE)
    assert override is not None
    assert override.model == "gpt-4.1-mini"
    assert override.reasoning == "high"
    assert (
        "topic reasoning override set to high for codex."
        in transport.send_calls[-1]["message"].text
    )

    msg_clear = replace(
        msg,
        message_id=11,
        text="/reasoning clear",
    )
    await _handle_reasoning_command(
        cfg,
        msg_clear,
        "clear",
        ambient_context=None,
        topic_store=topic_store,
        chat_prefs=None,
        resolved_scope="main",
        scope_chat_ids=frozenset({123}),
    )

    override = await topic_store.get_engine_override(123, 77, CODEX_ENGINE)
    assert override is not None
    assert override.model == "gpt-4.1-mini"
    assert override.reasoning is None
    assert (
        "topic reasoning override cleared (using chat default)."
        in transport.send_calls[-1]["message"].text
    )


@pytest.mark.anyio
async def test_reasoning_command_show_reports_overrides(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    cfg = replace(cfg, topics=TelegramTopicsSettings(enabled=True, scope="main"))
    chat_prefs = ChatPrefsStore(tmp_path / "telegram_chat_prefs_state.json")
    topic_store = TopicStateStore(tmp_path / "telegram_topics_state.json")
    await chat_prefs.set_engine_override(
        123,
        CODEX_ENGINE,
        EngineOverrides(model=None, reasoning="low"),
    )
    await topic_store.set_engine_override(
        123,
        88,
        CODEX_ENGINE,
        EngineOverrides(model=None, reasoning="high"),
    )
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=10,
        text="/reasoning",
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=123,
        thread_id=88,
    )

    await _handle_reasoning_command(
        cfg,
        msg,
        "",
        ambient_context=None,
        topic_store=topic_store,
        chat_prefs=chat_prefs,
        resolved_scope="main",
        scope_chat_ids=frozenset({123}),
    )

    text = transport.send_calls[-1]["message"].text
    assert "engine: codex (global default)" in text
    assert "reasoning: high (topic override)" in text
    assert "defaults: topic: high, chat: low" in text
    assert "available levels: minimal, low, medium, high, xhigh" in text


@pytest.mark.anyio
async def test_send_with_resume_waits_for_token() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    sent: list[
        tuple[
            int,
            int,
            str,
            ResumeToken,
            RunContext | None,
            int | None,
            tuple[int, int | None] | None,
            MessageRef | None,
        ]
    ] = []

    async def enqueue(
        chat_id: int,
        user_msg_id: int,
        text: str,
        resume: ResumeToken,
        context: RunContext | None,
        thread_id: int | None,
        session_key: tuple[int, int | None] | None,
        progress_ref: MessageRef | None,
    ) -> None:
        sent.append(
            (
                chat_id,
                user_msg_id,
                text,
                resume,
                context,
                thread_id,
                session_key,
                progress_ref,
            )
        )

    running_task = RunningTask()

    async def trigger_resume() -> None:
        await anyio.sleep(0)
        running_task.resume = ResumeToken(engine=CODEX_ENGINE, value="abc123")
        running_task.resume_ready.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(trigger_resume)
        await send_with_resume(
            cfg,
            enqueue,
            running_task,
            123,
            10,
            None,
            None,
            "hello",
        )

    assert len(sent) == 1
    assert sent[0][:7] == (
        123,
        10,
        "hello",
        ResumeToken(engine=CODEX_ENGINE, value="abc123"),
        None,
        None,
        None,
    )
    assert sent[0][7] == transport.send_calls[0]["ref"]
    assert transport.send_calls
    assert "queued" in transport.send_calls[0]["message"].text.lower()


@pytest.mark.anyio
async def test_send_with_resume_reports_when_missing() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    sent: list[
        tuple[
            int,
            int,
            str,
            ResumeToken,
            RunContext | None,
            int | None,
            tuple[int, int | None] | None,
            MessageRef | None,
        ]
    ] = []

    async def enqueue(
        chat_id: int,
        user_msg_id: int,
        text: str,
        resume: ResumeToken,
        context: RunContext | None,
        thread_id: int | None,
        session_key: tuple[int, int | None] | None,
        progress_ref: MessageRef | None,
    ) -> None:
        sent.append(
            (
                chat_id,
                user_msg_id,
                text,
                resume,
                context,
                thread_id,
                session_key,
                progress_ref,
            )
        )

    running_task = RunningTask()
    running_task.done.set()

    await send_with_resume(
        cfg,
        enqueue,
        running_task,
        123,
        10,
        None,
        None,
        "hello",
    )

    assert sent == []
    assert transport.send_calls
    assert "resume token" in transport.send_calls[-1]["message"].text.lower()


@pytest.mark.anyio
async def test_run_engine_hides_resume_line_in_topics() -> None:
    transport = _CaptureTransport()
    runner = ScriptRunner(
        [Return(answer="ok")],
        engine=CODEX_ENGINE,
        resume_value="resume-123",
    )
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )

    await _run_engine(
        exec_cfg=exec_cfg,
        runtime=runtime,
        running_tasks={},
        chat_id=123,
        user_msg_id=1,
        text="hello",
        resume_token=None,
        context=None,
        reply_ref=None,
        on_thread_known=None,
        engine_override=None,
        thread_id=77,
        show_resume_line=False,
    )

    assert transport.last_message is not None
    assert "resume-123" not in transport.last_message.text


@pytest.mark.anyio
async def test_run_main_loop_routes_reply_to_running_resume() -> None:
    progress_ready = anyio.Event()
    stop_polling = anyio.Event()
    reply_ready = anyio.Event()
    hold = anyio.Event()

    transport = FakeTransport(progress_ready=progress_ready)
    bot = FakeBot()
    resume_value = "abc123"
    runner = ScriptRunner(
        [Wait(hold), Sleep(0.05), Return(answer="ok")],
        engine=CODEX_ENGINE,
        resume_value=resume_value,
    )
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="first",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
        )
        await progress_ready.wait()
        assert transport.progress_ref is not None
        assert isinstance(transport.progress_ref.message_id, int)
        reply_id = transport.progress_ref.message_id
        reply_ready.set()
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=2,
            text="followup",
            reply_to_message_id=reply_id,
            reply_to_text=None,
            sender_id=123,
        )
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


@pytest.mark.anyio
async def test_run_main_loop_persists_topic_sessions_in_project_scope(
    tmp_path: Path,
) -> None:
    project_chat_id = -100
    resume_value = "resume-123"

    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner(
        [Return(answer="ok")],
        engine=CODEX_ENGINE,
        resume_value=resume_value,
    )
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    projects = ProjectsConfig(
        projects={
            "takopi": ProjectConfig(
                alias="takopi",
                path=Path("."),
                worktrees_dir=Path(".worktrees"),
                chat_id=project_chat_id,
            )
        },
        default_project=None,
        chat_map={project_chat_id: "takopi"},
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=projects,
        config_path=tmp_path / "takopi.toml",
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        topics=TelegramTopicsSettings(
            enabled=True,
            scope="projects",
        ),
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=project_chat_id,
            message_id=1,
            text="hello",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            thread_id=77,
        )

    with anyio.fail_after(2):
        await run_main_loop(cfg, poller)

    state_path = resolve_state_path(runtime.config_path or tmp_path / "takopi.toml")
    store = TopicStateStore(state_path)
    stored = await store.get_session_resume(project_chat_id, 77, CODEX_ENGINE)
    assert stored == ResumeToken(engine=CODEX_ENGINE, value=resume_value)


@pytest.mark.anyio
async def test_run_main_loop_auto_resumes_topic_default_engine(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "takopi.toml"
    topic_path = resolve_state_path(state_path)
    store = TopicStateStore(topic_path)
    await store.set_session_resume(
        123, 77, ResumeToken(engine=CODEX_ENGINE, value="resume-codex")
    )
    await store.set_session_resume(
        123, 77, ResumeToken(engine="claude", value="resume-claude")
    )
    await store.set_default_engine(123, 77, "claude")

    transport = FakeTransport()
    bot = FakeBot()
    codex_runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    claude_runner = ScriptRunner([Return(answer="ok")], engine="claude")
    router = AutoRouter(
        entries=[
            RunnerEntry(engine=codex_runner.engine, runner=codex_runner),
            RunnerEntry(engine=claude_runner.engine, runner=claude_runner),
        ],
        default_engine=codex_runner.engine,
    )
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=tmp_path,
                worktrees_dir=Path(".worktrees"),
                chat_id=123,
            )
        },
        default_project=None,
        chat_map={123: "proj"},
    )
    runtime = TransportRuntime(
        router=router,
        projects=projects,
        config_path=state_path,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=ExecBridgeConfig(
            transport=transport,
            presenter=MarkdownPresenter(),
            final_notify=True,
        ),
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        topics=TelegramTopicsSettings(
            enabled=True,
            scope="main",
        ),
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="hello",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            thread_id=77,
        )

    await run_main_loop(cfg, poller)

    assert codex_runner.calls == []
    assert len(claude_runner.calls) == 1
    assert claude_runner.calls[0][1] == ResumeToken(
        engine="claude", value="resume-claude"
    )


@pytest.mark.anyio
async def test_run_main_loop_auto_resumes_chat_sessions(tmp_path: Path) -> None:
    resume_value = "resume-123"
    state_path = tmp_path / "takopi.toml"

    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner(
        [Return(answer="ok")],
        engine=CODEX_ENGINE,
        resume_value=resume_value,
    )
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=tmp_path,
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project="proj",
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=projects,
        config_path=state_path,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        session_mode="chat",
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="hello",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            chat_type="private",
        )

    await run_main_loop(cfg, poller)

    store = ChatSessionStore(resolve_sessions_path(state_path))
    stored = await store.get_session_resume(123, None, CODEX_ENGINE)
    assert stored == ResumeToken(engine=CODEX_ENGINE, value=resume_value)

    runner2 = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    runtime2 = TransportRuntime(
        router=_make_router(runner2),
        projects=_empty_projects(),
        config_path=state_path,
    )
    cfg2 = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime2,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        session_mode="chat",
    )

    async def poller2(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=2,
            text="followup",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            chat_type="private",
        )

    await run_main_loop(cfg2, poller2)

    assert runner2.calls[0][1] == ResumeToken(engine=CODEX_ENGINE, value=resume_value)


@pytest.mark.anyio
async def test_run_main_loop_prompt_upload_uses_caption_directives(
    tmp_path: Path,
) -> None:
    payload = b"hello"
    proj_dir = tmp_path / "proj"
    other_dir = tmp_path / "other"
    proj_dir.mkdir()
    other_dir.mkdir()

    class _UploadBot(FakeBot):
        async def get_file(self, file_id: str) -> File | None:
            _ = file_id
            return File(file_path="files/hello.txt")

        async def download_file(self, file_path: str) -> bytes | None:
            _ = file_path
            return payload

    transport = FakeTransport()
    bot = _UploadBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=proj_dir,
                worktrees_dir=Path(".worktrees"),
            ),
            "other": ProjectConfig(
                alias="other",
                path=other_dir,
                worktrees_dir=Path(".worktrees"),
            ),
        },
        default_project="proj",
    )
    runtime = TransportRuntime(router=_make_router(runner), projects=projects)
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        files=TelegramFilesSettings(
            enabled=True,
            auto_put=True,
            auto_put_mode="prompt",
        ),
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="/other do thing",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            chat_type="private",
            document=TelegramDocument(
                file_id="doc-1",
                file_name="hello.txt",
                mime_type="text/plain",
                file_size=len(payload),
                raw={"file_id": "doc-1"},
            ),
        )

    await run_main_loop(cfg, poller)

    saved_path = other_dir / "incoming" / "hello.txt"
    assert saved_path.read_bytes() == payload
    assert runner.calls
    prompt_text, _ = runner.calls[0]
    assert prompt_text.startswith("do thing")
    assert "/other" not in prompt_text
    assert "[uploaded file: incoming/hello.txt]" in prompt_text


@pytest.mark.anyio
async def test_run_main_loop_voice_transcript_preserves_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_runner = ScriptRunner([Return(answer="codex")], engine=CODEX_ENGINE)
    claude_runner = ScriptRunner([Return(answer="claude")], engine="claude")
    router = AutoRouter(
        entries=[
            RunnerEntry(engine=claude_runner.engine, runner=claude_runner),
            RunnerEntry(engine=codex_runner.engine, runner=codex_runner),
        ],
        default_engine=claude_runner.engine,
    )
    runtime = TransportRuntime(router=router, projects=_empty_projects())
    transport = FakeTransport()
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    cfg = TelegramBridgeConfig(
        bot=FakeBot(),
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        voice_transcription=True,
    )

    async def _fake_transcribe(
        *,
        bot: BotClient,
        msg: TelegramIncomingMessage,
        enabled: bool,
        model: str,
        max_bytes: int | None = None,
        reply,
    ) -> str:
        _ = bot, msg, enabled, model, max_bytes, reply
        return "/codex do thing"

    monkeypatch.setattr(telegram_loop, "transcribe_voice", _fake_transcribe)
    monkeypatch.setattr(telegram_loop, "list_command_ids", lambda **_: [])

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            voice=TelegramVoice(
                file_id="voice-1",
                mime_type=None,
                file_size=None,
                duration=None,
                raw={"file_id": "voice-1"},
            ),
        )

    await run_main_loop(cfg, poller)

    assert not claude_runner.calls
    assert len(codex_runner.calls) == 1
    assert codex_runner.calls[0][0].startswith("(voice transcribed) do thing")


@pytest.mark.anyio
async def test_run_main_loop_debounces_forwarded_messages_preserves_directives() -> (
    None
):
    codex_runner = ScriptRunner([Return(answer="codex")], engine=CODEX_ENGINE)
    claude_runner = ScriptRunner([Return(answer="claude")], engine="claude")
    router = AutoRouter(
        entries=[
            RunnerEntry(engine=claude_runner.engine, runner=claude_runner),
            RunnerEntry(engine=codex_runner.engine, runner=codex_runner),
        ],
        default_engine=claude_runner.engine,
    )
    runtime = TransportRuntime(router=router, projects=_empty_projects())
    transport = FakeTransport()
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    cfg = TelegramBridgeConfig(
        bot=FakeBot(),
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=DEBOUNCE_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="/codex summarize these",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
        )
        await anyio.sleep(_cfg.forward_coalesce_s / 2)
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=2,
            text="a",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            raw={"forward_origin": {"type": "user"}},
        )
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=3,
            text="b",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            raw={"forward_origin": {"type": "user"}},
        )
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=4,
            text="c",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            raw={"forward_origin": {"type": "user"}},
        )

    await run_main_loop(cfg, poller)

    assert not claude_runner.calls
    assert len(codex_runner.calls) == 1
    prompt_text, _ = codex_runner.calls[0]
    assert prompt_text == "summarize these\n\na\n\nb\n\nc"


@pytest.mark.anyio
async def test_run_main_loop_ignores_forwarded_without_prompt() -> None:
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    runtime = TransportRuntime(router=_make_router(runner), projects=_empty_projects())
    transport = FakeTransport()
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    cfg = TelegramBridgeConfig(
        bot=FakeBot(),
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="a",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            raw={"forward_origin": {"type": "user"}},
        )
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=2,
            text="b",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            raw={"forward_origin": {"type": "user"}},
        )

    await run_main_loop(cfg, poller)

    assert runner.calls == []


@pytest.mark.anyio
async def test_run_main_loop_forwarded_document_still_uploads(
    tmp_path: Path,
) -> None:
    payload = b"hello"

    class _UploadBot(FakeBot):
        async def get_file(self, file_id: str) -> File | None:
            _ = file_id
            return File(file_path="files/hello.txt")

        async def download_file(self, file_path: str) -> bytes | None:
            _ = file_path
            return payload

    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=tmp_path,
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project="proj",
    )
    runtime = TransportRuntime(router=_make_router(runner), projects=projects)
    transport = FakeTransport()
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    cfg = TelegramBridgeConfig(
        bot=_UploadBot(),
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        files=TelegramFilesSettings(
            enabled=True,
            auto_put=True,
            auto_put_mode="prompt",
        ),
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="do thing",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            chat_type="private",
            document=TelegramDocument(
                file_id="doc-1",
                file_name="hello.txt",
                mime_type="text/plain",
                file_size=len(payload),
                raw={"file_id": "doc-1"},
            ),
            raw={"forward_origin": {"type": "user"}},
        )

    await run_main_loop(cfg, poller)

    saved_path = tmp_path / "incoming" / "hello.txt"
    assert saved_path.read_bytes() == payload
    assert runner.calls
    prompt_text, _ = runner.calls[0]
    assert prompt_text.startswith("do thing")
    assert "[uploaded file: incoming/hello.txt]" in prompt_text


@pytest.mark.anyio
async def test_run_main_loop_prompt_upload_auto_resumes_chat_sessions(
    tmp_path: Path,
) -> None:
    payload = b"hello"
    resume_value = "resume-123"
    state_path = tmp_path / "takopi.toml"
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    class _UploadBot(FakeBot):
        async def get_file(self, file_id: str) -> File | None:
            _ = file_id
            return File(file_path="files/hello.txt")

        async def download_file(self, file_path: str) -> bytes | None:
            _ = file_path
            return payload

    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=project_dir,
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project="proj",
    )
    bot = _UploadBot()

    transport = FakeTransport()
    runner = ScriptRunner(
        [Return(answer="ok")],
        engine=CODEX_ENGINE,
        resume_value=resume_value,
    )
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=projects,
        config_path=state_path,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        session_mode="chat",
        files=TelegramFilesSettings(
            enabled=True,
            auto_put=True,
            auto_put_mode="prompt",
        ),
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="hello",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            chat_type="private",
            document=TelegramDocument(
                file_id="doc-1",
                file_name="hello.txt",
                mime_type="text/plain",
                file_size=len(payload),
                raw={"file_id": "doc-1"},
            ),
        )

    await run_main_loop(cfg, poller)

    store = ChatSessionStore(resolve_sessions_path(state_path))
    stored = await store.get_session_resume(123, None, CODEX_ENGINE)
    assert stored == ResumeToken(engine=CODEX_ENGINE, value=resume_value)

    transport2 = FakeTransport()
    runner2 = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg2 = ExecBridgeConfig(
        transport=transport2,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime2 = TransportRuntime(
        router=_make_router(runner2),
        projects=projects,
        config_path=state_path,
    )
    cfg2 = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime2,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg2,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        session_mode="chat",
        files=TelegramFilesSettings(
            enabled=True,
            auto_put=True,
            auto_put_mode="prompt",
        ),
    )

    async def poller2(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=2,
            text="followup",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            chat_type="private",
            document=TelegramDocument(
                file_id="doc-2",
                file_name="hello2.txt",
                mime_type="text/plain",
                file_size=len(payload),
                raw={"file_id": "doc-2"},
            ),
        )

    await run_main_loop(cfg2, poller2)

    assert runner2.calls[0][1] == ResumeToken(
        engine=CODEX_ENGINE,
        value=resume_value,
    )


@pytest.mark.anyio
async def test_run_main_loop_command_updates_chat_session_resume(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _Command:
        id = "run_cmd"
        description = "run command"

        async def handle(self, ctx):
            await ctx.executor.run_one(commands.RunRequest(prompt="hello"))
            return commands.CommandResult(text="done")

    entrypoints = [
        FakeEntryPoint(
            "run_cmd",
            "takopi.commands.run_cmd:BACKEND",
            plugins.COMMAND_GROUP,
            loader=_Command,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)

    resume_value = "resume-123"
    state_path = tmp_path / "takopi.toml"

    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner(
        [Return(answer="ok")],
        engine=CODEX_ENGINE,
        resume_value=resume_value,
    )
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
        config_path=state_path,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        session_mode="chat",
        show_resume_line=False,
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="/run_cmd",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            chat_type="private",
        )

    await run_main_loop(cfg, poller)

    store = ChatSessionStore(resolve_sessions_path(state_path))
    stored = await store.get_session_resume(123, None, CODEX_ENGINE)
    assert stored == ResumeToken(engine=CODEX_ENGINE, value=resume_value)

    transport2 = FakeTransport()
    runner2 = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg2 = ExecBridgeConfig(
        transport=transport2,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime2 = TransportRuntime(
        router=_make_router(runner2),
        projects=_empty_projects(),
        config_path=state_path,
    )
    cfg2 = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime2,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg2,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        session_mode="chat",
        show_resume_line=False,
    )

    async def poller2(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=2,
            text="followup",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            chat_type="private",
        )

    await run_main_loop(cfg2, poller2)

    assert runner2.calls[0][1] == ResumeToken(
        engine=CODEX_ENGINE,
        value=resume_value,
    )


@pytest.mark.anyio
async def test_run_main_loop_hides_resume_line_when_disabled(
    tmp_path: Path,
) -> None:
    resume_value = "resume-123"
    state_path = tmp_path / "takopi.toml"

    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner(
        [Return(answer="ok")],
        engine=CODEX_ENGINE,
        resume_value=resume_value,
    )
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=tmp_path,
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project="proj",
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=projects,
        config_path=state_path,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        session_mode="chat",
        show_resume_line=False,
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="hello",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            chat_type="private",
        )

    await run_main_loop(cfg, poller)

    assert transport.send_calls
    final_text = transport.send_calls[-1]["message"].text
    assert resume_value not in final_text


@pytest.mark.anyio
async def test_run_main_loop_chat_sessions_isolate_group_senders(
    tmp_path: Path,
) -> None:
    resume_value = "resume-group"
    state_path = tmp_path / "takopi.toml"

    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner(
        [Return(answer="ok")],
        engine=CODEX_ENGINE,
        resume_value=resume_value,
    )
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
        config_path=state_path,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        session_mode="chat",
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=-100,
            message_id=1,
            text="hello",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=111,
            chat_type="supergroup",
        )

    await run_main_loop(cfg, poller)

    runner2 = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    runtime2 = TransportRuntime(
        router=_make_router(runner2),
        projects=_empty_projects(),
        config_path=state_path,
    )
    cfg2 = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime2,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        session_mode="chat",
    )

    async def poller2(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=-100,
            message_id=2,
            text="followup",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=222,
            chat_type="supergroup",
        )

    await run_main_loop(cfg2, poller2)

    assert runner2.calls[0][1] is None


@pytest.mark.anyio
async def test_run_main_loop_new_clears_chat_sessions(tmp_path: Path) -> None:
    state_path = tmp_path / "takopi.toml"
    store = ChatSessionStore(resolve_sessions_path(state_path))
    await store.set_session_resume(
        123, None, ResumeToken(engine=CODEX_ENGINE, value="resume-1")
    )

    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
        config_path=state_path,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        session_mode="chat",
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="/new",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            chat_type="private",
        )

    await run_main_loop(cfg, poller)

    store2 = ChatSessionStore(resolve_sessions_path(state_path))
    assert await store2.get_session_resume(123, None, CODEX_ENGINE) is None


@pytest.mark.anyio
async def test_run_main_loop_new_clears_topic_sessions(tmp_path: Path) -> None:
    state_path = tmp_path / "takopi.toml"
    store = TopicStateStore(resolve_state_path(state_path))
    await store.set_session_resume(
        123, 77, ResumeToken(engine=CODEX_ENGINE, value="resume-1")
    )

    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
        config_path=state_path,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        topics=TelegramTopicsSettings(enabled=True, scope="main"),
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="/new",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            thread_id=77,
            chat_type="supergroup",
        )

    with anyio.fail_after(2):
        await run_main_loop(cfg, poller)

    store2 = TopicStateStore(resolve_state_path(state_path))
    assert await store2.get_session_resume(123, 77, CODEX_ENGINE) is None


@pytest.mark.anyio
async def test_run_main_loop_replies_in_same_thread() -> None:
    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="hello",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            thread_id=77,
        )

    await run_main_loop(cfg, poller)

    reply_calls = [
        call
        for call in transport.send_calls
        if call["options"] is not None and call["options"].reply_to is not None
    ]
    assert reply_calls
    assert all(call["options"].thread_id == 77 for call in reply_calls)


@pytest.mark.anyio
async def test_run_main_loop_batches_media_group_upload(
    tmp_path: Path,
) -> None:
    payloads = {
        "photos/file_1.jpg": b"one",
        "photos/file_2.jpg": b"two",
    }
    file_map = {
        "doc-1": "photos/file_1.jpg",
        "doc-2": "photos/file_2.jpg",
    }

    class _MediaBot(FakeBot):
        async def get_file(self, file_id: str) -> File | None:
            file_path = file_map.get(file_id)
            if file_path is None:
                return None
            return File(file_path=file_path)

        async def download_file(self, file_path: str) -> bytes | None:
            return payloads.get(file_path)

    transport = FakeTransport()
    bot = _MediaBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=tmp_path,
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project=None,
    )
    runtime = TransportRuntime(router=_make_router(runner), projects=projects)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=BATCH_MEDIA_GROUP_DEBOUNCE_S,
        files=TelegramFilesSettings(enabled=True, auto_put=True),
    )
    msg1 = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=1,
        text="/file put /proj incoming/test1",
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=321,
        chat_type="private",
        media_group_id="grp-1",
        document=TelegramDocument(
            file_id="doc-1",
            file_name=None,
            mime_type="image/jpeg",
            file_size=len(payloads["photos/file_1.jpg"]),
            raw={"file_id": "doc-1"},
        ),
    )
    msg2 = TelegramIncomingMessage(
        transport="telegram",
        chat_id=123,
        message_id=2,
        text="",
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=321,
        chat_type="private",
        media_group_id="grp-1",
        document=TelegramDocument(
            file_id="doc-2",
            file_name=None,
            mime_type="image/jpeg",
            file_size=len(payloads["photos/file_2.jpg"]),
            raw={"file_id": "doc-2"},
        ),
    )

    stop_polling = anyio.Event()

    async def poller(_cfg: TelegramBridgeConfig):
        yield msg1
        yield msg2
        await stop_polling.wait()

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_main_loop, cfg, poller)
        try:
            with anyio.fail_after(3):
                while len(transport.send_calls) < 1:
                    await anyio.sleep(0.05)
            assert len(transport.send_calls) == 1
            text = transport.send_calls[0]["message"].text
            assert "saved file_1.jpg, file_2.jpg" in text
            assert "to incoming/test1/" in text
            target_dir = tmp_path / "incoming" / "test1"
            assert (target_dir / "file_1.jpg").read_bytes() == payloads[
                "photos/file_1.jpg"
            ]
            assert (target_dir / "file_2.jpg").read_bytes() == payloads[
                "photos/file_2.jpg"
            ]
        finally:
            stop_polling.set()
            tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_run_main_loop_handles_command_plugins(monkeypatch) -> None:
    class _Command:
        id = "echo_cmd"
        description = "echo"

        async def handle(self, ctx):
            return commands.CommandResult(text=f"echo:{ctx.args_text}")

    entrypoints = [
        FakeEntryPoint(
            "echo_cmd",
            "takopi.commands.echo:BACKEND",
            plugins.COMMAND_GROUP,
            loader=_Command,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)

    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="/echo_cmd hello",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
        )

    await run_main_loop(cfg, poller)

    assert runner.calls == []
    assert transport.send_calls
    assert transport.send_calls[-1]["message"].text == "echo:hello"


@pytest.mark.anyio
async def test_run_main_loop_command_uses_project_default_engine(
    monkeypatch,
) -> None:
    class _Command:
        id = "use_project"
        description = "use project default"

        async def handle(self, ctx):
            result = await ctx.executor.run_one(
                commands.RunRequest(
                    prompt="hello",
                    context=RunContext(project="proj"),
                ),
                mode="capture",
            )
            return commands.CommandResult(text=f"ran:{result.engine}")

    entrypoints = [
        FakeEntryPoint(
            "use_project",
            "takopi.commands.use_project:BACKEND",
            plugins.COMMAND_GROUP,
            loader=_Command,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)

    transport = FakeTransport()
    bot = FakeBot()
    codex_runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    pi_runner = ScriptRunner([Return(answer="ok")], engine="pi")
    router = AutoRouter(
        entries=[
            RunnerEntry(engine=codex_runner.engine, runner=codex_runner),
            RunnerEntry(engine=pi_runner.engine, runner=pi_runner),
        ],
        default_engine=codex_runner.engine,
    )
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=Path("."),
                worktrees_dir=Path(".worktrees"),
                default_engine=pi_runner.engine,
            )
        },
        default_project=None,
    )
    runtime = TransportRuntime(
        router=router,
        projects=projects,
    )
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="/use_project",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
        )

    await run_main_loop(cfg, poller)

    assert codex_runner.calls == []
    assert len(pi_runner.calls) == 1
    assert transport.send_calls[-1]["message"].text == "ran:pi"


@pytest.mark.anyio
async def test_run_main_loop_command_defaults_to_chat_project(
    monkeypatch,
) -> None:
    class _Command:
        id = "auto_ctx"
        description = "auto context"

        async def handle(self, ctx):
            result = await ctx.executor.run_one(
                commands.RunRequest(prompt="hello"),
                mode="capture",
            )
            return commands.CommandResult(text=f"ran:{result.engine}")

    entrypoints = [
        FakeEntryPoint(
            "auto_ctx",
            "takopi.commands.auto_ctx:BACKEND",
            plugins.COMMAND_GROUP,
            loader=_Command,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)

    transport = FakeTransport()
    bot = FakeBot()
    codex_runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    pi_runner = ScriptRunner([Return(answer="ok")], engine="pi")
    router = AutoRouter(
        entries=[
            RunnerEntry(engine=codex_runner.engine, runner=codex_runner),
            RunnerEntry(engine=pi_runner.engine, runner=pi_runner),
        ],
        default_engine=codex_runner.engine,
    )
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=Path("."),
                worktrees_dir=Path(".worktrees"),
                default_engine=pi_runner.engine,
                chat_id=-42,
            )
        },
        default_project=None,
        chat_map={-42: "proj"},
    )
    runtime = TransportRuntime(
        router=router,
        projects=projects,
    )
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=-42,
            message_id=1,
            text="/auto_ctx",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
        )

    await run_main_loop(cfg, poller)

    assert codex_runner.calls == []
    assert len(pi_runner.calls) == 1
    assert transport.send_calls[-1]["message"].text == "ran:pi"


@pytest.mark.anyio
async def test_run_main_loop_refreshes_command_ids(monkeypatch) -> None:
    class _Command:
        id = "late_cmd"
        description = "late command"

        async def handle(self, ctx):
            return commands.CommandResult(text="late")

    entrypoints = [
        FakeEntryPoint(
            "late_cmd",
            "takopi.commands.late:BACKEND",
            plugins.COMMAND_GROUP,
            loader=_Command,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)

    calls = {"count": 0}

    def _list_command_ids(*, allowlist=None):
        _ = allowlist
        calls["count"] += 1
        if calls["count"] == 1:
            return []
        return ["late_cmd"]

    monkeypatch.setattr(telegram_loop, "list_command_ids", _list_command_ids)

    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="/late_cmd hello",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
        )

    await run_main_loop(cfg, poller)

    assert calls["count"] >= 2
    assert transport.send_calls[-1]["message"].text == "late"


@pytest.mark.anyio
async def test_run_main_loop_mentions_only_skips_voice_and_files(
    monkeypatch, tmp_path
) -> None:
    calls = {"voice": 0, "file": 0}

    async def fake_transcribe_voice(**kwargs):
        _ = kwargs
        calls["voice"] += 1
        return "hello"

    async def fake_handle_file_put_default(*args, **kwargs):
        _ = args, kwargs
        calls["file"] += 1
        return None

    monkeypatch.setattr(telegram_loop, "transcribe_voice", fake_transcribe_voice)
    monkeypatch.setattr(
        telegram_loop, "_handle_file_put_default", fake_handle_file_put_default
    )

    transport = FakeTransport()
    bot = FakeBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    config_path = tmp_path / "takopi.toml"
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
        config_path=config_path,
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=FAST_FORWARD_COALESCE_S,
        media_group_debounce_s=FAST_MEDIA_GROUP_DEBOUNCE_S,
        voice_transcription=True,
        files=TelegramFilesSettings(enabled=True, auto_put=True),
    )

    prefs = ChatPrefsStore(resolve_prefs_path(config_path))
    await prefs.set_trigger_mode(123, "mentions")

    voice = TelegramVoice(
        file_id="voice-id",
        mime_type="audio/ogg",
        file_size=5,
        duration=1,
        raw={},
    )
    document = TelegramDocument(
        file_id="doc-id",
        file_name="doc.txt",
        mime_type="text/plain",
        file_size=5,
        raw={},
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=1,
            text="",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            voice=voice,
            raw={},
        )
        yield TelegramIncomingMessage(
            transport="telegram",
            chat_id=123,
            message_id=2,
            text="",
            reply_to_message_id=None,
            reply_to_text=None,
            sender_id=123,
            document=document,
            raw={},
        )

    await run_main_loop(cfg, poller)

    assert calls["voice"] == 0
    assert calls["file"] == 0
    assert runner.calls == []
