from __future__ import annotations

import enum
import itertools
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Hashable, Protocol, TYPE_CHECKING

import httpx

import anyio

from .logging import get_logger
from .transports import RetryAfter

logger = get_logger(__name__)


class TelegramPriority(enum.IntEnum):
    HIGH = 0
    LOW = 1


_SEND_PRIORITY = 0
_DELETE_PRIORITY = 1
_EDIT_PRIORITY = 2


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
        replace_message_id: int | None = None,
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


if TYPE_CHECKING:
    from anyio.abc import TaskGroup
else:
    TaskGroup = object


@dataclass(slots=True)
class OutboxOp:
    execute: Callable[[], Awaitable[Any]]
    priority: int
    updated_at: float
    chat_id: int | None
    label: str | None = None
    done: anyio.Event = field(default_factory=anyio.Event)
    result: Any = None

    def set_result(self, result: Any) -> None:
        if self.done.is_set():
            return
        self.result = result
        self.done.set()


class TelegramOutbox:
    def __init__(
        self,
        *,
        interval_for_chat: Callable[[int | None], float],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        on_error: Callable[[OutboxOp, Exception], None] | None = None,
        on_outbox_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._interval_for_chat = interval_for_chat
        self._clock = clock
        self._sleep = sleep
        self._on_error = on_error
        self._on_outbox_error = on_outbox_error
        self._pending: dict[Hashable, OutboxOp] = {}
        self._cond = anyio.Condition()
        self._start_lock = anyio.Lock()
        self._closed = False
        self._tg: TaskGroup | None = None
        self._next_at = 0.0
        self._retry_at = 0.0

    async def _ensure_worker(self) -> None:
        async with self._start_lock:
            if self._tg is not None:
                return
            self._tg = await anyio.create_task_group().__aenter__()
            self._tg.start_soon(self._run)

    async def enqueue(self, *, key: Hashable, op: OutboxOp, wait: bool = True) -> Any:
        await self._ensure_worker()
        async with self._cond:
            if self._closed:
                op.set_result(None)
                return op.result
            previous = self._pending.get(key)
            if previous is not None:
                previous.set_result(None)
            self._pending[key] = op
            self._cond.notify()
        if not wait:
            return None
        await op.done.wait()
        return op.result

    async def drop_pending(self, *, key: Hashable) -> None:
        async with self._cond:
            pending = self._pending.pop(key, None)
            if pending is not None:
                pending.set_result(None)
            self._cond.notify()

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            self._fail_pending()
            self._cond.notify_all()
        if self._tg is not None:
            await self._tg.__aexit__(None, None, None)
            self._tg = None

    def _fail_pending(self) -> None:
        for pending in list(self._pending.values()):
            pending.set_result(None)
        self._pending.clear()

    def _pick_locked(self) -> tuple[Hashable, OutboxOp] | None:
        if not self._pending:
            return None
        return min(
            self._pending.items(),
            key=lambda item: (item[1].priority, item[1].updated_at),
        )

    async def _execute(self, op: OutboxOp) -> Any:
        try:
            return await op.execute()
        except Exception as exc:
            if isinstance(exc, RetryAfter):
                raise
            if self._on_error is not None:
                self._on_error(op, exc)
            return None

    async def _sleep_until(self, deadline: float) -> None:
        delay = deadline - self._clock()
        if delay > 0:
            await self._sleep(delay)

    async def _run(self) -> None:
        cancel_exc = anyio.get_cancelled_exc_class()
        try:
            while True:
                async with self._cond:
                    while not self._pending and not self._closed:
                        await self._cond.wait()
                    if self._closed and not self._pending:
                        return
                blocked_until = max(self._next_at, self._retry_at)
                if self._clock() < blocked_until:
                    await self._sleep_until(blocked_until)
                    continue
                async with self._cond:
                    if self._closed and not self._pending:
                        return
                    picked = self._pick_locked()
                    if picked is None:
                        continue
                    key, op = picked
                    self._pending.pop(key, None)
                started_at = self._clock()
                try:
                    result = await self._execute(op)
                except RetryAfter as exc:
                    self._retry_at = max(
                        self._retry_at, self._clock() + exc.retry_after
                    )
                    async with self._cond:
                        if self._closed:
                            op.set_result(None)
                        elif key not in self._pending:
                            self._pending[key] = op
                            self._cond.notify()
                        else:
                            op.set_result(None)
                    continue
                self._next_at = started_at + self._interval_for_chat(op.chat_id)
                op.set_result(result)
        except cancel_exc:
            return
        except Exception as exc:
            async with self._cond:
                self._closed = True
                self._fail_pending()
                self._cond.notify_all()
            if self._on_outbox_error is not None:
                self._on_outbox_error(exc)
            return


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
        replace_message_id: int | None = None,
    ) -> dict | None:
        _ = priority
        _ = not_before
        _ = replace_message_id
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
        self._clock = clock
        self._sleep = sleep
        self._private_interval = (
            0.0 if private_chat_rps <= 0 else 1.0 / private_chat_rps
        )
        self._group_interval = 0.0 if group_chat_rps <= 0 else 1.0 / group_chat_rps
        self._outbox = TelegramOutbox(
            interval_for_chat=self._interval_for_chat,
            clock=clock,
            sleep=sleep,
            on_error=self._log_request_error,
            on_outbox_error=self._log_outbox_failure,
        )
        self._seq = itertools.count()

    def _interval_for_chat(self, chat_id: int | None) -> float:
        if chat_id is None:
            return self._private_interval
        if is_group_chat_id(chat_id):
            return self._group_interval
        return self._private_interval

    def _log_request_error(self, request: OutboxOp, exc: Exception) -> None:
        logger.error(
            "telegram.outbox.request_failed",
            method=request.label,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )

    def _log_outbox_failure(self, exc: Exception) -> None:
        logger.error(
            "telegram.outbox.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )

    async def _drop_pending_edits(self, *, chat_id: int, message_id: int) -> None:
        _ = chat_id
        await self._outbox.drop_pending(key=("edit", message_id))

    def _unique_key(self, prefix: str) -> tuple[str, int]:
        return (prefix, next(self._seq))

    async def _enqueue(
        self,
        *,
        key: Hashable,
        label: str,
        execute: Callable[[], Awaitable[Any]],
        priority: int,
        chat_id: int | None,
        wait: bool = True,
    ) -> Any:
        request = OutboxOp(
            execute=execute,
            priority=priority,
            updated_at=self._clock(),
            chat_id=chat_id,
            label=label,
        )
        return await self._outbox.enqueue(key=key, op=request, wait=wait)

    async def close(self) -> None:
        await self._outbox.close()
        await self._client.close()

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[dict] | None:
        while True:
            try:
                return await self._client.get_updates(
                    offset=offset, timeout_s=timeout_s, allowed_updates=allowed_updates
                )
            except TelegramRetryAfter as exc:
                await self._sleep(exc.retry_after)

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
        replace_message_id: int | None = None,
    ) -> dict | None:
        _ = priority
        _ = not_before
        _ = replace_message_id

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
                replace_message_id=replace_message_id,
            )

        if replace_message_id is not None:
            await self._outbox.drop_pending(key=("edit", replace_message_id))
        return await self._enqueue(
            key=(
                ("send", replace_message_id)
                if replace_message_id is not None
                else self._unique_key("send")
            ),
            label="send_message",
            execute=execute,
            priority=_SEND_PRIORITY,
            chat_id=chat_id,
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
        _ = priority
        _ = not_before

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
            key=("edit", message_id),
            label="edit_message_text",
            execute=execute,
            priority=_EDIT_PRIORITY,
            chat_id=chat_id,
            wait=wait,
        )

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
    ) -> bool:
        _ = priority
        await self._drop_pending_edits(chat_id=chat_id, message_id=message_id)

        async def execute() -> bool:
            return await self._client.delete_message(
                chat_id=chat_id,
                message_id=message_id,
                priority=priority,
            )

        return bool(
            await self._enqueue(
                key=("delete", message_id),
                label="delete_message",
                execute=execute,
                priority=_DELETE_PRIORITY,
                chat_id=chat_id,
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
        _ = priority

        async def execute() -> bool:
            return await self._client.set_my_commands(
                commands,
                priority=priority,
                scope=scope,
                language_code=language_code,
            )

        return bool(
            await self._enqueue(
                key=self._unique_key("set_my_commands"),
                label="set_my_commands",
                execute=execute,
                priority=_SEND_PRIORITY,
                chat_id=None,
            )
        )

    async def get_me(
        self, *, priority: TelegramPriority = TelegramPriority.HIGH
    ) -> dict | None:
        _ = priority

        async def execute() -> dict | None:
            return await self._client.get_me(priority=priority)

        return await self._enqueue(
            key=self._unique_key("get_me"),
            label="get_me",
            execute=execute,
            priority=_SEND_PRIORITY,
            chat_id=None,
        )
