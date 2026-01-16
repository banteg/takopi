from dataclasses import replace
from pathlib import Path

import pytest

from takopi.settings import TelegramTopicsSettings
from takopi.telegram.chat_sessions import ChatSessionStore
from takopi.telegram.commands.topics import (
    _handle_chat_new_command,
    _handle_ctx_command,
    _handle_new_command,
    _handle_topic_command,
)
from takopi.telegram.topic_state import TopicStateStore
from takopi.telegram.types import TelegramIncomingMessage
from tests.telegram_fakes import FakeTransport, make_cfg


def _msg(
    text: str,
    *,
    chat_id: int = 123,
    message_id: int = 1,
    thread_id: int | None = None,
    chat_type: str | None = "private",
) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=1,
        thread_id=thread_id,
        chat_type=chat_type,
    )


@pytest.mark.anyio
async def test_ctx_command_requires_topic(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        topics=TelegramTopicsSettings(enabled=True, scope="all"),
    )
    store = TopicStateStore(tmp_path / "topics.json")
    msg = _msg("/ctx")

    await _handle_ctx_command(
        cfg,
        msg,
        args_text="",
        store=store,
        resolved_scope="all",
        scope_chat_ids=frozenset({msg.chat_id}),
    )

    text = transport.send_calls[-1]["message"].text
    assert "only works inside a topic" in text


@pytest.mark.anyio
async def test_new_command_requires_topic(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        topics=TelegramTopicsSettings(enabled=True, scope="all"),
    )
    store = TopicStateStore(tmp_path / "topics.json")
    msg = _msg("/new")

    await _handle_new_command(
        cfg,
        msg,
        store=store,
        resolved_scope="all",
        scope_chat_ids=frozenset({msg.chat_id}),
    )

    text = transport.send_calls[-1]["message"].text
    assert "only works inside a topic" in text


@pytest.mark.anyio
async def test_chat_new_command_no_sessions(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    store = ChatSessionStore(tmp_path / "sessions.json")
    msg = _msg("/new", chat_type="private")

    await _handle_chat_new_command(cfg, msg, store, session_key=None)

    text = transport.send_calls[-1]["message"].text
    assert "no stored sessions" in text


@pytest.mark.anyio
async def test_chat_new_command_group_clears(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    store = ChatSessionStore(tmp_path / "sessions.json")
    msg = _msg("/new", chat_type="supergroup")

    await _handle_chat_new_command(cfg, msg, store, session_key=(msg.chat_id, 1))

    text = transport.send_calls[-1]["message"].text
    assert "cleared stored sessions for you in this chat" in text


@pytest.mark.anyio
async def test_topic_command_requires_args(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        topics=TelegramTopicsSettings(enabled=True, scope="all"),
    )
    store = TopicStateStore(tmp_path / "topics.json")
    msg = _msg("/topic")

    await _handle_topic_command(
        cfg,
        msg,
        args_text="",
        store=store,
        resolved_scope="all",
        scope_chat_ids=frozenset({msg.chat_id}),
    )

    text = transport.send_calls[-1]["message"].text
    assert "usage: /topic" in text
