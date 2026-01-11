from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Hashable,
    Iterable,
    Protocol,
    TYPE_CHECKING,
)

import httpx

import anyio

from ..logging import get_logger
from .types import (
    TelegramCallbackQuery,
    TelegramDocument,
    TelegramIncomingMessage,
    TelegramIncomingUpdate,
    TelegramVoice,
)

logger = get_logger(__name__)


SEND_PRIORITY = 0
DELETE_PRIORITY = 1
EDIT_PRIORITY = 2


class RetryAfter(Exception):
    def __init__(self, retry_after: float, description: str | None = None) -> None:
        super().__init__(description or f"retry after {retry_after}")
        self.retry_after = float(retry_after)
        self.description = description


class TelegramRetryAfter(RetryAfter):
    pass


def is_group_chat_id(chat_id: int) -> bool:
    return chat_id < 0


def parse_incoming_update(
    update: dict[str, Any],
    *,
    chat_id: int | None = None,
    chat_ids: set[int] | None = None,
) -> TelegramIncomingUpdate | None:
    msg = update.get("message")
    if isinstance(msg, dict):
        return _parse_incoming_message(msg, chat_id=chat_id, chat_ids=chat_ids)
    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        return _parse_callback_query(
            callback_query,
            chat_id=chat_id,
            chat_ids=chat_ids,
        )
    return None


def _parse_incoming_message(
    msg: dict[str, Any],
    *,
    chat_id: int | None = None,
    chat_ids: set[int] | None = None,
) -> TelegramIncomingMessage | None:
    def _parse_document_payload(payload: dict[str, Any]) -> TelegramDocument | None:
        file_id = payload.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            return None
        return TelegramDocument(
            file_id=file_id,
            file_name=payload.get("file_name")
            if isinstance(payload.get("file_name"), str)
            else None,
            mime_type=payload.get("mime_type")
            if isinstance(payload.get("mime_type"), str)
            else None,
            file_size=payload.get("file_size")
            if isinstance(payload.get("file_size"), int)
            and not isinstance(payload.get("file_size"), bool)
            else None,
            raw=payload,
        )

    raw_text = msg.get("text")
    text = raw_text if isinstance(raw_text, str) else None
    caption = msg.get("caption")
    if text is None and isinstance(caption, str):
        text = caption
    if text is None:
        text = ""
    file_command = False
    if isinstance(text, str):
        stripped = text.lstrip()
        if stripped.startswith("/"):
            token = stripped.split(maxsplit=1)[0]
            file_command = token.startswith("/file")
    voice_payload: TelegramVoice | None = None
    voice = msg.get("voice")
    if isinstance(voice, dict):
        file_id = voice.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            file_id = None
        if file_id is not None:
            voice_payload = TelegramVoice(
                file_id=file_id,
                mime_type=voice.get("mime_type")
                if isinstance(voice.get("mime_type"), str)
                else None,
                file_size=voice.get("file_size")
                if isinstance(voice.get("file_size"), int)
                and not isinstance(voice.get("file_size"), bool)
                else None,
                duration=voice.get("duration")
                if isinstance(voice.get("duration"), int)
                and not isinstance(voice.get("duration"), bool)
                else None,
                raw=voice,
            )
            if not isinstance(raw_text, str) and not isinstance(caption, str):
                text = ""
    document_payload: TelegramDocument | None = None
    document = msg.get("document")
    if isinstance(document, dict):
        document_payload = _parse_document_payload(document)
    if document_payload is None:
        video = msg.get("video")
        if isinstance(video, dict):
            document_payload = _parse_document_payload(video)
    if document_payload is None:
        photo = msg.get("photo")
        if isinstance(photo, list):
            best: dict[str, Any] | None = None
            best_score = -1
            for item in photo:
                if not isinstance(item, dict):
                    continue
                file_id = item.get("file_id")
                if not isinstance(file_id, str) or not file_id:
                    continue
                size = item.get("file_size")
                if isinstance(size, int) and not isinstance(size, bool):
                    score = size
                else:
                    width = item.get("width")
                    height = item.get("height")
                    if isinstance(width, int) and isinstance(height, int):
                        score = width * height
                    else:
                        score = 0
                if score > best_score:
                    best_score = score
                    best = item
            if best is not None:
                document_payload = _parse_document_payload(best)
    if document_payload is None and file_command:
        sticker = msg.get("sticker")
        if isinstance(sticker, dict):
            document_payload = _parse_document_payload(sticker)
    has_text = isinstance(raw_text, str) or isinstance(caption, str)
    if not has_text and voice_payload is None and document_payload is None:
        return None
    chat = msg.get("chat")
    if not isinstance(chat, dict):
        return None
    msg_chat_id = chat.get("id")
    if not isinstance(msg_chat_id, int):
        return None
    chat_type = chat.get("type") if isinstance(chat.get("type"), str) else None
    is_forum = chat.get("is_forum")
    if not isinstance(is_forum, bool):
        is_forum = None
    allowed = chat_ids
    if allowed is None and chat_id is not None:
        allowed = {chat_id}
    if allowed is not None and msg_chat_id not in allowed:
        return None
    message_id = msg.get("message_id")
    if not isinstance(message_id, int):
        return None
    reply = msg.get("reply_to_message")
    reply_to_message_id = None
    reply_to_text = None
    if isinstance(reply, dict):
        reply_to_message_id = (
            reply.get("message_id")
            if isinstance(reply.get("message_id"), int)
            else None
        )
        reply_to_text = (
            reply.get("text") if isinstance(reply.get("text"), str) else None
        )
    sender = msg.get("from")
    sender_id = (
        sender.get("id")
        if isinstance(sender, dict) and isinstance(sender.get("id"), int)
        else None
    )
    media_group_id = msg.get("media_group_id")
    if not isinstance(media_group_id, str):
        media_group_id = None
    thread_id = msg.get("message_thread_id")
    if isinstance(thread_id, bool) or not isinstance(thread_id, int):
        thread_id = None
    is_topic_message = msg.get("is_topic_message")
    if not isinstance(is_topic_message, bool):
        is_topic_message = None
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=msg_chat_id,
        message_id=message_id,
        text=text,
        reply_to_message_id=reply_to_message_id,
        reply_to_text=reply_to_text,
        sender_id=sender_id,
        media_group_id=media_group_id,
        thread_id=thread_id,
        is_topic_message=is_topic_message,
        chat_type=chat_type,
        is_forum=is_forum,
        voice=voice_payload,
        document=document_payload,
        raw=msg,
    )


def _parse_callback_query(
    query: dict[str, Any],
    *,
    chat_id: int | None = None,
    chat_ids: set[int] | None = None,
) -> TelegramCallbackQuery | None:
    callback_id = query.get("id")
    if not isinstance(callback_id, str) or not callback_id:
        return None
    msg = query.get("message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat")
    if not isinstance(chat, dict):
        return None
    msg_chat_id = chat.get("id")
    if not isinstance(msg_chat_id, int):
        return None
    allowed = chat_ids
    if allowed is None and chat_id is not None:
        allowed = {chat_id}
    if allowed is not None and msg_chat_id not in allowed:
        return None
    message_id = msg.get("message_id")
    if not isinstance(message_id, int):
        return None
    data = query.get("data") if isinstance(query.get("data"), str) else None
    sender = query.get("from")
    sender_id = (
        sender.get("id")
        if isinstance(sender, dict) and isinstance(sender.get("id"), int)
        else None
    )
    return TelegramCallbackQuery(
        transport="telegram",
        chat_id=msg_chat_id,
        message_id=message_id,
        callback_query_id=callback_id,
        data=data,
        sender_id=sender_id,
        raw=query,
    )


async def poll_incoming(
    bot: BotClient,
    *,
    chat_id: int | None = None,
    chat_ids: Iterable[int] | Callable[[], Iterable[int]] | None = None,
    offset: int | None = None,
) -> AsyncIterator[TelegramIncomingUpdate]:
    while True:
        updates = await bot.get_updates(
            offset=offset,
            timeout_s=50,
            allowed_updates=["message", "callback_query"],
        )
        if updates is None:
            logger.info("loop.get_updates.failed")
            await anyio.sleep(2)
            continue
        logger.debug("loop.updates", updates=updates)
        resolved_chat_ids = chat_ids() if callable(chat_ids) else chat_ids
        allowed = set(resolved_chat_ids) if resolved_chat_ids is not None else None
        if allowed is None and chat_id is not None:
            allowed = {chat_id}
        for upd in updates:
            offset = upd["update_id"] + 1
            msg = parse_incoming_update(upd, chat_ids=allowed)
            if msg is not None:
                yield msg


class BotClient(Protocol):
    async def close(self) -> None: ...

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[dict] | None: ...

    async def get_file(self, file_id: str) -> dict | None: ...

    async def download_file(self, file_path: str) -> bytes | None: ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        message_thread_id: int | None = None,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        *,
        replace_message_id: int | None = None,
    ) -> dict | None: ...

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        disable_notification: bool | None = False,
        caption: str | None = None,
    ) -> dict | None: ...

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        *,
        wait: bool = True,
    ) -> dict | None: ...

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> bool: ...

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool: ...

    async def get_me(self) -> dict | None: ...

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool | None = None,
    ) -> bool: ...

    async def get_chat(self, chat_id: int) -> dict | None: ...

    async def get_chat_member(self, chat_id: int, user_id: int) -> dict | None: ...

    async def create_forum_topic(
        self,
        chat_id: int,
        name: str,
    ) -> dict | None: ...

    async def edit_forum_topic(
        self,
        chat_id: int,
        message_thread_id: int,
        name: str,
    ) -> bool: ...


if TYPE_CHECKING:
    from anyio.abc import TaskGroup
else:
    TaskGroup = object


@dataclass(slots=True)
class OutboxOp:
    execute: Callable[[], Awaitable[Any]]
    priority: int
    queued_at: float
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
        self.next_at = 0.0
        self.retry_at = 0.0

    async def ensure_worker(self) -> None:
        async with self._start_lock:
            if self._tg is not None or self._closed:
                return
            self._tg = await anyio.create_task_group().__aenter__()
            self._tg.start_soon(self.run)

    async def enqueue(self, *, key: Hashable, op: OutboxOp, wait: bool = True) -> Any:
        await self.ensure_worker()
        async with self._cond:
            if self._closed:
                op.set_result(None)
                return op.result
            previous = self._pending.get(key)
            if previous is not None:
                op.queued_at = previous.queued_at
                previous.set_result(None)
            else:
                op.queued_at = op.updated_at
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
            self.fail_pending()
            self._cond.notify_all()
        if self._tg is not None:
            await self._tg.__aexit__(None, None, None)
            self._tg = None

    def fail_pending(self) -> None:
        for pending in list(self._pending.values()):
            pending.set_result(None)
        self._pending.clear()

    def pick_locked(self) -> tuple[Hashable, OutboxOp] | None:
        if not self._pending:
            return None
        return min(
            self._pending.items(),
            key=lambda item: (item[1].priority, item[1].queued_at),
        )

    async def execute_op(self, op: OutboxOp) -> Any:
        try:
            return await op.execute()
        except Exception as exc:
            if isinstance(exc, RetryAfter):
                raise
            if self._on_error is not None:
                self._on_error(op, exc)
            return None

    async def sleep_until(self, deadline: float) -> None:
        delay = deadline - self._clock()
        if delay > 0:
            await self._sleep(delay)

    async def run(self) -> None:
        cancel_exc = anyio.get_cancelled_exc_class()
        try:
            while True:
                async with self._cond:
                    while not self._pending and not self._closed:
                        await self._cond.wait()
                    if self._closed and not self._pending:
                        return
                blocked_until = max(self.next_at, self.retry_at)
                if self._clock() < blocked_until:
                    await self.sleep_until(blocked_until)
                    continue
                async with self._cond:
                    if self._closed and not self._pending:
                        return
                    picked = self.pick_locked()
                    if picked is None:
                        continue
                    key, op = picked
                    self._pending.pop(key, None)
                started_at = self._clock()
                try:
                    result = await self.execute_op(op)
                except RetryAfter as exc:
                    self.retry_at = max(self.retry_at, self._clock() + exc.retry_after)
                    async with self._cond:
                        if self._closed:
                            op.set_result(None)
                        elif key not in self._pending:
                            self._pending[key] = op
                            self._cond.notify()
                        else:
                            op.set_result(None)
                    continue
                self.next_at = started_at + self._interval_for_chat(op.chat_id)
                op.set_result(result)
        except cancel_exc:
            return
        except Exception as exc:
            async with self._cond:
                self._closed = True
                self.fail_pending()
                self._cond.notify_all()
            if self._on_outbox_error is not None:
                self._on_outbox_error(exc)
            return


def retry_after_from_payload(payload: dict[str, Any]) -> float | None:
    params = payload.get("parameters")
    if isinstance(params, dict):
        retry_after = params.get("retry_after")
        if isinstance(retry_after, (int, float)):
            return float(retry_after)
    return None


class TelegramClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        client: BotClient | None = None,
        timeout_s: float = 120,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        private_chat_rps: float = 1.0,
        group_chat_rps: float = 20.0 / 60.0,
    ) -> None:
        if client is not None:
            if token is not None or http_client is not None:
                raise ValueError("Provide either token or client, not both.")
            self._client_override = client
            self._base = None
            self._file_base = None
            self._http_client = None
            self._owns_http_client = False
        else:
            if token is None or not token:
                raise ValueError("Telegram token is empty")
            self._client_override = None
            self._base = f"https://api.telegram.org/bot{token}"
            self._file_base = f"https://api.telegram.org/file/bot{token}"
            self._http_client = http_client or httpx.AsyncClient(timeout=timeout_s)
            self._owns_http_client = http_client is None
        self._clock = clock
        self._sleep = sleep
        self._private_interval = (
            0.0 if private_chat_rps <= 0 else 1.0 / private_chat_rps
        )
        self._group_interval = 0.0 if group_chat_rps <= 0 else 1.0 / group_chat_rps
        self._outbox = TelegramOutbox(
            interval_for_chat=self.interval_for_chat,
            clock=clock,
            sleep=sleep,
            on_error=self.log_request_error,
            on_outbox_error=self.log_outbox_failure,
        )
        self._seq = itertools.count()

    def interval_for_chat(self, chat_id: int | None) -> float:
        if chat_id is None:
            return self._private_interval
        if is_group_chat_id(chat_id):
            return self._group_interval
        return self._private_interval

    def log_request_error(self, request: OutboxOp, exc: Exception) -> None:
        logger.error(
            "telegram.outbox.request_failed",
            method=request.label,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )

    def log_outbox_failure(self, exc: Exception) -> None:
        logger.error(
            "telegram.outbox.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )

    async def drop_pending_edits(self, *, chat_id: int, message_id: int) -> None:
        await self._outbox.drop_pending(key=("edit", chat_id, message_id))

    def unique_key(self, prefix: str) -> tuple[str, int]:
        return (prefix, next(self._seq))

    async def enqueue_op(
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
            queued_at=0.0,
            updated_at=self._clock(),
            chat_id=chat_id,
            label=label,
        )
        return await self._outbox.enqueue(key=key, op=request, wait=wait)

    async def close(self) -> None:
        await self._outbox.close()
        if self._client_override is not None:
            await self._client_override.close()
            return
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()

    async def _post(self, method: str, json_data: dict[str, Any]) -> Any | None:
        if self._http_client is None or self._base is None:
            raise RuntimeError("TelegramClient is configured without an HTTP client.")
        logger.debug("telegram.request", method=method, payload=json_data)
        try:
            resp = await self._http_client.post(
                f"{self._base}/{method}", json=json_data
            )
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
                retry_after: float | None = None
                try:
                    payload = resp.json()
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    retry_after = retry_after_from_payload(payload)
                retry_after = 5.0 if retry_after is None else retry_after
                logger.warning(
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
            if payload.get("error_code") == 429:
                retry_after = retry_after_from_payload(payload)
                retry_after = 5.0 if retry_after is None else retry_after
                logger.warning(
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

    async def _post_form(
        self,
        method: str,
        data: dict[str, Any],
        files: dict[str, Any],
    ) -> Any | None:
        if self._http_client is None or self._base is None:
            raise RuntimeError("TelegramClient is configured without an HTTP client.")
        logger.debug("telegram.request", method=method, payload=data)
        try:
            resp = await self._http_client.post(
                f"{self._base}/{method}", data=data, files=files
            )
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
                retry_after: float | None = None
                try:
                    payload = resp.json()
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    retry_after = retry_after_from_payload(payload)
                retry_after = 5.0 if retry_after is None else retry_after
                logger.warning(
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
            if payload.get("error_code") == 429:
                retry_after = retry_after_from_payload(payload)
                retry_after = 5.0 if retry_after is None else retry_after
                logger.warning(
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
        while True:
            try:
                if self._client_override is not None:
                    return await self._client_override.get_updates(
                        offset=offset,
                        timeout_s=timeout_s,
                        allowed_updates=allowed_updates,
                    )
                params: dict[str, Any] = {"timeout": timeout_s}
                if offset is not None:
                    params["offset"] = offset
                if allowed_updates is not None:
                    params["allowed_updates"] = allowed_updates
                result = await self._post("getUpdates", params)
                return result if isinstance(result, list) else None
            except TelegramRetryAfter as exc:
                await self._sleep(exc.retry_after)

    async def get_file(self, file_id: str) -> dict | None:
        while True:
            try:
                if self._client_override is not None:
                    return await self._client_override.get_file(file_id)
                result = await self._post("getFile", {"file_id": file_id})
                return result if isinstance(result, dict) else None
            except TelegramRetryAfter as exc:
                await self._sleep(exc.retry_after)

    async def download_file(self, file_path: str) -> bytes | None:
        if self._client_override is not None:
            return await self._client_override.download_file(file_path)
        if self._http_client is None or self._file_base is None:
            raise RuntimeError("TelegramClient is configured without an HTTP client.")
        url = f"{self._file_base}/{file_path}"
        try:
            resp = await self._http_client.get(url)
        except httpx.HTTPError as exc:
            request_url = getattr(exc.request, "url", None)
            logger.error(
                "telegram.file_network_error",
                url=str(request_url) if request_url is not None else None,
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "telegram.file_http_error",
                status=resp.status_code,
                url=str(resp.request.url),
                error=str(exc),
                body=resp.text,
            )
            return None
        return resp.content

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        message_thread_id: int | None = None,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        *,
        replace_message_id: int | None = None,
    ) -> dict | None:
        async def execute() -> dict | None:
            if self._client_override is not None:
                return await self._client_override.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_to_message_id=reply_to_message_id,
                    disable_notification=disable_notification,
                    message_thread_id=message_thread_id,
                    entities=entities,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    replace_message_id=replace_message_id,
                )
            params: dict[str, Any] = {"chat_id": chat_id, "text": text}
            if disable_notification is not None:
                params["disable_notification"] = disable_notification
            if reply_to_message_id is not None:
                params["reply_to_message_id"] = reply_to_message_id
            if message_thread_id is not None:
                params["message_thread_id"] = message_thread_id
            if entities is not None:
                params["entities"] = entities
            if parse_mode is not None:
                params["parse_mode"] = parse_mode
            if reply_markup is not None:
                params["reply_markup"] = reply_markup
            result = await self._post("sendMessage", params)
            return result if isinstance(result, dict) else None

        if replace_message_id is not None:
            await self._outbox.drop_pending(key=("edit", chat_id, replace_message_id))
        result = await self.enqueue_op(
            key=(
                ("send", chat_id, replace_message_id)
                if replace_message_id is not None
                else self.unique_key("send")
            ),
            label="send_message",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=chat_id,
        )
        if replace_message_id is not None and result is not None:
            await self.delete_message(chat_id=chat_id, message_id=replace_message_id)
        return result

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        disable_notification: bool | None = False,
        caption: str | None = None,
    ) -> dict | None:
        async def execute() -> dict | None:
            if self._client_override is not None:
                return await self._client_override.send_document(
                    chat_id=chat_id,
                    filename=filename,
                    content=content,
                    reply_to_message_id=reply_to_message_id,
                    message_thread_id=message_thread_id,
                    disable_notification=disable_notification,
                    caption=caption,
                )
            params: dict[str, Any] = {"chat_id": chat_id}
            if disable_notification is not None:
                params["disable_notification"] = disable_notification
            if reply_to_message_id is not None:
                params["reply_to_message_id"] = reply_to_message_id
            if message_thread_id is not None:
                params["message_thread_id"] = message_thread_id
            if caption is not None:
                params["caption"] = caption
            result = await self._post_form(
                "sendDocument",
                params,
                files={"document": (filename, content)},
            )
            return result if isinstance(result, dict) else None

        return await self.enqueue_op(
            key=self.unique_key("send_document"),
            label="send_document",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=chat_id,
        )

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        *,
        wait: bool = True,
    ) -> dict | None:
        async def execute() -> dict | None:
            if self._client_override is not None:
                return await self._client_override.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    entities=entities,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    wait=wait,
                )
            params: dict[str, Any] = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
            }
            if entities is not None:
                params["entities"] = entities
            if parse_mode is not None:
                params["parse_mode"] = parse_mode
            if reply_markup is not None:
                params["reply_markup"] = reply_markup
            result = await self._post("editMessageText", params)
            return result if isinstance(result, dict) else None

        return await self.enqueue_op(
            key=("edit", chat_id, message_id),
            label="edit_message_text",
            execute=execute,
            priority=EDIT_PRIORITY,
            chat_id=chat_id,
            wait=wait,
        )

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> bool:
        await self.drop_pending_edits(chat_id=chat_id, message_id=message_id)

        async def execute() -> bool:
            if self._client_override is not None:
                return await self._client_override.delete_message(
                    chat_id=chat_id,
                    message_id=message_id,
                )
            result = await self._post(
                "deleteMessage",
                {"chat_id": chat_id, "message_id": message_id},
            )
            return bool(result)

        return bool(
            await self.enqueue_op(
                key=("delete", chat_id, message_id),
                label="delete_message",
                execute=execute,
                priority=DELETE_PRIORITY,
                chat_id=chat_id,
            )
        )

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool:
        async def execute() -> bool:
            if self._client_override is not None:
                return await self._client_override.set_my_commands(
                    commands,
                    scope=scope,
                    language_code=language_code,
                )
            params: dict[str, Any] = {"commands": commands}
            if scope is not None:
                params["scope"] = scope
            if language_code is not None:
                params["language_code"] = language_code
            result = await self._post("setMyCommands", params)
            return bool(result)

        return bool(
            await self.enqueue_op(
                key=self.unique_key("set_my_commands"),
                label="set_my_commands",
                execute=execute,
                priority=SEND_PRIORITY,
                chat_id=None,
            )
        )

    async def get_me(self) -> dict | None:
        async def execute() -> dict | None:
            if self._client_override is not None:
                return await self._client_override.get_me()
            result = await self._post("getMe", {})
            return result if isinstance(result, dict) else None

        return await self.enqueue_op(
            key=self.unique_key("get_me"),
            label="get_me",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=None,
        )

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool | None = None,
    ) -> bool:
        async def execute() -> bool:
            if self._client_override is not None:
                return await self._client_override.answer_callback_query(
                    callback_query_id=callback_query_id,
                    text=text,
                    show_alert=show_alert,
                )
            params: dict[str, Any] = {"callback_query_id": callback_query_id}
            if text is not None:
                params["text"] = text
            if show_alert is not None:
                params["show_alert"] = show_alert
            result = await self._post("answerCallbackQuery", params)
            return bool(result)

        return bool(
            await self.enqueue_op(
                key=self.unique_key("answer_callback_query"),
                label="answer_callback_query",
                execute=execute,
                priority=SEND_PRIORITY,
                chat_id=None,
            )
        )

    async def get_chat(self, chat_id: int) -> dict | None:
        async def execute() -> dict | None:
            if self._client_override is not None:
                return await self._client_override.get_chat(chat_id)
            result = await self._post("getChat", {"chat_id": chat_id})
            return result if isinstance(result, dict) else None

        return await self.enqueue_op(
            key=self.unique_key("get_chat"),
            label="get_chat",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=chat_id,
        )

    async def get_chat_member(self, chat_id: int, user_id: int) -> dict | None:
        async def execute() -> dict | None:
            if self._client_override is not None:
                return await self._client_override.get_chat_member(chat_id, user_id)
            result = await self._post(
                "getChatMember", {"chat_id": chat_id, "user_id": user_id}
            )
            return result if isinstance(result, dict) else None

        return await self.enqueue_op(
            key=self.unique_key("get_chat_member"),
            label="get_chat_member",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=chat_id,
        )

    async def create_forum_topic(self, chat_id: int, name: str) -> dict | None:
        async def execute() -> dict | None:
            if self._client_override is not None:
                return await self._client_override.create_forum_topic(chat_id, name)
            result = await self._post(
                "createForumTopic", {"chat_id": chat_id, "name": name}
            )
            return result if isinstance(result, dict) else None

        return await self.enqueue_op(
            key=self.unique_key("create_forum_topic"),
            label="create_forum_topic",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=chat_id,
        )

    async def edit_forum_topic(
        self, chat_id: int, message_thread_id: int, name: str
    ) -> bool:
        async def execute() -> bool:
            if self._client_override is not None:
                return await self._client_override.edit_forum_topic(
                    chat_id, message_thread_id, name
                )
            result = await self._post(
                "editForumTopic",
                {
                    "chat_id": chat_id,
                    "message_thread_id": message_thread_id,
                    "name": name,
                },
            )
            return bool(result)

        return bool(
            await self.enqueue_op(
                key=self.unique_key("edit_forum_topic"),
                label="edit_forum_topic",
                execute=execute,
                priority=SEND_PRIORITY,
                chat_id=chat_id,
            )
        )
