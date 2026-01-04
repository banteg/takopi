from __future__ import annotations

import enum
import random
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

import httpx

import anyio

from .logging import get_logger

logger = get_logger(__name__)


if TYPE_CHECKING:
    from anyio.abc import TaskGroup
else:
    TaskGroup = object


class TelegramPriority(enum.IntEnum):
    HIGH = 0
    NORMAL = 1
    LOW = 2


class TelegramRetryAfter(Exception):
    def __init__(self, retry_after: float, description: str | None = None) -> None:
        super().__init__(description or f"retry after {retry_after}")
        self.retry_after = float(retry_after)
        self.description = description


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
        priority: TelegramPriority = TelegramPriority.NORMAL,
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
        priority: TelegramPriority = TelegramPriority.NORMAL,
        not_before: float | None = None,
    ) -> dict | None: ...

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        priority: TelegramPriority = TelegramPriority.NORMAL,
    ) -> bool: ...

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        priority: TelegramPriority = TelegramPriority.NORMAL,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool: ...

    async def get_me(
        self, *, priority: TelegramPriority = TelegramPriority.NORMAL
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
        priority: TelegramPriority = TelegramPriority.NORMAL,
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
        priority: TelegramPriority = TelegramPriority.NORMAL,
        not_before: float | None = None,
    ) -> dict | None:
        _ = priority
        _ = not_before
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
        priority: TelegramPriority = TelegramPriority.NORMAL,
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
        priority: TelegramPriority = TelegramPriority.NORMAL,
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
        self, *, priority: TelegramPriority = TelegramPriority.NORMAL
    ) -> dict | None:
        _ = priority
        res = await self._post("getMe", {})
        return res if isinstance(res, dict) else None


@dataclass(slots=True)
class _QueuedRequest:
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    priority: TelegramPriority
    chat_id: int | None
    not_before: float | None
    coalesce_key: tuple[Any, ...] | None
    done: anyio.Event = field(default_factory=anyio.Event)
    result: Any = None

    def set_result(self, result: Any) -> None:
        if self.done.is_set():
            return
        self.result = result
        self.done.set()


class TelegramRateLimiter:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        private_chat_rps: float = 1.0,
        group_chat_rps: float = 20.0 / 60.0,
    ) -> None:
        self._clock = clock
        self._sleep = sleep

        self._p_interval = 0.0 if private_chat_rps <= 0 else 1.0 / private_chat_rps
        self._gr_interval = 0.0 if group_chat_rps <= 0 else 1.0 / group_chat_rps

        self._lock = anyio.Lock()
        self._chat_next_at: dict[int, float] = defaultdict(float)

    def _chat_interval(self, chat_id: int) -> float:
        # Heuristic: group/supergroup/channel chat IDs are negative.
        return self._gr_interval if is_group_chat_id(chat_id) else self._p_interval

    async def wait_turn(
        self, *, chat_id: int | None, not_before: float | None = None
    ) -> None:
        async with self._lock:
            now = self._clock()
            target = max(now, not_before or now)
            if chat_id is not None:
                target = max(target, self._chat_next_at[chat_id])

            if chat_id is not None:
                self._chat_next_at[chat_id] = target + self._chat_interval(chat_id)

        await self._sleep(max(0.0, target - now))

    async def apply_retry_after(
        self, *, chat_id: int | None, retry_after: float
    ) -> None:
        jitter = random.uniform(0.0, min(0.25, retry_after * 0.25))
        delay = max(0.0, retry_after + jitter)
        async with self._lock:
            now = self._clock()
            until = now + delay
            if chat_id is not None:
                self._chat_next_at[chat_id] = max(self._chat_next_at[chat_id], until)
        await self._sleep(delay)


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
        self._limiter = TelegramRateLimiter(
            clock=clock,
            sleep=sleep,
            private_chat_rps=private_chat_rps,
            group_chat_rps=group_chat_rps,
        )
        self._queues: dict[TelegramPriority, deque[_QueuedRequest]] = {
            TelegramPriority.HIGH: deque(),
            TelegramPriority.NORMAL: deque(),
            TelegramPriority.LOW: deque(),
        }
        self._pending_by_key: dict[tuple[Any, ...], _QueuedRequest] = {}
        self._cond = anyio.Condition()
        self._start_lock = anyio.Lock()
        self._closed = False
        self._tg: TaskGroup | None = None

    async def _ensure_worker(self) -> None:
        async with self._start_lock:
            if self._tg is not None:
                return
            self._tg = await anyio.create_task_group().__aenter__()
            self._tg.start_soon(self._run)

    async def _enqueue(self, request: _QueuedRequest) -> Any:
        await self._ensure_worker()
        async with self._cond:
            if self._closed:
                request.set_result(None)
                return request.result
            if request.method == "edit_message_text" and request.chat_id is not None:
                if request.priority != TelegramPriority.LOW:
                    message_id = request.kwargs.get("message_id")
                    if isinstance(message_id, int):
                        self._drop_pending_edits(
                            chat_id=request.chat_id, message_id=message_id
                        )
            if request.method == "delete_message" and request.chat_id is not None:
                message_id = request.kwargs.get("message_id")
                if isinstance(message_id, int):
                    self._drop_pending_edits(
                        chat_id=request.chat_id, message_id=message_id
                    )
            if request.coalesce_key is not None:
                previous = self._pending_by_key.get(request.coalesce_key)
                if previous is not None:
                    previous.set_result(None)
                self._pending_by_key[request.coalesce_key] = request
            self._queues[request.priority].append(request)
            self._cond.notify()
        await request.done.wait()
        return request.result

    async def _next_request(self) -> _QueuedRequest | None:
        async with self._cond:
            while True:
                if self._closed and all(not queue for queue in self._queues.values()):
                    return None
                for priority in (
                    TelegramPriority.HIGH,
                    TelegramPriority.NORMAL,
                    TelegramPriority.LOW,
                ):
                    queue = self._queues[priority]
                    if queue:
                        return queue.popleft()
                await self._cond.wait()

    async def _execute(self, request: _QueuedRequest) -> Any:
        while True:
            await self._limiter.wait_turn(
                chat_id=request.chat_id, not_before=request.not_before
            )
            try:
                method = getattr(self._client, request.method)
                return await method(*request.args, **request.kwargs)
            except TelegramRetryAfter as exc:
                await self._limiter.apply_retry_after(
                    chat_id=request.chat_id, retry_after=exc.retry_after
                )
                continue
            except Exception as exc:
                logger.error(
                    "telegram.pump.request_failed",
                    method=request.method,
                    error=str(exc),
                    error_type=exc.__class__.__name__,
                )
                return None

    async def _run(self) -> None:
        cancel_exc = anyio.get_cancelled_exc_class()
        try:
            while True:
                request = await self._next_request()
                if request is None:
                    return
                if request.coalesce_key is not None:
                    if self._pending_by_key.get(request.coalesce_key) is not request:
                        continue
                result = await self._execute(request)
                request.set_result(result)
                if request.coalesce_key is not None:
                    if self._pending_by_key.get(request.coalesce_key) is request:
                        self._pending_by_key.pop(request.coalesce_key, None)
        except cancel_exc:
            return
        except Exception as exc:
            logger.exception(
                "telegram.pump.failed",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            self._fail_pending()

    def _fail_pending(self) -> None:
        for queue in self._queues.values():
            while queue:
                queue.popleft().set_result(None)
        for pending in list(self._pending_by_key.values()):
            pending.set_result(None)
        self._pending_by_key.clear()

    def _drop_pending_edits(self, *, chat_id: int, message_id: int) -> None:
        coalesce_key = ("edit", chat_id, message_id)
        pending = self._pending_by_key.pop(coalesce_key, None)
        if pending is not None:
            pending.set_result(None)
        for queue in self._queues.values():
            if not queue:
                continue
            kept: deque[_QueuedRequest] = deque()
            while queue:
                req = queue.popleft()
                if (
                    req.method == "edit_message_text"
                    and req.chat_id == chat_id
                    and req.kwargs.get("message_id") == message_id
                ):
                    req.set_result(None)
                    continue
                kept.append(req)
            queue.extend(kept)

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            self._fail_pending()
            self._cond.notify_all()
        if self._tg is not None:
            await self._tg.__aexit__(None, None, None)
            self._tg = None
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
        priority: TelegramPriority = TelegramPriority.NORMAL,
        not_before: float | None = None,
    ) -> dict | None:
        request = _QueuedRequest(
            method="send_message",
            args=(),
            kwargs={
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "disable_notification": disable_notification,
                "entities": entities,
                "parse_mode": parse_mode,
            },
            priority=priority,
            chat_id=chat_id,
            not_before=not_before,
            coalesce_key=None,
        )
        return await self._enqueue(request)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        *,
        priority: TelegramPriority = TelegramPriority.NORMAL,
        not_before: float | None = None,
    ) -> dict | None:
        coalesce_key = None
        if priority == TelegramPriority.LOW:
            coalesce_key = ("edit", chat_id, message_id)
        request = _QueuedRequest(
            method="edit_message_text",
            args=(),
            kwargs={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "entities": entities,
                "parse_mode": parse_mode,
            },
            priority=priority,
            chat_id=chat_id,
            not_before=not_before,
            coalesce_key=coalesce_key,
        )
        return await self._enqueue(request)

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        priority: TelegramPriority = TelegramPriority.NORMAL,
    ) -> bool:
        request = _QueuedRequest(
            method="delete_message",
            args=(),
            kwargs={"chat_id": chat_id, "message_id": message_id},
            priority=priority,
            chat_id=chat_id,
            not_before=None,
            coalesce_key=None,
        )
        return bool(await self._enqueue(request))

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        priority: TelegramPriority = TelegramPriority.NORMAL,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool:
        request = _QueuedRequest(
            method="set_my_commands",
            args=(),
            kwargs={
                "commands": commands,
                "scope": scope,
                "language_code": language_code,
            },
            priority=priority,
            chat_id=None,
            not_before=None,
            coalesce_key=None,
        )
        return bool(await self._enqueue(request))

    async def get_me(
        self, *, priority: TelegramPriority = TelegramPriority.NORMAL
    ) -> dict | None:
        request = _QueuedRequest(
            method="get_me",
            args=(),
            kwargs={"priority": priority},
            priority=priority,
            chat_id=None,
            not_before=None,
            coalesce_key=None,
        )
        return await self._enqueue(request)
