from pathlib import Path

from takopi.config import ProjectConfig, ProjectsConfig
from takopi.ids import RESERVED_CHAT_COMMANDS
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.telegram.trigger_mode import should_trigger_run
from takopi.telegram.types import TelegramIncomingMessage
from takopi.transport_runtime import TransportRuntime


def _runtime() -> TransportRuntime:
    runner = ScriptRunner([Return(answer="ok")], engine="codex")
    router = AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=Path("."),
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project=None,
    )
    return TransportRuntime(router=router, projects=projects)


def _msg(text: str, **kwargs) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=1,
        message_id=1,
        text=text,
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=1,
        **kwargs,
    )


def test_should_trigger_run_mentions() -> None:
    runtime = _runtime()
    msg = _msg("hello @bot")
    assert should_trigger_run(
        msg,
        bot_username="bot",
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )


def test_should_trigger_run_engine_and_project() -> None:
    runtime = _runtime()
    assert should_trigger_run(
        _msg("/codex hello"),
        bot_username=None,
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )
    assert should_trigger_run(
        _msg("/proj hello"),
        bot_username=None,
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )


def test_should_trigger_run_reply_to_bot() -> None:
    runtime = _runtime()
    msg = _msg("hello", reply_to_is_bot=True)
    assert should_trigger_run(
        msg,
        bot_username=None,
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )


def test_should_trigger_run_ignores_implicit_topic_reply_to_root() -> None:
    runtime = _runtime()
    msg = TelegramIncomingMessage(
        transport="telegram",
        chat_id=1,
        message_id=187,
        text="hello",
        reply_to_message_id=163,
        reply_to_text=None,
        reply_to_is_bot=True,
        reply_to_username="TakopiBot",
        sender_id=1,
        thread_id=163,
        is_topic_message=True,
        chat_type="supergroup",
        is_forum=True,
    )
    assert not should_trigger_run(
        msg,
        bot_username=None,
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )


def test_should_trigger_run_known_commands() -> None:
    runtime = _runtime()
    assert should_trigger_run(
        _msg("/agent"),
        bot_username=None,
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )
    assert should_trigger_run(
        _msg("/ping"),
        bot_username=None,
        runtime=runtime,
        command_ids={"ping"},
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )


def test_should_trigger_run_ignores_unknown_commands() -> None:
    runtime = _runtime()
    assert not should_trigger_run(
        _msg("/wat"),
        bot_username=None,
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )
