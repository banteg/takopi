import httpx
import pytest

from takopi.logging import setup_logging
from takopi.telegram import (
    TelegramClient,
    make_workspace_keyboard,
    parse_workspace_callback,
)


@pytest.mark.anyio
async def test_telegram_429_no_retry() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            429,
            json={
                "ok": False,
                "description": "retry",
                "parameters": {"retry_after": 3},
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
    assert len(calls) == 1


@pytest.mark.anyio
async def test_http_error_returns_none_on_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops", request=request)

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(transport=transport)
    try:
        tg = TelegramClient("123:abcDEF_ghij", client=client)
        result = await tg._post("getUpdates", {"timeout": 1})
        assert result is None
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_telegram_empty_token_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        TelegramClient("")


@pytest.mark.anyio
async def test_close_owned_client() -> None:
    client = TelegramClient("123:abc")
    await client.close()


@pytest.mark.anyio
async def test_close_external_client() -> None:
    async with httpx.AsyncClient() as ext:
        client = TelegramClient("123:abc", client=ext)
        await client.close()


@pytest.mark.anyio
async def test_send_message_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 123}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(chat_id=456, text="hello")
        assert result == {"message_id": 123}


@pytest.mark.anyio
async def test_send_message_with_reply_markup() -> None:
    captured: dict | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        import json

        captured = json.loads(request.content)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 123}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        await tg.send_message(
            chat_id=456,
            text="hello",
            reply_markup={"inline_keyboard": [[{"text": "btn", "callback_data": "x"}]]},
        )
        assert captured is not None
        assert "reply_markup" in captured


@pytest.mark.anyio
async def test_edit_message_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 123}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.edit_message_text(chat_id=456, message_id=789, text="edited")
        assert result == {"message_id": 123}


@pytest.mark.anyio
async def test_delete_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": True},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.delete_message(chat_id=456, message_id=789)
        assert result is True


@pytest.mark.anyio
async def test_answer_callback_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": True},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        await tg.answer_callback_query("query123")


@pytest.mark.anyio
async def test_answer_callback_query_with_text() -> None:
    captured: dict | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        import json

        captured = json.loads(request.content)
        return httpx.Response(
            200,
            json={"ok": True, "result": True},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        await tg.answer_callback_query("query123", text="Done!")
        assert captured is not None
        assert captured.get("text") == "Done!"


@pytest.mark.anyio
async def test_get_updates_returns_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
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
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.get_updates(offset=0, timeout_s=1)
        assert result is not None
        assert len(result) == 2
        assert result[0]["update_id"] == 1


@pytest.mark.anyio
async def test_get_updates_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": []},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.get_updates(offset=0, timeout_s=1)
        assert result == []


@pytest.mark.anyio
async def test_api_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: chat not found",
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(chat_id=456, text="hello")
        assert result is None


@pytest.mark.anyio
async def test_http_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(chat_id=456, text="hello")
        assert result is None


@pytest.mark.anyio
async def test_5xx_returns_none() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500, text="Error", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(chat_id=456, text="hello")
        assert result is None
        assert len(attempts) == 1


@pytest.mark.anyio
async def test_4xx_api_error_returns_none() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(
            200,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request",
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(chat_id=456, text="hello")
        assert result is None
        assert len(attempts) == 1


class TestMakeWorkspaceKeyboard:
    def test_single_workspace(self) -> None:
        keyboard = make_workspace_keyboard(["myproject"])
        assert keyboard == {
            "inline_keyboard": [
                [{"text": "myproject", "callback_data": "ws:myproject"}]
            ]
        }

    def test_two_workspaces_same_row(self) -> None:
        keyboard = make_workspace_keyboard(["p1", "p2"])
        assert keyboard == {
            "inline_keyboard": [
                [
                    {"text": "p1", "callback_data": "ws:p1"},
                    {"text": "p2", "callback_data": "ws:p2"},
                ]
            ]
        }

    def test_three_workspaces_wraps(self) -> None:
        keyboard = make_workspace_keyboard(["p1", "p2", "p3"])
        assert keyboard == {
            "inline_keyboard": [
                [
                    {"text": "p1", "callback_data": "ws:p1"},
                    {"text": "p2", "callback_data": "ws:p2"},
                ],
                [{"text": "p3", "callback_data": "ws:p3"}],
            ]
        }

    def test_custom_columns(self) -> None:
        keyboard = make_workspace_keyboard(["a", "b", "c", "d"], columns=3)
        assert keyboard == {
            "inline_keyboard": [
                [
                    {"text": "a", "callback_data": "ws:a"},
                    {"text": "b", "callback_data": "ws:b"},
                    {"text": "c", "callback_data": "ws:c"},
                ],
                [{"text": "d", "callback_data": "ws:d"}],
            ]
        }

    def test_empty_list(self) -> None:
        keyboard = make_workspace_keyboard([])
        assert keyboard == {"inline_keyboard": []}


class TestParseWorkspaceCallback:
    def test_valid_callback(self) -> None:
        result = parse_workspace_callback("ws:myproject")
        assert result == "myproject"

    def test_invalid_prefix(self) -> None:
        result = parse_workspace_callback("other:myproject")
        assert result is None

    def test_no_prefix(self) -> None:
        result = parse_workspace_callback("myproject")
        assert result is None

    def test_empty_name(self) -> None:
        result = parse_workspace_callback("ws:")
        assert result == ""
