from __future__ import annotations

from typing import Any, Protocol

import httpx

from .logging import get_logger

logger = get_logger(__name__)


class BotClient(Protocol):
    async def close(self) -> None: ...

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[dict] | None: ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
    ) -> dict | None: ...

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
    ) -> dict | None: ...

    async def delete_message(self, chat_id: int, message_id: int) -> bool: ...

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool: ...

    async def get_me(self) -> dict | None: ...


class TelegramClient:
    def __init__(
        self,
        token: str,
        timeout_s: float = 120,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token:
            raise ValueError("Telegram token is empty")
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(self, method: str, json_data: dict[str, Any]) -> Any | None:
        logger.debug("telegram.request", method=method, payload=json_data)
        try:
            resp = await self._client.post(f"{self._base}/{method}", json=json_data)
        except httpx.HTTPError as e:
            url = getattr(e.request, "url", None)
            logger.error(
                "telegram.network_error",
                method=method,
                url=str(url) if url is not None else None,
                error=str(e),
                error_type=e.__class__.__name__,
            )
            return None

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = resp.text
            logger.error(
                "telegram.http_error",
                method=method,
                status=resp.status_code,
                url=str(resp.request.url),
                error=str(e),
                body=body,
            )
            return None

        try:
            payload = resp.json()
        except Exception as e:
            body = resp.text
            logger.error(
                "telegram.bad_response",
                method=method,
                status=resp.status_code,
                url=str(resp.request.url),
                error=str(e),
                error_type=e.__class__.__name__,
                body=body,
            )
            return None

        if not isinstance(payload, dict):
            logger.error(
                "telegram.invalid_payload",
                method=method,
                url=str(resp.request.url),
                payload=payload,
            )
            return None

        if not payload.get("ok"):
            logger.error(
                "telegram.api_error",
                method=method,
                url=str(resp.request.url),
                payload=payload,
            )
            return None

        logger.debug("telegram.response", method=method, payload=payload)
        return payload.get("result")

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[dict] | None:
        params: dict[str, Any] = {"timeout": timeout_s}
        if offset is not None:
            params["offset"] = offset
        if allowed_updates is not None:
            params["allowed_updates"] = allowed_updates
        return await self._post("getUpdates", params)  # type: ignore[return-value]

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
    ) -> dict | None:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if disable_notification is not None:
            params["disable_notification"] = disable_notification
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id
        if entities is not None:
            params["entities"] = entities
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        return await self._post("sendMessage", params)  # type: ignore[return-value]

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
    ) -> dict | None:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if entities is not None:
            params["entities"] = entities
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        return await self._post("editMessageText", params)  # type: ignore[return-value]

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        res = await self._post(
            "deleteMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id,
            },
        )
        return bool(res)

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool:
        params: dict[str, Any] = {"commands": commands}
        if scope is not None:
            params["scope"] = scope
        if language_code is not None:
            params["language_code"] = language_code
        res = await self._post("setMyCommands", params)
        return bool(res)

    async def get_me(self) -> dict | None:
        res = await self._post("getMe", {})
        return res if isinstance(res, dict) else None
