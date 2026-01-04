import logging

import httpx
import pytest

from takopi.logging import RedactTokenFilter
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
async def test_no_token_in_logs_on_http_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "123:abcDEF_ghij"
    redactor = RedactTokenFilter()
    root_logger = logging.getLogger()
    root_logger.addFilter(redactor)

    def handler(request: httpx.Request) -> httpx.Response:
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
async def test_telegram_empty_token_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        TelegramClient("")


@pytest.mark.anyio
async def test_close_owned_client() -> None:
    client = TelegramClient("123:abc")
    await client.close()


@pytest.mark.anyio
async def test_close_external_client() -> None:
    external = httpx.AsyncClient()
    client = TelegramClient("123:abc", client=external)
    await client.close()
    await external.aclose()


@pytest.mark.anyio
async def test_get_updates_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": [{"update_id": 1}]},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.get_updates(offset=None)

    assert result == [{"update_id": 1}]


@pytest.mark.anyio
async def test_get_updates_with_params() -> None:
    captured_params: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        captured_params.append(body)
        return httpx.Response(
            200,
            json={"ok": True, "result": []},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        await tg.get_updates(offset=100, timeout_s=30, allowed_updates=["message"])

    assert captured_params[0]["offset"] == 100
    assert captured_params[0]["timeout"] == 30
    assert captured_params[0]["allowed_updates"] == ["message"]


@pytest.mark.anyio
async def test_send_message_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 42}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(chat_id=123, text="hello")

    assert result == {"message_id": 42}


@pytest.mark.anyio
async def test_send_message_with_all_params() -> None:
    captured_params: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        captured_params.append(body)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        await tg.send_message(
            chat_id=123,
            text="test",
            reply_to_message_id=456,
            disable_notification=True,
            entities=[{"type": "bold", "offset": 0, "length": 4}],
            parse_mode="HTML",
            reply_markup={"inline_keyboard": []},
        )

    params = captured_params[0]
    assert params["reply_to_message_id"] == 456
    assert params["disable_notification"] is True
    assert params["entities"] == [{"type": "bold", "offset": 0, "length": 4}]
    assert params["parse_mode"] == "HTML"
    assert params["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.anyio
async def test_edit_message_text_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 42}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.edit_message_text(chat_id=123, message_id=42, text="updated")

    assert result == {"message_id": 42}


@pytest.mark.anyio
async def test_delete_message_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": True},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.delete_message(chat_id=123, message_id=42)

    assert result is True


@pytest.mark.anyio
async def test_set_my_commands_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": True},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.set_my_commands(
            [{"command": "start", "description": "Start"}]
        )

    assert result is True


@pytest.mark.anyio
async def test_get_me_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"id": 123, "is_bot": True, "username": "test"},
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.get_me()

    assert result == {"id": 123, "is_bot": True, "username": "test"}


@pytest.mark.anyio
async def test_answer_callback_query_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": True},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.answer_callback_query("query123", text="Done!")

    assert result is True


@pytest.mark.anyio
async def test_post_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection failed")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(chat_id=123, text="test")

    assert result is None


@pytest.mark.anyio
async def test_post_invalid_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(chat_id=123, text="test")

    assert result is None


@pytest.mark.anyio
async def test_post_non_dict_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["array", "not", "dict"], request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(chat_id=123, text="test")

    assert result is None


@pytest.mark.anyio
async def test_post_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": False, "description": "Bad Request"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tg = TelegramClient("123:abc", client=client)
        result = await tg.send_message(chat_id=123, text="test")

    assert result is None


class TestMakeWorkspaceKeyboard:
    def test_single_workspace(self) -> None:
        keyboard = make_workspace_keyboard(["project1"])
        assert keyboard == {
            "inline_keyboard": [[{"text": "project1", "callback_data": "ws:project1"}]]
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
