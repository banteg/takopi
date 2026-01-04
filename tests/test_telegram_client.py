import logging

import httpx
import pytest
import structlog

from takopi.logging import redact_token_processor, setup_logging
from takopi.telegram import TelegramClient


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


def test_redact_token_processor():
    event_dict = {"event": "error bot123:abcDEF_ghij message"}
    result = redact_token_processor(None, None, event_dict)
    assert result["event"] == "error bot[REDACTED] message"
    assert "123:abcDEF_ghij" not in result["event"]


def test_redact_bare_token_processor():
    event_dict = {"event": "error 123:ABCdefGHIjklMNOpqrsTUVwxyz message"}
    result = redact_token_processor(None, None, event_dict)
    assert result["event"] == "error [REDACTED_TOKEN] message"
    assert "123:ABCdefGHIjklMNOpqrsTUVwxyz" not in result["event"]
