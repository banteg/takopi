from pathlib import Path

import anyio
import pytest

from takopi import commands, plugins
import takopi.telegram.bridge as bridge
from takopi.directives import parse_directives
from takopi.telegram.bridge import (
    TelegramBridgeConfig,
    TelegramTransport,
    _build_bot_commands,
    _handle_cancel,
    _is_cancel_command,
    _send_with_resume,
    run_main_loop,
)
from takopi.context import RunContext
from takopi.config import ProjectConfig, ProjectsConfig, empty_projects_config
from takopi.runner_bridge import ExecBridgeConfig, RunningTask
from takopi.markdown import MarkdownPresenter
from takopi.model import EngineId, ResumeToken
from takopi.router import AutoRouter, RunnerEntry
from takopi.transport_runtime import TransportRuntime
from takopi.runners.mock import Return, ScriptRunner, Sleep, Wait
from takopi.telegram.types import TelegramIncomingMessage
from takopi.transport import MessageRef, RenderedMessage, SendOptions
from tests.plugin_fixtures import FakeEntryPoint, install_entrypoints

CODEX_ENGINE = EngineId("codex")


def _make_router(runner) -> AutoRouter:
    return AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )


class _FakeTransport:
    def __init__(self, progress_ready: anyio.Event | None = None) -> None:
        self._next_id = 1
        self.send_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.delete_calls: list[MessageRef] = []
        self.progress_ready = progress_ready
        self.progress_ref: MessageRef | None = None

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
        if (
            self.progress_ref is None
            and options is not None
            and options.reply_to is not None
            and options.notify is False
        ):
            self.progress_ref = ref
            if self.progress_ready is not None:
                self.progress_ready.set()
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


class _FakeBot:
    def __init__(self) -> None:
        self.command_calls: list[dict] = []
        self.send_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.delete_calls: list[dict] = []

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

    async def get_file(self, file_id: str) -> dict | None:
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
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        *,
        replace_message_id: int | None = None,
    ) -> dict:
        self.send_calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "disable_notification": disable_notification,
                "entities": entities,
                "parse_mode": parse_mode,
                "replace_message_id": replace_message_id,
            }
        )
        return {"message_id": 1}

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        *,
        wait: bool = True,
    ) -> dict:
        self.edit_calls.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "entities": entities,
                "parse_mode": parse_mode,
                "wait": wait,
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

    async def get_me(self) -> dict | None:
        return {"id": 1}

    async def close(self) -> None:
        return None


def _make_cfg(
    transport: _FakeTransport, runner: ScriptRunner | None = None
) -> TelegramBridgeConfig:
    if runner is None:
        runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=empty_projects_config(),
    )
    return TelegramBridgeConfig(
        bot=_FakeBot(),
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
    )


def test_parse_directives_inline_engine() -> None:
    directives = parse_directives(
        "/claude do it",
        engine_ids=("codex", "claude"),
        projects=empty_projects_config(),
    )
    assert directives.engine == "claude"
    assert directives.prompt == "do it"


def test_parse_directives_newline() -> None:
    directives = parse_directives(
        "/codex\nhello",
        engine_ids=("codex", "claude"),
        projects=empty_projects_config(),
    )
    assert directives.engine == "codex"
    assert directives.prompt == "hello"


def test_parse_directives_ignores_unknown() -> None:
    directives = parse_directives(
        "/unknown hi",
        engine_ids=("codex", "claude"),
        projects=empty_projects_config(),
    )
    assert directives.engine is None
    assert directives.prompt == "/unknown hi"


def test_parse_directives_bot_suffix() -> None:
    directives = parse_directives(
        "/claude@bunny_agent_bot hi",
        engine_ids=("claude",),
        projects=empty_projects_config(),
    )
    assert directives.engine == "claude"
    assert directives.prompt == "hi"


def test_parse_directives_only_first_non_empty_line() -> None:
    directives = parse_directives(
        "hello\n/claude hi",
        engine_ids=("codex", "claude"),
        projects=empty_projects_config(),
    )
    assert directives.engine is None
    assert directives.prompt == "hello\n/claude hi"


def test_build_bot_commands_includes_cancel_and_engine() -> None:
    runner = ScriptRunner(
        [Return(answer="ok")], engine=CODEX_ENGINE, resume_value="sid"
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=empty_projects_config(),
    )
    commands = _build_bot_commands(runtime)

    assert {"command": "cancel", "description": "cancel run"} in commands
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
    commands = _build_bot_commands(runtime)

    assert any(cmd["command"] == "good" for cmd in commands)
    assert not any(cmd["command"] == "bad-name" for cmd in commands)


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
        projects=empty_projects_config(),
    )

    commands_list = _build_bot_commands(runtime)

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
    commands = _build_bot_commands(runtime)

    assert len(commands) == 100
    assert any(cmd["command"] == "codex" for cmd in commands)
    assert any(cmd["command"] == "cancel" for cmd in commands)


@pytest.mark.anyio
async def test_telegram_transport_passes_replace_and_wait() -> None:
    bot = _FakeBot()
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
async def test_telegram_transport_edit_wait_false_returns_ref() -> None:
    class _OutboxBot:
        def __init__(self) -> None:
            self.edit_calls: list[dict[str, object]] = []

        async def get_updates(
            self,
            offset: int | None,
            timeout_s: int = 50,
            allowed_updates: list[str] | None = None,
        ) -> list[dict] | None:
            return None

        async def get_file(self, file_id: str) -> dict | None:
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
            entities: list[dict] | None = None,
            parse_mode: str | None = None,
            *,
            replace_message_id: int | None = None,
        ) -> dict | None:
            return None

        async def edit_message_text(
            self,
            chat_id: int,
            message_id: int,
            text: str,
            entities: list[dict] | None = None,
            parse_mode: str | None = None,
            *,
            wait: bool = True,
        ) -> dict | None:
            self.edit_calls.append(
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "entities": entities,
                    "parse_mode": parse_mode,
                    "wait": wait,
                }
            )
            if not wait:
                return None
            return {"message_id": message_id}

        async def delete_message(
            self,
            chat_id: int,
            message_id: int,
        ) -> bool:
            return False

        async def set_my_commands(
            self,
            commands: list[dict[str, object]],
            *,
            scope: dict[str, object] | None = None,
            language_code: str | None = None,
        ) -> bool:
            return False

        async def get_me(self) -> dict | None:
            return None

        async def close(self) -> None:
            return None

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
    transport = _FakeTransport()
    cfg = _make_cfg(transport)
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

    await _handle_cancel(cfg, msg, running_tasks)

    assert len(transport.send_calls) == 1
    assert "reply to the progress message" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_handle_cancel_with_no_progress_message_says_nothing_running() -> None:
    transport = _FakeTransport()
    cfg = _make_cfg(transport)
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

    await _handle_cancel(cfg, msg, running_tasks)

    assert len(transport.send_calls) == 1
    assert "nothing is currently running" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_handle_cancel_with_finished_task_says_nothing_running() -> None:
    transport = _FakeTransport()
    cfg = _make_cfg(transport)
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

    await _handle_cancel(cfg, msg, running_tasks)

    assert len(transport.send_calls) == 1
    assert "nothing is currently running" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_handle_cancel_cancels_running_task() -> None:
    transport = _FakeTransport()
    cfg = _make_cfg(transport)
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
    await _handle_cancel(cfg, msg, running_tasks)

    assert running_task.cancel_requested.is_set() is True
    assert len(transport.send_calls) == 0  # No error message sent


@pytest.mark.anyio
async def test_handle_cancel_only_cancels_matching_progress_message() -> None:
    transport = _FakeTransport()
    cfg = _make_cfg(transport)
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

    await _handle_cancel(cfg, msg, running_tasks)

    assert task_first.cancel_requested.is_set() is True
    assert task_second.cancel_requested.is_set() is False
    assert len(transport.send_calls) == 0


def test_cancel_command_accepts_extra_text() -> None:
    assert _is_cancel_command("/cancel now") is True
    assert _is_cancel_command("/cancel@takopi please") is True
    assert _is_cancel_command("/cancelled") is False


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
        reply_text="`ctx: takopi @ feat/api`",
    )

    assert resolved.prompt == "do it"
    assert resolved.resume_token is None
    assert resolved.engine_override is None
    assert resolved.context == RunContext(project="takopi", branch="feat/api")


@pytest.mark.anyio
async def test_send_with_resume_waits_for_token() -> None:
    transport = _FakeTransport()
    cfg = _make_cfg(transport)
    sent: list[tuple[int, int, str, ResumeToken, RunContext | None]] = []

    async def enqueue(
        chat_id: int,
        user_msg_id: int,
        text: str,
        resume: ResumeToken,
        context: RunContext | None,
    ) -> None:
        sent.append((chat_id, user_msg_id, text, resume, context))

    running_task = RunningTask()

    async def trigger_resume() -> None:
        await anyio.sleep(0)
        running_task.resume = ResumeToken(engine=CODEX_ENGINE, value="abc123")
        running_task.resume_ready.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(trigger_resume)
        await _send_with_resume(
            cfg,
            enqueue,
            running_task,
            123,
            10,
            "hello",
        )

    assert sent == [
        (123, 10, "hello", ResumeToken(engine=CODEX_ENGINE, value="abc123"), None)
    ]
    assert transport.send_calls == []


@pytest.mark.anyio
async def test_send_with_resume_reports_when_missing() -> None:
    transport = _FakeTransport()
    cfg = _make_cfg(transport)
    sent: list[tuple[int, int, str, ResumeToken, RunContext | None]] = []

    async def enqueue(
        chat_id: int,
        user_msg_id: int,
        text: str,
        resume: ResumeToken,
        context: RunContext | None,
    ) -> None:
        sent.append((chat_id, user_msg_id, text, resume, context))

    running_task = RunningTask()
    running_task.done.set()

    await _send_with_resume(
        cfg,
        enqueue,
        running_task,
        123,
        10,
        "hello",
    )

    assert sent == []
    assert transport.send_calls
    assert "resume token" in transport.send_calls[-1]["message"].text.lower()


@pytest.mark.anyio
async def test_run_main_loop_routes_reply_to_running_resume() -> None:
    progress_ready = anyio.Event()
    stop_polling = anyio.Event()
    reply_ready = anyio.Event()
    hold = anyio.Event()

    transport = _FakeTransport(progress_ready=progress_ready)
    bot = _FakeBot()
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
        projects=empty_projects_config(),
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
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

    transport = _FakeTransport()
    bot = _FakeBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=empty_projects_config(),
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
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

    transport = _FakeTransport()
    bot = _FakeBot()
    codex_runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    pi_runner = ScriptRunner([Return(answer="ok")], engine=EngineId("pi"))
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

    transport = _FakeTransport()
    bot = _FakeBot()
    codex_runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    pi_runner = ScriptRunner([Return(answer="ok")], engine=EngineId("pi"))
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

    monkeypatch.setattr(bridge, "list_command_ids", _list_command_ids)

    transport = _FakeTransport()
    bot = _FakeBot()
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=empty_projects_config(),
    )
    cfg = TelegramBridgeConfig(
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
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
