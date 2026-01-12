from __future__ import annotations

from typing import Any
from collections.abc import AsyncIterator, Callable, Iterable

import anyio

from ..logging import get_logger
from .api_models import Update
from .client_api import BotClient
from .types import (
    TelegramCallbackQuery,
    TelegramDocument,
    TelegramIncomingMessage,
    TelegramIncomingUpdate,
    TelegramVoice,
)

logger = get_logger(__name__)


def parse_incoming_update(
    update: Update | dict[str, Any],
    *,
    chat_id: int | None = None,
    chat_ids: set[int] | None = None,
) -> TelegramIncomingUpdate | None:
    if isinstance(update, Update):
        msg = update.message
        callback_query = update.callback_query
    else:
        msg = update.get("message")
        callback_query = update.get("callback_query")

    if isinstance(msg, dict):
        return _parse_incoming_message(msg, chat_id=chat_id, chat_ids=chat_ids)
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
            offset = upd.update_id + 1
            msg = parse_incoming_update(upd, chat_ids=allowed)
            if msg is not None:
                yield msg
