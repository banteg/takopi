from dataclasses import replace
from pathlib import Path

import pytest

from takopi.telegram.api_models import ChatMember
from takopi.telegram.commands.agent import _handle_agent_command
from takopi.telegram.commands.trigger import _handle_trigger_command
from takopi.telegram.chat_prefs import ChatPrefsStore
from takopi.telegram.topic_state import TopicStateStore
from takopi.telegram.types import TelegramIncomingMessage
from takopi.settings import TelegramTopicsSettings
from tests.telegram_fakes import FakeBot, FakeTransport, make_cfg


def _msg(
    text: str,
    *,
    chat_id: int = 123,
    message_id: int = 10,
    sender_id: int | None = 42,
    chat_type: str | None = "private",
    thread_id: int | None = None,
) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=sender_id,
        chat_type=chat_type,
        thread_id=thread_id,
    )


def _last_text(transport: FakeTransport) -> str:
    assert transport.send_calls
    return transport.send_calls[-1]["message"].text


class _MemberBot(FakeBot):
    async def get_chat_member(self, chat_id: int, user_id: int) -> ChatMember | None:
        _ = chat_id, user_id
        return ChatMember(status="member", can_manage_topics=False)


@pytest.mark.anyio
async def test_agent_show_private_defaults() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/agent", chat_type="private")

    await _handle_agent_command(
        cfg,
        msg,
        args_text="",
        ambient_context=None,
        topic_store=None,
        chat_prefs=None,
    )

    text = _last_text(transport)
    assert "engine: codex" in text
    assert "available: codex" in text


@pytest.mark.anyio
async def test_agent_set_clear_group_admin(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    prefs = ChatPrefsStore(tmp_path / "prefs.json")
    msg = _msg("/agent set codex", chat_type="supergroup")

    await _handle_agent_command(
        cfg,
        msg,
        args_text="set codex",
        ambient_context=None,
        topic_store=None,
        chat_prefs=prefs,
    )

    assert await prefs.get_default_engine(msg.chat_id) == "codex"
    assert "chat default engine set" in _last_text(transport)

    await _handle_agent_command(
        cfg,
        msg,
        args_text="clear",
        ambient_context=None,
        topic_store=None,
        chat_prefs=prefs,
    )

    assert await prefs.get_default_engine(msg.chat_id) is None
    assert "chat default engine cleared" in _last_text(transport)


@pytest.mark.anyio
async def test_agent_set_denied_for_non_admin(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), bot=_MemberBot())
    prefs = ChatPrefsStore(tmp_path / "prefs.json")
    msg = _msg("/agent set codex", chat_type="supergroup")

    await _handle_agent_command(
        cfg,
        msg,
        args_text="set codex",
        ambient_context=None,
        topic_store=None,
        chat_prefs=prefs,
    )

    assert await prefs.get_default_engine(msg.chat_id) is None
    assert "restricted to group admins" in _last_text(transport)


@pytest.mark.anyio
async def test_agent_set_invalid_engine(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/agent set nope", chat_type="private")

    await _handle_agent_command(
        cfg,
        msg,
        args_text="set nope",
        ambient_context=None,
        topic_store=None,
        chat_prefs=ChatPrefsStore(tmp_path / "prefs.json"),
    )

    text = _last_text(transport)
    assert "unknown engine" in text
    assert "available engines" in text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("topic_mode", "chat_mode", "expected_source", "expected_trigger"),
    [
        ("mentions", None, "topic override", "mentions"),
        (None, "mentions", "chat default", "mentions"),
        (None, None, "default", "all"),
    ],
)
async def test_trigger_show_sources(
    tmp_path: Path,
    topic_mode: str | None,
    chat_mode: str | None,
    expected_source: str,
    expected_trigger: str,
) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        topics=TelegramTopicsSettings(enabled=True, scope="all"),
    )
    topic_store = TopicStateStore(tmp_path / "topics.json")
    chat_prefs = ChatPrefsStore(tmp_path / "prefs.json")
    msg = _msg("/trigger", chat_type="supergroup", thread_id=7)

    if topic_mode is not None:
        await topic_store.set_trigger_mode(msg.chat_id, msg.thread_id or 0, topic_mode)
    if chat_mode is not None:
        await chat_prefs.set_trigger_mode(msg.chat_id, chat_mode)

    await _handle_trigger_command(
        cfg,
        msg,
        args_text="",
        _ambient_context=None,
        topic_store=topic_store,
        chat_prefs=chat_prefs,
    )

    text = _last_text(transport)
    assert f"trigger: {expected_trigger} ({expected_source})" in text
    assert "available: all, mentions" in text


@pytest.mark.anyio
async def test_trigger_set_clear_permissions(tmp_path: Path) -> None:
    transport = FakeTransport()
    prefs = ChatPrefsStore(tmp_path / "prefs.json")
    msg = _msg("/trigger mentions", chat_type="supergroup")

    denied_cfg = replace(make_cfg(transport), bot=_MemberBot())
    await _handle_trigger_command(
        denied_cfg,
        msg,
        args_text="mentions",
        _ambient_context=None,
        topic_store=None,
        chat_prefs=prefs,
    )
    assert await prefs.get_trigger_mode(msg.chat_id) is None
    assert "restricted to group admins" in _last_text(transport)

    transport = FakeTransport()
    allow_cfg = make_cfg(transport)
    await _handle_trigger_command(
        allow_cfg,
        msg,
        args_text="mentions",
        _ambient_context=None,
        topic_store=None,
        chat_prefs=prefs,
    )
    assert await prefs.get_trigger_mode(msg.chat_id) == "mentions"
    assert "chat trigger mode set" in _last_text(transport)

    await _handle_trigger_command(
        allow_cfg,
        msg,
        args_text="clear",
        _ambient_context=None,
        topic_store=None,
        chat_prefs=prefs,
    )
    assert await prefs.get_trigger_mode(msg.chat_id) is None
    assert "chat trigger mode reset" in _last_text(transport)


@pytest.mark.anyio
async def test_trigger_missing_sender_denied(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    prefs = ChatPrefsStore(tmp_path / "prefs.json")
    msg = _msg("/trigger all", chat_type="supergroup", sender_id=None)

    await _handle_trigger_command(
        cfg,
        msg,
        args_text="all",
        _ambient_context=None,
        topic_store=None,
        chat_prefs=prefs,
    )

    assert await prefs.get_trigger_mode(msg.chat_id) is None
    assert "cannot verify sender" in _last_text(transport)


@pytest.mark.anyio
async def test_trigger_topic_unavailable() -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        topics=TelegramTopicsSettings(enabled=True, scope="all"),
    )
    msg = _msg("/trigger mentions", chat_type="supergroup", thread_id=3)

    await _handle_trigger_command(
        cfg,
        msg,
        args_text="mentions",
        _ambient_context=None,
        topic_store=None,
        chat_prefs=None,
    )

    assert "topic trigger settings are unavailable" in _last_text(transport)


@pytest.mark.anyio
async def test_trigger_chat_prefs_unavailable() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/trigger mentions", chat_type="supergroup")

    await _handle_trigger_command(
        cfg,
        msg,
        args_text="mentions",
        _ambient_context=None,
        topic_store=None,
        chat_prefs=None,
    )

    assert "chat trigger settings are unavailable" in _last_text(transport)
