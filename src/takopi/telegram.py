from __future__ import annotations

import enum
import re
import time
from typing import Any, Awaitable, Callable, Protocol

import httpx

import anyio

from .logging import get_logger
from .transports import KeyedRateLimiter, PumpRequest, RequestPump, RetryAfter

logger = get_logger(__name__)


class TelegramPriority(enum.IntEnum):
    HIGH = 0
    LOW = 1


class TelegramRetryAfter(RetryAfter):
    pass


def is_group_chat_id(chat_id: int) -> bool:
    return chat_id < 0


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
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
        not_before: float | None = None,
    ) -> dict | None: ...

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
        not_before: float | None = None,
        wait: bool = True,
    ) -> dict | None: ...

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
    ) -> bool: ...

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool: ...

    async def get_me(
        self, *, priority: TelegramPriority = TelegramPriority.HIGH
    ) -> dict | None: ...


_RETRY_AFTER_RE = re.compile(r"retry after (\d+)", re.IGNORECASE)


def _retry_after_from_payload(payload: dict[str, Any]) -> float | None:
    params = payload.get("parameters")
    if isinstance(params, dict):
        retry_after = params.get("retry_after")
        if isinstance(retry_after, (int, float)):
            return float(retry_after)
    description = payload.get("description")
    if isinstance(description, str):
        return _retry_after_from_description(description)
    return None


def _retry_after_from_description(description: str) -> float | None:
    match = _RETRY_AFTER_RE.search(description)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _retry_after_from_response(resp: httpx.Response) -> float | None:
    try:
        payload = resp.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        retry_after = _retry_after_from_payload(payload)
        if retry_after is not None:
            return retry_after
    return _retry_after_from_description(resp.text)


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
            if resp.status_code == 429:
                retry_after = _retry_after_from_response(resp)
                if retry_after is not None:
                    logger.info(
                        "telegram.rate_limited",
                        method=method,
                        status=resp.status_code,
                        url=str(resp.request.url),
                        retry_after=retry_after,
                    )
                    raise TelegramRetryAfter(retry_after) from e
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
            retry_after = _retry_after_from_payload(payload)
            if retry_after is not None:
                logger.info(
                    "telegram.rate_limited",
                    method=method,
                    url=str(resp.request.url),
                    retry_after=retry_after,
                )
                raise TelegramRetryAfter(retry_after)
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
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
        not_before: float | None = None,
    ) -> dict | None:
        _ = priority
        _ = not_before
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
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
        not_before: float | None = None,
        wait: bool = True,
    ) -> dict | None:
        _ = priority
        _ = not_before
        _ = wait
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

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
    ) -> bool:
        _ = priority
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
        priority: TelegramPriority = TelegramPriority.HIGH,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool:
        _ = priority
        params: dict[str, Any] = {"commands": commands}
        if scope is not None:
            params["scope"] = scope
        if language_code is not None:
            params["language_code"] = language_code
        res = await self._post("setMyCommands", params)
        return bool(res)

    async def get_me(
        self, *, priority: TelegramPriority = TelegramPriority.HIGH
    ) -> dict | None:
        _ = priority
        res = await self._post("getMe", {})
        return res if isinstance(res, dict) else None


class QueuedTelegramClient:
    def __init__(
        self,
        client: BotClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        private_chat_rps: float = 1.0,
        group_chat_rps: float = 20.0 / 60.0,
    ) -> None:
        self._client = client
        self._private_interval = (
            0.0 if private_chat_rps <= 0 else 1.0 / private_chat_rps
        )
        self._group_interval = 0.0 if group_chat_rps <= 0 else 1.0 / group_chat_rps

        def interval_for_chat(chat_id: int) -> float:
            return (
                self._group_interval
                if is_group_chat_id(chat_id)
                else self._private_interval
            )

        self._limiter = KeyedRateLimiter(
            interval_for_key=interval_for_chat,
            clock=clock,
            sleep=sleep,
        )
        self._pump = RequestPump(
            limiter=self._limiter,
            priorities=[TelegramPriority.HIGH, TelegramPriority.LOW],
            clock=clock,
            on_error=self._log_request_error,
            on_pump_error=self._log_pump_failure,
        )

    def _log_request_error(self, request: PumpRequest, exc: Exception) -> None:
        logger.error(
            "telegram.pump.request_failed",
            method=request.label,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )

    def _log_pump_failure(self, exc: Exception) -> None:
        logger.error(
            "telegram.pump.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )

    async def _drop_pending_edits(self, *, chat_id: int, message_id: int) -> None:
        await self._pump.drop_pending(coalesce_key=("edit", chat_id, message_id))

    async def _enqueue(
        self,
        *,
        label: str,
        execute: Callable[[], Awaitable[Any]],
        priority: TelegramPriority,
        chat_id: int | None,
        not_before: float | None,
        coalesce_key: tuple[Any, ...] | None,
        wait: bool = True,
    ) -> Any:
        request = PumpRequest(
            execute=execute,
            priority=int(priority),
            scope=chat_id,
            not_before=not_before,
            coalesce_key=coalesce_key,
            label=label,
        )
        return await self._pump.enqueue(request, wait=wait)

    async def close(self) -> None:
        await self._pump.close()
        await self._client.close()

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[dict] | None:
        return await self._client.get_updates(
            offset=offset, timeout_s=timeout_s, allowed_updates=allowed_updates
        )

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
        not_before: float | None = None,
    ) -> dict | None:
        async def execute() -> dict | None:
            return await self._client.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
                disable_notification=disable_notification,
                entities=entities,
                parse_mode=parse_mode,
                priority=priority,
                not_before=not_before,
            )

        return await self._enqueue(
            label="send_message",
            execute=execute,
            priority=priority,
            chat_id=chat_id,
            not_before=not_before,
            coalesce_key=None,
        )

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
        not_before: float | None = None,
        wait: bool = True,
    ) -> dict | None:
        if priority != TelegramPriority.LOW:
            await self._drop_pending_edits(chat_id=chat_id, message_id=message_id)
        coalesce_key = (
            ("edit", chat_id, message_id) if priority == TelegramPriority.LOW else None
        )

        async def execute() -> dict | None:
            return await self._client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                entities=entities,
                parse_mode=parse_mode,
                priority=priority,
                not_before=not_before,
            )

        return await self._enqueue(
            label="edit_message_text",
            execute=execute,
            priority=priority,
            chat_id=chat_id,
            not_before=not_before,
            coalesce_key=coalesce_key,
            wait=wait,
        )

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
    ) -> bool:
        await self._drop_pending_edits(chat_id=chat_id, message_id=message_id)

        async def execute() -> bool:
            return await self._client.delete_message(
                chat_id=chat_id,
                message_id=message_id,
                priority=priority,
            )

        return bool(
            await self._enqueue(
                label="delete_message",
                execute=execute,
                priority=priority,
                chat_id=chat_id,
                not_before=None,
                coalesce_key=None,
            )
        )

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool:
        async def execute() -> bool:
            return await self._client.set_my_commands(
                commands,
                priority=priority,
                scope=scope,
                language_code=language_code,
            )

        return bool(
            await self._enqueue(
                label="set_my_commands",
                execute=execute,
                priority=priority,
                chat_id=None,
                not_before=None,
                coalesce_key=None,
            )
        )

    async def get_me(
        self, *, priority: TelegramPriority = TelegramPriority.HIGH
    ) -> dict | None:
        async def execute() -> dict | None:
            return await self._client.get_me(priority=priority)

        return await self._enqueue(
            label="get_me",
            execute=execute,
            priority=priority,
            chat_id=None,
            not_before=None,
            coalesce_key=None,
        )
