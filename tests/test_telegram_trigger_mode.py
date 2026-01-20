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


def test_should_trigger_run_reply_to_bot_none_does_not_trigger() -> None:
    """Test that reply_to_is_bot=None does not trigger the bot.

    This is important for the forum topic fix: when a message is in a bot-created
    topic, reply_to_is_bot is set to None (instead of True) because the reply is
    to a forum_topic_created service message, not an actual bot message.
    """
    runtime = _runtime()
    # Message with reply_to_is_bot=None should NOT trigger (no mention, no command)
    msg = _msg("hello", reply_to_is_bot=None)
    assert not should_trigger_run(
        msg,
        bot_username="bot",
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )


def test_should_trigger_run_forum_topic_message_without_mention() -> None:
    """Test that a message in a forum topic without mention does not trigger.

    Simulates a message in a bot-created forum topic where:
    - reply_to_is_bot is None (because it's a topic creation message)
    - No @mention of the bot
    - No slash command

    The bot should NOT trigger on this message.
    """
    runtime = _runtime()
    msg = _msg(
        "Just chatting in the topic",
        reply_to_is_bot=None,
        thread_id=163,
        is_topic_message=True,
    )
    assert not should_trigger_run(
        msg,
        bot_username="TakopiBot",
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )


def test_should_trigger_run_forum_topic_message_with_mention() -> None:
    """Test that a message in a forum topic WITH mention DOES trigger.

    Even though reply_to_is_bot is None (topic creation), the @mention
    should still trigger the bot. Note: bot_username must be lowercase
    because the check lowercases the message text.
    """
    runtime = _runtime()
    msg = _msg(
        "Hey @takopibot can you help?",
        reply_to_is_bot=None,
        thread_id=163,
        is_topic_message=True,
    )
    assert should_trigger_run(
        msg,
        bot_username="takopibot",
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )


def test_should_trigger_run_forum_topic_message_with_command() -> None:
    """Test that a slash command in a forum topic DOES trigger.

    Even though reply_to_is_bot is None (topic creation), slash commands
    should still trigger the bot.
    """
    runtime = _runtime()
    msg = _msg(
        "/agent do something",
        reply_to_is_bot=None,
        thread_id=163,
        is_topic_message=True,
    )
    assert should_trigger_run(
        msg,
        bot_username="TakopiBot",
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )


def test_should_trigger_run_explicit_reply_to_bot_in_topic() -> None:
    """Test that an explicit reply to a bot message in a topic DOES trigger.

    When a user explicitly replies to a bot message (not the topic creation),
    reply_to_is_bot should be True and trigger the bot.
    """
    runtime = _runtime()
    msg = _msg(
        "Thanks for the help!",
        reply_to_is_bot=True,  # Explicit reply to bot message
        thread_id=163,
        is_topic_message=True,
    )
    assert should_trigger_run(
        msg,
        bot_username="TakopiBot",
        runtime=runtime,
        command_ids=set(),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
    )
