from __future__ import annotations

import pytest

from takopi.telegram.loop import _format_prompt_line, _format_sender, _format_timestamp
from takopi.telegram.types import TelegramIncomingMessage


def _message(**overrides: object) -> TelegramIncomingMessage:
    data = {
        "transport": "telegram",
        "chat_id": 1,
        "message_id": 1,
        "text": "hello",
        "reply_to_message_id": None,
        "reply_to_text": None,
        "sender_id": 1,
        "thread_id": 77,
    }
    data.update(overrides)
    return TelegramIncomingMessage(**data)


def test_format_timestamp_handles_missing_and_known() -> None:
    assert _format_timestamp(None) == "time=?"
    assert _format_timestamp(1_720_000_000) == "2024-07-03T09:46:40Z"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"sender_first_name": "Test", "sender_last_name": "User"}, "Test User"),
        ({"sender_first_name": "Test", "sender_last_name": None}, "Test"),
        ({"sender_username": "tester"}, "@tester"),
        (
            {
                "sender_id": 42,
                "sender_username": None,
                "sender_first_name": None,
                "sender_last_name": None,
            },
            "user:42",
        ),
        (
            {
                "sender_id": None,
                "sender_username": None,
                "sender_first_name": None,
                "sender_last_name": None,
            },
            "unknown",
        ),
    ],
)
def test_format_sender_fallbacks(overrides: dict[str, object], expected: str) -> None:
    msg = _message(**overrides)
    assert _format_sender(msg) == expected


def test_format_prompt_line_party_includes_header_and_text() -> None:
    msg = _message(
        thread_id=123,
        date=1_720_000_000,
        sender_first_name="Test",
        sender_last_name="User",
    )

    assert (
        _format_prompt_line(msg, "hello", prompt_mode="party")
        == "[2024-07-03T09:46:40Z] Test User: hello"
    )


def test_format_prompt_line_party_blank_text_returns_header_only() -> None:
    msg = _message(
        thread_id=123,
        date=None,
        sender_id=None,
        sender_username=None,
        sender_first_name=None,
        sender_last_name=None,
    )

    assert (
        _format_prompt_line(msg, "   ", prompt_mode="party")
        == "[time=?] unknown:"
    )


def test_format_prompt_line_default_skips_header() -> None:
    msg = _message(
        thread_id=123,
        date=1_720_000_000,
        sender_first_name="Test",
        sender_last_name="User",
    )

    assert _format_prompt_line(msg, "hello") == "hello"
