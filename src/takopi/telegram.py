from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Protocol

import httpx

from .logging import RedactTokenFilter

logger = logging.getLogger(__name__)
logger.addFilter(RedactTokenFilter())

MAX_RETRIES = 3
BASE_RETRY_DELAY = 1.0
JITTER_FACTOR = 0.1


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

    async def _should_retry(self, attempt: int) -> bool:
        return attempt < MAX_RETRIES - 1

    async def _wait_with_backoff(self, delay: float, use_jitter: bool = False) -> None:
        if use_jitter:
            jitter = delay * JITTER_FACTOR * random.random()
            await asyncio.sleep(delay + jitter)
        else:
            await asyncio.sleep(delay)

    async def _parse_response(self, resp: httpx.Response, method: str) -> Any | None:
        try:
            return resp.json()
        except Exception as e:
            body = resp.text
            logger.error(
                "[telegram] bad response method=%s status=%s url=%s: %s body=%r",
                method,
                resp.status_code,
                resp.request.url,
                e,
                body,
            )
            return None

    async def _post(self, method: str, json_data: dict[str, Any]) -> Any | None:
        retry_delay = BASE_RETRY_DELAY

        for attempt in range(MAX_RETRIES):
            logger.debug("[telegram] request %s: %s", method, json_data)

            try:
                resp = await self._client.post(f"{self._base}/{method}", json=json_data)
            except httpx.HTTPError as e:
                url = getattr(e.request, "url", None)
                logger.error(
                    "[telegram] network error method=%s url=%s: %s", method, url, e
                )
                if not await self._should_retry(attempt):
                    return None
                await self._wait_with_backoff(retry_delay, use_jitter=False)
                retry_delay *= 2
                continue

            payload = await self._parse_response(resp, method)
            if payload is None:
                if not await self._should_retry(attempt):
                    return None
                await self._wait_with_backoff(retry_delay, use_jitter=False)
                retry_delay *= 2
                continue

            if not isinstance(payload, dict):
                logger.error(
                    "[telegram] invalid response method=%s url=%s: %r",
                    method,
                    resp.request.url,
                    payload,
                )
                return None

            if not payload.get("ok"):
                logger.error(
                    "[telegram] api error method=%s url=%s: %s",
                    method,
                    resp.request.url,
                    payload,
                )

                if resp.status_code == 429:
                    retry_after = payload.get("parameters", {}).get("retry_after")
                    if retry_after:
                        logger.info(
                            "[telegram] rate limited, retrying after %s seconds",
                            retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                if not await self._should_retry(attempt):
                    return None
                await self._wait_with_backoff(retry_delay, use_jitter=True)
                retry_delay *= 2
                continue

            logger.debug("[telegram] response %s: %s", method, payload)
            return payload.get("result")

        return None

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
