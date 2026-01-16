import httpx
import pytest

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
