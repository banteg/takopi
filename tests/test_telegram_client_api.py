import time

import httpx
import pytest

from takopi.telegram import client_api
from takopi.telegram.api_models import User
from takopi.telegram.client_api import (
    HttpBotClient,
    TelegramRetryAfter,
    retry_after_from_payload,
)


def _response() -> httpx.Response:
    request = httpx.Request("POST", "https://example.com")
    return httpx.Response(200, request=request)


def test_retry_after_from_payload() -> None:
    assert retry_after_from_payload({}) is None
    assert retry_after_from_payload({"parameters": {"retry_after": 2}}) == 2.0


def test_parse_envelope_invalid_payload() -> None:
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    assert (
        client._parse_telegram_envelope(
            method="sendMessage",
            resp=_response(),
            payload="nope",
        )
        is None
    )


def test_parse_envelope_rate_limited() -> None:
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    payload = {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}}
    with pytest.raises(TelegramRetryAfter) as exc:
        client._parse_telegram_envelope(
            method="sendMessage",
            resp=_response(),
            payload=payload,
        )
    assert exc.value.retry_after == 1.0


def test_parse_envelope_api_error() -> None:
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    payload = {"ok": False, "error_code": 400, "description": "boom"}
    assert (
        client._parse_telegram_envelope(
            method="sendMessage",
            resp=_response(),
            payload=payload,
        )
        is None
    )


def test_parse_envelope_ok() -> None:
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    payload = {"ok": True, "result": {"message_id": 1}}
    assert client._parse_telegram_envelope(
        method="sendMessage",
        resp=_response(),
        payload=payload,
    ) == {"message_id": 1}


@pytest.mark.anyio
async def test_client_methods_build_params_and_decode() -> None:
    payloads = {
        "getUpdates": [{"update_id": 1}],
        "getFile": {"file_path": "path"},
        "sendMessage": {"message_id": 1, "chat": {"id": 1, "type": "private"}},
        "sendDocument": {"message_id": 2, "chat": {"id": 1, "type": "private"}},
        "editMessageText": {"message_id": 3, "chat": {"id": 1, "type": "private"}},
        "deleteMessage": True,
        "setMyCommands": True,
        "getMe": {"id": 7},
        "answerCallbackQuery": True,
        "getChat": {"id": 5, "type": "private"},
        "getChatMember": {"status": "member"},
        "createForumTopic": {"message_thread_id": 11},
        "editForumTopic": True,
    }

    class _StubClient(HttpBotClient):
        def __init__(self) -> None:
            super().__init__("token", http_client=httpx.AsyncClient())
            self.calls: list[tuple[str, dict | None, dict | None, dict | None]] = []

        async def _request(
            self,
            method: str,
            *,
            json: dict | None = None,
            data: dict | None = None,
            files: dict | None = None,
        ) -> object | None:
            self.calls.append((method, json, data, files))
            return payloads.get(method)

    client = _StubClient()

    updates = await client.get_updates(offset=10, allowed_updates=["message"])
    assert updates and updates[0].update_id == 1

    assert await client.get_file("file") is not None

    msg = await client.send_message(
        1,
        "hi",
        reply_to_message_id=2,
        disable_notification=True,
        message_thread_id=3,
        entities=[{"type": "bold", "offset": 0, "length": 2}],
        parse_mode="Markdown",
        reply_markup={"inline_keyboard": []},
    )
    assert msg and msg.message_id == 1

    doc = await client.send_document(
        1,
        "file.txt",
        b"data",
        reply_to_message_id=2,
        message_thread_id=3,
        disable_notification=True,
        caption="doc",
    )
    assert doc and doc.message_id == 2

    edit = await client.edit_message_text(
        1,
        2,
        "edit",
        entities=[{"type": "italic", "offset": 0, "length": 4}],
        parse_mode="Markdown",
        reply_markup={"inline_keyboard": []},
    )
    assert edit and edit.message_id == 3

    assert await client.delete_message(1, 2) is True
    assert await client.set_my_commands(
        [{"command": "ping", "description": "pong"}],
        scope={"type": "chat"},
        language_code="en",
    )
    assert await client.answer_callback_query("cb", text="ok", show_alert=True) is True
    assert await client.get_chat(1) is not None
    assert await client.get_chat_member(1, 2) is not None
    assert await client.create_forum_topic(1, "topic") is not None
    assert await client.edit_forum_topic(1, 2, "topic") is True

    await client.close()

    send_call = next(call for call in client.calls if call[0] == "sendMessage")
    assert send_call[1]["disable_notification"] is True
    assert send_call[1]["reply_to_message_id"] == 2
    assert send_call[1]["message_thread_id"] == 3
    assert send_call[1]["entities"]
    assert send_call[1]["parse_mode"] == "Markdown"
    assert send_call[1]["link_preview_options"] == {"is_disabled": True}
    assert send_call[1]["reply_markup"]

    doc_call = next(call for call in client.calls if call[0] == "sendDocument")
    assert doc_call[2]["caption"] == "doc"
    assert doc_call[3]["document"][0] == "file.txt"

    edit_call = next(call for call in client.calls if call[0] == "editMessageText")
    assert edit_call[1]["link_preview_options"] == {"is_disabled": True}


@pytest.mark.anyio
async def test_decode_result_invalid_payload_returns_none() -> None:
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    assert client._decode_result(method="getMe", payload=["bad"], model=User) is None
    await client.close()


def _conflict_description() -> str:
    return (
        "Conflict: terminated by other getUpdates request; "
        "make sure that only one bot instance is running"
    )


@pytest.fixture
def fresh_startup_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the module back into its just-started state."""
    monkeypatch.setattr(client_api, "_process_start", time.monotonic())
    monkeypatch.setattr(client_api, "_startup_conflict_attempts", 0)


def test_startup_conflict_backoff_is_exponential_and_capped(
    fresh_startup_window: None,
) -> None:
    description = _conflict_description()
    ladder = [client_api._startup_conflict_backoff(description) for _ in range(6)]
    assert ladder == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


def test_startup_conflict_backoff_resets_after_success(
    fresh_startup_window: None,
) -> None:
    description = _conflict_description()
    client_api._startup_conflict_backoff(description)
    client_api._startup_conflict_backoff(description)
    client_api._clear_startup_conflicts()
    assert client_api._startup_conflict_backoff(description) == 1.0


def test_startup_conflict_ignores_other_conflicts(fresh_startup_window: None) -> None:
    # The webhook variant of 409 is not self-clearing: retrying would hide a real
    # misconfiguration, so it must fall through to normal error handling.
    webhook = "Conflict: can't use getUpdates method while webhook is active"
    assert client_api._startup_conflict_backoff(webhook) is None
    assert client_api._startup_conflict_backoff(None) is None


def test_startup_conflict_expires_after_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Past the window a lingering 409 means a second bridge really is polling
    # this token, which has to stay visible instead of retrying forever.
    monkeypatch.setattr(
        client_api,
        "_process_start",
        time.monotonic() - client_api._STARTUP_CONFLICT_WINDOW_S - 1,
    )
    monkeypatch.setattr(client_api, "_startup_conflict_attempts", 0)
    assert client_api._startup_conflict_backoff(_conflict_description()) is None


def test_parse_envelope_startup_conflict(fresh_startup_window: None) -> None:
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    payload = {
        "ok": False,
        "error_code": 409,
        "description": _conflict_description(),
    }
    with pytest.raises(TelegramRetryAfter) as exc:
        client._parse_telegram_envelope(
            method="getUpdates",
            resp=_response(),
            payload=payload,
        )
    assert exc.value.retry_after == 1.0


def test_parse_envelope_conflict_outside_window_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        client_api,
        "_process_start",
        time.monotonic() - client_api._STARTUP_CONFLICT_WINDOW_S - 1,
    )
    client = HttpBotClient("token", http_client=httpx.AsyncClient())
    payload = {
        "ok": False,
        "error_code": 409,
        "description": _conflict_description(),
    }
    assert (
        client._parse_telegram_envelope(
            method="getUpdates",
            resp=_response(),
            payload=payload,
        )
        is None
    )
