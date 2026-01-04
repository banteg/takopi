import logging

import httpx
import pytest

from takopi.logging import RedactTokenFilter
from takopi.telegram import TelegramClient


@pytest.mark.anyio
async def test_telegram_429_with_retry() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "description": "retry",
                    "parameters": {"retry_after": 0.1},
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=request,
        )

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", client=client)
        result = await tg._post("sendMessage", {"chat_id": 1, "text": "hi"})
    finally:
        await client.aclose()

    assert result == {"message_id": 1}
    assert len(calls) == 2


@pytest.mark.anyio
async def test_telegram_429_max_retries() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request):
        calls.append(1)
        return httpx.Response(
            429,
            json={
                "ok": False,
                "description": "retry",
                "parameters": {"retry_after": 0.05},
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", client=client)
        result = await tg._post("sendMessage", {"chat_id": 1, "text": "hi"})
    finally:
        await client.aclose()

    assert result is None
    assert len(calls) == 3


@pytest.mark.anyio
async def test_telegram_network_error_retry() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=request,
        )

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", client=client)
        result = await tg._post("sendMessage", {"chat_id": 1, "text": "hi"})
    finally:
        await client.aclose()

    assert result == {"message_id": 1}
    assert len(calls) == 2


@pytest.mark.anyio
async def test_telegram_network_error_max_retries() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request):
        calls.append(1)
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", client=client)
        result = await tg._post("sendMessage", {"chat_id": 1, "text": "hi"})
    finally:
        await client.aclose()

    assert result is None
    assert len(calls) == 3


@pytest.mark.anyio
async def test_telegram_bad_json_retry() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, text="not json", request=request)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=request,
        )

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", client=client)
        result = await tg._post("sendMessage", {"chat_id": 1, "text": "hi"})
    finally:
        await client.aclose()

    assert result == {"message_id": 1}
    assert len(calls) == 2


@pytest.mark.anyio
async def test_telegram_invalid_payload_response() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(200, json=["not", "a", "dict"], request=request)

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", client=client)
        result = await tg._post("sendMessage", {"chat_id": 1, "text": "hi"})
    finally:
        await client.aclose()

    assert result is None


@pytest.mark.anyio
async def test_telegram_api_error_without_retry() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request):
        calls.append(1)
        return httpx.Response(
            200,
            json={
                "ok": False,
                "description": "bad request",
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", client=client)
        result = await tg._post("sendMessage", {"chat_id": 1, "text": "hi"})
    finally:
        await client.aclose()

    assert result is None
    assert len(calls) == 3


@pytest.mark.anyio
async def test_no_token_in_logs_on_http_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "123:abcDEF_ghij"
    redactor = RedactTokenFilter()
    root_logger = logging.getLogger()
    root_logger.addFilter(redactor)

    def handler(request: httpx.Request):
        return httpx.Response(500, text="oops", request=request)

    transport = httpx.MockTransport(handler)

    caplog.set_level(logging.ERROR)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient(token, client=client)
        await tg._post("getUpdates", {"timeout": 1})
    finally:
        await client.aclose()

    root_logger.removeFilter(redactor)

    assert token not in caplog.text
    assert "bot[REDACTED]" in caplog.text


@pytest.mark.anyio
async def test_send_message() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 123, "text": "test"}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(456, "test message")
    finally:
        await client.aclose()

    assert result == {"message_id": 123, "text": "test"}


@pytest.mark.anyio
async def test_send_message_with_options() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(
            456,
            "test",
            reply_to_message_id=789,
            disable_notification=True,
            parse_mode="Markdown",
        )
    finally:
        await client.aclose()

    assert result == {"message_id": 1}


@pytest.mark.anyio
async def test_send_message_with_entities() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(
            456,
            "test",
            entities=[{"type": "bold", "offset": 0, "length": 4}],
        )
    finally:
        await client.aclose()

    assert result == {"message_id": 1}


@pytest.mark.anyio
async def test_edit_message_text() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1, "text": "updated"}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.edit_message_text(456, 789, "new text")
    finally:
        await client.aclose()

    assert result == {"message_id": 1, "text": "updated"}


@pytest.mark.anyio
async def test_edit_message_text_with_entities() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.edit_message_text(
            456,
            789,
            "new text",
            entities=[{"type": "bold", "offset": 0, "length": 4}],
            parse_mode="Markdown",
        )
    finally:
        await client.aclose()

    assert result == {"message_id": 1}


@pytest.mark.anyio
async def test_delete_message() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"ok": True, "result": True}, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.delete_message(456, 789)
    finally:
        await client.aclose()

    assert result is True


@pytest.mark.anyio
async def test_delete_message_failure() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"ok": False}, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.delete_message(456, 789)
    finally:
        await client.aclose()

    assert result is False


@pytest.mark.anyio
async def test_set_my_commands() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"ok": True, "result": True}, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.set_my_commands(
            [{"command": "start", "description": "Start"}]
        )
    finally:
        await client.aclose()

    assert result is True


@pytest.mark.anyio
async def test_set_my_commands_with_scope_and_language() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"ok": True, "result": True}, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.set_my_commands(
            [{"command": "start", "description": "Start"}],
            scope={"type": "default"},
            language_code="en",
        )
    finally:
        await client.aclose()

    assert result is True


@pytest.mark.anyio
async def test_get_me() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": 123456789,
                    "is_bot": True,
                    "first_name": "Test Bot",
                },
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.get_me()
    finally:
        await client.aclose()

    assert result is not None
    assert result["id"] == 123456789
    assert result["first_name"] == "Test Bot"


@pytest.mark.anyio
async def test_get_updates() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"text": "hello"}},
                    {"update_id": 2, "message": {"text": "world"}},
                ],
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.get_updates(offset=10, timeout_s=30)
    finally:
        await client.aclose()

    assert result is not None
    assert len(result) == 2
    assert result[0]["update_id"] == 1


@pytest.mark.anyio
async def test_get_updates_with_allowed_updates() -> None:
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"ok": True, "result": []}, request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.get_updates(offset=10, allowed_updates=["message"])
    finally:
        await client.aclose()

    assert result == []


@pytest.mark.anyio
async def test_client_close_owned() -> None:
    tg = TelegramClient("123:abc")
    assert tg._owns_client is True
    await tg.close()


@pytest.mark.anyio
async def test_client_close_not_owned() -> None:
    client = httpx.AsyncClient()
    tg = TelegramClient("123:abc", client=client)
    tg._owns_client = False
    await tg.close()
    assert not client.is_closed


@pytest.mark.anyio
async def test_empty_token_raises_error() -> None:
    with pytest.raises(ValueError, match="Telegram token is empty"):
        TelegramClient("")
