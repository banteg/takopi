from __future__ import annotations

import itertools
import time
from typing import Any
from collections.abc import Awaitable, Callable, Hashable

import anyio
import httpx

from ..logging import get_logger
from .api_models import Chat, ChatMember, File, ForumTopic, Message, Update, User
from .client_api import BotClient, HttpBotClient, TelegramRetryAfter
from .outbox import (
    DELETE_PRIORITY,
    EDIT_PRIORITY,
    SEND_PRIORITY,
    OutboxOp,
    TelegramOutbox,
)
from .parsing import parse_incoming_update, poll_incoming

logger = get_logger(__name__)

__all__ = [
    "BotClient",
    "TelegramClient",
    "TelegramRetryAfter",
    "is_group_chat_id",
    "parse_incoming_update",
    "poll_incoming",
]


def is_group_chat_id(chat_id: int) -> bool:
    return chat_id < 0


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
            self._client = client
        else:
            if token is None or not token:
                raise ValueError("Telegram token is empty")
            self._client = HttpBotClient(
                token,
                timeout_s=timeout_s,
                http_client=http_client,
            )
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
            queued_at=self._clock(),
            chat_id=chat_id,
            label=label,
        )
        return await self._outbox.enqueue(key=key, op=request, wait=wait)

    async def close(self) -> None:
        await self._outbox.close()
        await self._client.close()

    async def _call_with_retry_after(
        self,
        fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        while True:
            try:
                return await fn()
            except TelegramRetryAfter as exc:
                await self._sleep(exc.retry_after)

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[Update] | None:
        async def execute() -> list[Update] | None:
            return await self._client.get_updates(
                offset=offset,
                timeout_s=timeout_s,
                allowed_updates=allowed_updates,
            )

        return await self._call_with_retry_after(execute)

    async def get_file(self, file_id: str) -> File | None:
        async def execute() -> File | None:
            return await self._client.get_file(file_id)

        return await self._call_with_retry_after(execute)

    async def download_file(self, file_path: str) -> bytes | None:
        async def execute() -> bytes | None:
            return await self._client.download_file(file_path)

        return await self._call_with_retry_after(execute)

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
    ) -> Message | None:
        async def execute() -> Message | None:
            return await self._client.send_message(
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
    ) -> Message | None:
        async def execute() -> Message | None:
            return await self._client.send_document(
                chat_id=chat_id,
                filename=filename,
                content=content,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
                disable_notification=disable_notification,
                caption=caption,
            )

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
    ) -> Message | None:
        async def execute() -> Message | None:
            return await self._client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                entities=entities,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                wait=wait,
            )

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
            return await self._client.delete_message(
                chat_id=chat_id,
                message_id=message_id,
            )

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
            return await self._client.set_my_commands(
                commands,
                scope=scope,
                language_code=language_code,
            )

        return bool(
            await self.enqueue_op(
                key=self.unique_key("set_my_commands"),
                label="set_my_commands",
                execute=execute,
                priority=SEND_PRIORITY,
                chat_id=None,
            )
        )

    async def get_me(self) -> User | None:
        async def execute() -> User | None:
            return await self._client.get_me()

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
            return await self._client.answer_callback_query(
                callback_query_id=callback_query_id,
                text=text,
                show_alert=show_alert,
            )

        return bool(
            await self.enqueue_op(
                key=self.unique_key("answer_callback_query"),
                label="answer_callback_query",
                execute=execute,
                priority=SEND_PRIORITY,
                chat_id=None,
            )
        )

    async def get_chat(self, chat_id: int) -> Chat | None:
        async def execute() -> Chat | None:
            return await self._client.get_chat(chat_id)

        return await self.enqueue_op(
            key=self.unique_key("get_chat"),
            label="get_chat",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=chat_id,
        )

    async def get_chat_member(self, chat_id: int, user_id: int) -> ChatMember | None:
        async def execute() -> ChatMember | None:
            return await self._client.get_chat_member(chat_id, user_id)

        return await self.enqueue_op(
            key=self.unique_key("get_chat_member"),
            label="get_chat_member",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=chat_id,
        )

    async def create_forum_topic(self, chat_id: int, name: str) -> ForumTopic | None:
        async def execute() -> ForumTopic | None:
            return await self._client.create_forum_topic(chat_id, name)

        return await self.enqueue_op(
            key=self.unique_key("create_forum_topic"),
            label="create_forum_topic",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=chat_id,
        )

    async def edit_forum_topic(
        self,
        chat_id: int,
        message_thread_id: int,
        name: str,
    ) -> bool:
        async def execute() -> bool:
            return await self._client.edit_forum_topic(
                chat_id,
                message_thread_id,
                name,
            )

        return bool(
            await self.enqueue_op(
                key=self.unique_key("edit_forum_topic"),
                label="edit_forum_topic",
                execute=execute,
                priority=SEND_PRIORITY,
                chat_id=chat_id,
            )
        )
